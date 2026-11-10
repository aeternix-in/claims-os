"""Gateway-facing HCX endpoints, mounted under /hcx/v1 like the sandbox."""
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Header
from fastapi.responses import JSONResponse
from sqlalchemy import select

from .. import callbacks, db, payer
from ..config import get_settings
from ..envelope import ack, pack, response_headers, unpack
from ..errors import HcxError
from ..fhir_check import check_request_bundle
from ..envelope import now_ts
from ..workflows import COMMUNICATION, PAYMENT_NOTICE, PROVIDER_INITIATED, QUERY_WORKFLOW_ID, Workflow
import uuid
from .auth import require_bearer_auth

log = logging.getLogger("nhcx_mock.hcx")
router = APIRouter(prefix="/hcx/v1", tags=["HCX gateway"])

EXAMPLE = {
    "payload": {
        "headers": {
            "x-hcx-api_call_id": "7fe4963d-0000-0000-0000-b0c831f44e99",
            "x-hcx-correlation_id": "d961efb5-0000-0000-0000-70a166ec6b3b",
            "x-hcx-workflow_id": "1",
            "x-hcx-request_id": "2721496b-0000-0000-0000-92998f57e4fb",
            "x-hcx-timestamp": "2026-10-03T13:22:06+0530",
            "x-hcx-sender_code": "aeternix-provider@hcx",
            "x-hcx-recipient_code": "mock-payer@hcx",
            "x-hcx-status": "request.initiated",
            "x-hcx-ben-abha-id": "91-0000-0000-0000",
        },
        "fhir": {"resourceType": "Bundle", "type": "collection", "entry": ["..."]},
    }
}


async def run_payer(wf: Workflow, txn_id: int) -> None:
    with db.session() as s:
        txn = s.get(db.Transaction, txn_id)
        scenario, req_headers, req_fhir = txn.scenario, txn.request_headers, txn.request_fhir

    if scenario == "timeout":
        with db.session() as s:
            txn = s.get(db.Transaction, txn_id)
            txn.status, txn.callback_status = "payer_timeout", "not_sent"
            s.commit()
        log.info("payer timeout simulated txn=%s", txn_id)
        return

    bundle, status = payer.decide(req_fhir, scenario)
    headers = response_headers(req_headers)
    with db.session() as s:
        txn = s.get(db.Transaction, txn_id)
        txn.status, txn.response_headers, txn.response_fhir = status, headers, bundle
        s.commit()

    path = f"/hcx/v1/{wf.name}/{wf.on_action}"
    if scenario == "callback_fail":
        path = "/__unreachable__" + path  # real HTTP failure -> exercises retry path
    delivered = await callbacks.deliver(path, pack(headers, bundle, callback=True), txn_id)
    if scenario == "query" and delivered:
        await send_query(wf, txn_id)


async def send_query(wf: Workflow, txn_id: int) -> None:
    """After a 'Queried' decision the insurer sends the document query itself (NHA guide ch.8)."""
    with db.session() as s:
        txn = s.get(db.Transaction, txn_id)
        rh, req_fhir, corr = txn.request_headers, txn.request_fhir, txn.correlation_id
    bundle = payer.communication_request(req_fhir, wf.name)
    headers = {**rh, "x-hcx-api_call_id": str(uuid.uuid4()), "x-hcx-request_id": str(uuid.uuid4()),
               "x-hcx-timestamp": now_ts(), "x-hcx-status": "request.initiated",
               "x-hcx-workflow_id": QUERY_WORKFLOW_ID[wf.name],
               "x-hcx-sender_code": rh["x-hcx-recipient_code"], "x-hcx-recipient_code": rh["x-hcx-sender_code"]}
    with db.session() as s:
        q = db.Transaction(api_call_id=headers["x-hcx-api_call_id"], correlation_id=corr,
                           workflow_id=headers["x-hcx-workflow_id"], request_id=headers["x-hcx-request_id"],
                           workflow=COMMUNICATION.name, action=COMMUNICATION.action,
                           sender_code=headers["x-hcx-sender_code"], recipient_code=headers["x-hcx-recipient_code"],
                           scenario=f"query:{wf.name}:{txn_id}", status="query_sent",
                           request_headers=headers, request_fhir=bundle)
        s.add(q)
        s.commit()
        qid = q.id
    await callbacks.deliver("/hcx/v1/communication/request", pack(headers, bundle, callback=False), qid)


