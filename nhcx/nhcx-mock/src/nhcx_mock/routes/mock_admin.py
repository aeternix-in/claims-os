"""Mock-only helpers: inspect state, fake token, trigger payer-initiated flows."""
import uuid

from fastapi import APIRouter, BackgroundTasks
from sqlalchemy import select

from .. import callbacks, db, payer
from ..envelope import now_ts, pack
from ..errors import HcxError
from ..workflows import PAYMENT_NOTICE

router = APIRouter(prefix="/mock", tags=["Mock controls"])


@router.post("/token", summary="Fake token endpoint (any credentials accepted)")
def token():
    # ASSUMPTION: real token comes from ABDM's Keycloak (central-registry). Shape is illustrative.
    return {"accessToken": f"mock-{uuid.uuid4()}", "expiresIn": 1200, "tokenType": "bearer"}


@router.get("/transactions")
def list_transactions(correlation_id: str | None = None, limit: int = 50):
    with db.session() as s:
        q = select(db.Transaction).order_by(db.Transaction.id.desc()).limit(limit)
        if correlation_id:
            q = q.where(db.Transaction.correlation_id == correlation_id)
        return [t.summary() for t in s.scalars(q)]


@router.get("/transactions/{api_call_id}")
def get_transaction(api_call_id: str):
    with db.session() as s:
        txn = db.get_by_api_call_id(s, api_call_id)
        if not txn:
            raise HcxError(404, "ERR_NOT_FOUND", "Unknown api_call_id")
        return txn.summary()


@router.post("/paymentnotice/{correlation_id}",
             summary="Make the mock payer send a PaymentNotice for an approved claim")
async def trigger_payment_notice(correlation_id: str, background: BackgroundTasks):
    with db.session() as s:
        claim_txn = s.scalar(select(db.Transaction).where(
            db.Transaction.workflow == "claim",
            db.Transaction.correlation_id == correlation_id,
            db.Transaction.status.in_(["approved", "partially_approved"]),
        ))
        if not claim_txn:
            raise HcxError(400, "ERR_INVALID_CORRELATION_ID", "No approved claim with this correlation_id")

        bundle = payer.payment_notice(claim_txn.response_fhir)
        rh = claim_txn.request_headers
        headers = {
            **rh,
            "x-hcx-api_call_id": str(uuid.uuid4()),
            "x-hcx-request_id": str(uuid.uuid4()),
            "x-hcx-timestamp": now_ts(),
            "x-hcx-status": "request.initiated",
            "x-hcx-sender_code": rh["x-hcx-recipient_code"],   # payer
            "x-hcx-recipient_code": rh["x-hcx-sender_code"],   # provider
        }
        txn = db.Transaction(
            api_call_id=headers["x-hcx-api_call_id"], correlation_id=correlation_id,
            workflow_id=headers["x-hcx-workflow_id"], request_id=headers["x-hcx-request_id"],
            workflow=PAYMENT_NOTICE.name, action=PAYMENT_NOTICE.action,
            sender_code=headers["x-hcx-sender_code"], recipient_code=headers["x-hcx-recipient_code"],
            scenario="payment", status="notice_sent",
            request_headers=headers, request_fhir=bundle,
        )
        s.add(txn)
        s.commit()
        txn_id = txn.id

    # Payer-initiated action -> body without "type", like the sandbox's action calls.
    background.add_task(callbacks.deliver, "/hcx/v1/paymentnotice/request",
                        pack(headers, bundle, callback=False), txn_id)
    return {"api_call_id": headers["x-hcx-api_call_id"], "correlation_id": correlation_id}