def make_action_handler(wf: Workflow):
    async def handler(
        background: BackgroundTasks,
        body: dict[str, Any] = Body(..., examples=[EXAMPLE]),
        _token: str = Depends(require_bearer_auth),
        x_mock_scenario: str | None = Header(default=None, description=f"Test-only: {sorted(payer.SCENARIOS)}"),
    ):
        headers, fhir = unpack(body, expected_status="request.initiated")
        check_request_bundle(fhir, wf)

        scenario = x_mock_scenario or get_settings().default_scenario
        if scenario not in payer.SCENARIOS:
            raise HcxError(400, "ERR_MOCK_SCENARIO", f"Unknown x-mock-scenario {scenario!r}")

        with db.session() as s:
            if db.get_by_api_call_id(s, headers["x-hcx-api_call_id"]):
                raise HcxError(400, "ERR_DUPLICATE_API_CALL_ID", "x-hcx-api_call_id already used")
            txn = db.Transaction(
                api_call_id=headers["x-hcx-api_call_id"],
                correlation_id=headers["x-hcx-correlation_id"],
                workflow_id=headers["x-hcx-workflow_id"],
                request_id=headers["x-hcx-request_id"],
                workflow=wf.name, action=wf.action,
                sender_code=headers["x-hcx-sender_code"],
                recipient_code=headers["x-hcx-recipient_code"],
                scenario=scenario, status="submitted",
                request_headers=headers, request_fhir=fhir,
            )
            s.add(txn)
            s.commit()
            txn_id = txn.id

        log.info("accepted %s/%s txn=%s correlation=%s scenario=%s",
                 wf.name, wf.action, txn_id, headers["x-hcx-correlation_id"], scenario)
        background.add_task(run_payer, wf, txn_id)
        return JSONResponse(status_code=202, content=ack(headers))

    handler.__name__ = f"{wf.name}_{wf.action}"
    return handler


for _wf in PROVIDER_INITIATED.values():
    router.add_api_route(
        f"/{_wf.name}/{_wf.action}", make_action_handler(_wf), methods=["POST"], status_code=202,
        summary=f"{_wf.name}/{_wf.action} (provider -> payer; result arrives on /{_wf.name}/{_wf.on_action})",
    )


@router.post("/paymentnotice/on_request", status_code=202,
             summary="Provider acknowledges a PaymentNotice sent by the mock payer")
async def paymentnotice_on_request(
    body: dict[str, Any] = Body(...),
    _token: str = Depends(require_bearer_auth),
):
    headers, _fhir = unpack(body, expected_status="response.complete")
    with db.session() as s:
        txn = s.scalar(select(db.Transaction).where(
            db.Transaction.workflow == PAYMENT_NOTICE.name,
            db.Transaction.correlation_id == headers["x-hcx-correlation_id"],
        ))
        if txn is None:
            raise HcxError(400, "ERR_INVALID_CORRELATION_ID", "No payment notice for this correlation_id")
        txn.status = "acknowledged"
        s.commit()
    return JSONResponse(status_code=202, content=ack(headers))


@router.post("/communication/on_request", status_code=202,
             summary="Provider answers the insurer's document query; the insurer then sends its final decision")
async def communication_on_request(
    background: BackgroundTasks,
    body: dict[str, Any] = Body(...),
    _token: str = Depends(require_bearer_auth),
):
    headers, fhir = unpack(body, expected_status="response.complete")
    if not any((e.get("resource") or {}).get("resourceType") == "Communication" for e in fhir.get("entry") or []):
        raise HcxError(400, "ERR_INVALID_DOMAIN_PAYLOAD", "communication/on_request needs a Communication resource")
    with db.session() as s:
        q = s.scalar(select(db.Transaction).where(
            db.Transaction.workflow == COMMUNICATION.name,
            db.Transaction.correlation_id == headers["x-hcx-correlation_id"],
            db.Transaction.status == "query_sent").order_by(db.Transaction.id.desc()))
        if q is None:
            raise HcxError(400, "ERR_INVALID_CORRELATION_ID", "No open query for this correlation_id")
        q.status, q.response_headers, q.response_fhir = "answered", headers, fhir
        _, wf_name, orig_id = q.scenario.split(":")
        orig = s.get(db.Transaction, int(orig_id))
        orig.scenario = "approve"            # the reply satisfies the insurer in the mock
        s.commit()
    background.add_task(run_payer, PROVIDER_INITIATED[wf_name], int(orig_id))
    return JSONResponse(status_code=202, content=ack(headers))