"""Business operations: create cases, submit to NHCX, handle what NHCX sends back.

Ordering rule (important): a case is moved to *_submitted and the outbound exchange is
saved BEFORE the HTTP call. The payer's callback can arrive while our send is still in
flight (it does with the in-process mock, and can on a real network), so the callback
must find the exchange already recorded. After the call we only record the ack or the
failure - we never move a case back to "submitted".
"""
import logging
import uuid

import httpx

from . import store
from .domain import lifecycle
from .domain.lifecycle import CaseStatus
from .domain.models import ClaimCase, ClaimType
from .nhcx import client
from .nhcx import codes
from .nhcx.envelope import ENDPOINT, build_request, hcx_timestamp, wrap
from .nhcx.fhir_claim import to_claim_bundle
from .nhcx.inbound import InboundError, parse, reply_headers
from .nhcx.responses import UnreadableResponse, interpret

log = logging.getLogger("aeternix.services")


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


def _transition(s, case: store.CaseRow, target: CaseStatus, note: str | None = None) -> None:
    lifecycle.check(case.status, target)
    s.add(store.EventRow(case_id=case.case_id, from_status=case.status, to_status=target, note=note))
    case.status = target


def create_case(case: ClaimCase) -> store.CaseRow:
    with store.session() as s:
        if s.get(store.CaseRow, case.case_id):
            raise Conflict(f"case {case.case_id} already exists")
        row = store.CaseRow(case_id=case.case_id, status=CaseStatus.DRAFT, case_number=case.case_number,
                            data=case.model_dump(mode="json"))
        s.add(row)
        s.add(store.EventRow(case_id=case.case_id, from_status=None, to_status=CaseStatus.DRAFT, note="created"))
        s.commit()
        return row


def _summary(row: store.CaseRow) -> dict:
    d = row.data
    total = sum(float(i["unit_price"]) * i.get("quantity", 1) for i in d.get("line_items", []))
    return {"case_id": row.case_id, "status": row.status, "patient": d["patient"]["name"],
            "insurer": d["insurer"]["name"], "total": total, "currency": d.get("currency", "INR"),
            "admission_at": d.get("admission_at"), "updated_at": row.updated_at}


def next_actions(status: str) -> list[str]:
    """Which NHCX submissions the case allows right now ("preauth" / "claim")."""
    allowed = lifecycle.TRANSITIONS[CaseStatus(status)]
    return [w for w in ("preauth", "claim") if lifecycle.submitted(w) in allowed]


def list_cases(limit: int = 200) -> list[dict]:
    with store.session() as s:
        rows = s.query(store.CaseRow).order_by(store.CaseRow.updated_at.desc()).limit(limit).all()
        return [_summary(r) for r in rows]


def get_case(case_id: str) -> dict:
    with store.session() as s:
        row = s.get(store.CaseRow, case_id)
        if not row:
            raise NotFound(case_id)
        events = s.query(store.EventRow).filter_by(case_id=case_id).order_by(store.EventRow.id).all()
        exchanges = s.query(store.ExchangeRow).filter_by(case_id=case_id).order_by(store.ExchangeRow.id).all()
        return {
            **_summary(row), "data": row.data, "actions": next_actions(row.status),
            "case_number": row.case_number, "open_query": row.open_query,
            "last_decision": row.last_decision,
            "history": [{"from": e.from_status, "to": e.to_status, "note": e.note, "at": e.at} for e in events],
            "exchanges": [x.summary() for x in exchanges],
        }


async def submit(case_id: str, workflow: str, scenario: str | None = None) -> dict:
    """Send a pre-auth or claim for a case. Returns the outbound exchange summary."""
    if workflow not in ENDPOINT:
        raise ValueError(f"unknown workflow {workflow!r}")

    # 1. Build the message and record it BEFORE sending (see module docstring).
    with store.session() as s:
        row = s.get(store.CaseRow, case_id)
        if not row:
            raise NotFound(case_id)
        lifecycle.check(row.status, lifecycle.submitted(workflow))
        case = ClaimCase.model_validate(row.data)
        case.claim_type = ClaimType(workflow)
        bundle = to_claim_bundle(case, submission_id=str(uuid.uuid4()))
        # NHA guide 7.2: replacing a queried pre-auth is a resubmission (121); otherwise new (12 / 15)
        workflow_id = codes.WORKFLOW_PREAUTH_RESUBMIT if row.status == CaseStatus.PREAUTH_QUERY else None
        body, conv = build_request(case, bundle, workflow_id=workflow_id)
        if workflow_id:
            row.open_query = None
        api_call_id = conv.last_api_call_id
        exchange = store.ExchangeRow(case_id=case_id, workflow=workflow, direction="outbound", action="submit",
                                     api_call_id=api_call_id, correlation_id=conv.correlation_id,
                                     status="sending", body=body,
                                     plain={"headers": conv.last_headers, "fhir": bundle})
        s.add(exchange)
        row.data = case.model_dump(mode="json")
        _transition(s, row, lifecycle.submitted(workflow), note=f"api_call_id={api_call_id}")
        s.commit()
        exchange_id = exchange.id

    # 2. Send.
    error, http_status, response = None, None, None
    try:
        extra = {"x-mock-scenario": scenario} if scenario else None   # demo mode only (see api)
        r = await client.post(ENDPOINT[workflow], body, extra_headers=extra)
        http_status = r.status_code
        try:
            response = r.json()
        except ValueError:
            response = {"raw": r.text[:2000]}
        if r.status_code != 202:
            err = (response or {}).get("error") or {}
            error = f"{err.get('code', 'HTTP_' + str(r.status_code))}: {err.get('message', r.text[:300])}"
    except httpx.HTTPError as e:
        error = f"network error: {type(e).__name__}: {e}"

    # 3. Record the outcome of the send.
    with store.session() as s:
        exchange = s.get(store.ExchangeRow, exchange_id)
        exchange.http_status, exchange.response, exchange.error = http_status, response, error
        exchange.status = "failed" if error else "acked"
        row = s.get(store.CaseRow, case_id)
        if error and row.status == lifecycle.submitted(workflow):
            _transition(s, row, lifecycle.failed(workflow), note=error)
        s.commit()
        if error:
            log.warning("submit failed case=%s workflow=%s error=%s", case_id, workflow, error)
        return exchange.summary()


def _record_inbound(s, case_id: str, workflow: str, action: str, headers: dict, body: dict,
                    fhir: dict | None = None) -> None:
    s.add(store.ExchangeRow(case_id=case_id, workflow=workflow, direction="inbound", action=action,
                            api_call_id=headers["x-hcx-api_call_id"],
                            correlation_id=headers["x-hcx-correlation_id"], status="received", body=body,
                            plain={"headers": headers, "fhir": fhir}))


def handle_claim_response(workflow: str, body: dict) -> tuple[dict, bool]:
    """Process preauth/claim on_submit. Returns (headers, is_duplicate)."""
    headers, fhir = parse(body, expected_status="response.complete")
    with store.session() as s:
        if store.exchange_by_api_call_id(s, headers["x-hcx-api_call_id"]):
            return headers, True  # already processed: acknowledge, do nothing (idempotency)
        original = store.outbound_by_correlation(s, headers["x-hcx-correlation_id"], workflow)
        if not original:
            raise InboundError(400, "ERR_INVALID_CORRELATION_ID", f"no {workflow} request with this correlation_id")
        row = s.get(store.CaseRow, original.case_id)
        if headers["x-hcx-recipient_code"] != row.data["hospital"]["nhcx_participant_code"]:
            raise InboundError(400, "ERR_INVALID_SENDER_OR_RECIPIENT", "recipient is not this hospital")
        try:
            decision = interpret(fhir)
        except UnreadableResponse as e:
            raise InboundError(400, "ERR_INVALID_DOMAIN_PAYLOAD", str(e)) from e

        _record_inbound(s, row.case_id, workflow, "on_submit", headers, body, fhir)
        if decision.decision == "submitted":          # outcome "queued": still in process, no state change
            s.commit()
            return headers, False
        target = lifecycle.decided(workflow, decision.decision)
        if row.status != target:                      # a query may already have arrived via communication
            try:
                _transition(s, row, target, note=decision.disposition)
            except lifecycle.InvalidTransition as e:
                raise InboundError(400, "ERR_INVALID_STATE", str(e)) from e
        row.last_decision = decision.as_dict()
        s.commit()
        log.info("case=%s %s -> %s", row.case_id, workflow, target)
        return headers, False


def _resources(fhir: dict, rtype: str) -> list[dict]:
    return [e["resource"] for e in fhir.get("entry", [])
            if (e.get("resource") or {}).get("resourceType") == rtype]


def _ident(resource: dict, code: str) -> str | None:
    for i in resource.get("identifier") or []:
        if any(c.get("code") == code for c in (i.get("type") or {}).get("coding", [])):
            return i.get("value")
    return None


def handle_communication_request(body: dict) -> tuple[dict, bool]:
    """Insurer asks for more information (NHA guide ch.8, workflow 24 pre-auth / 27 claim).
    Stores the query on the case and moves it to *_query. Returns (headers, is_duplicate)."""
    headers, fhir = parse(body, expected_status="request.initiated")
    with store.session() as s:
        if store.exchange_by_api_call_id(s, headers["x-hcx-api_call_id"]):
            return headers, True
        reqs = _resources(fhir, "CommunicationRequest")
        cln = _ident(reqs[0], "CLN") if reqs else None
        if not cln:
            raise InboundError(400, "PAYR-1037", "CommunicationRequest with identifier type CLN is required")
        row = s.query(store.CaseRow).filter_by(case_number=cln).first()
        if not row:
            raise InboundError(400, "ERR_UNKNOWN_CASE", f"no case with case number {cln!r}")
        workflow = {"24": "preauth", "27": "claim"}.get(headers["x-hcx-workflow_id"]) \
            or ("claim" if row.status.startswith("claim") else "preauth")
        remarks = " ".join(p.get("contentString", "") for p in reqs[0].get("payload", []) if p.get("contentString"))
        _record_inbound(s, row.case_id, "communication", "request", headers, body, fhir)
        row.open_query = {"workflow": workflow, "remarks": remarks or "The insurer requested more information.",
                          "headers": headers, "request_id": reqs[0].get("id"), "received_at": hcx_timestamp()}
        target = lifecycle.decided(workflow, "query")
        if row.status != target:
            try:
                _transition(s, row, target, note=f"Insurer query: {remarks}")
            except lifecycle.InvalidTransition as e:
                raise InboundError(400, "ERR_INVALID_STATE", str(e)) from e
        s.commit()
        return headers, False


async def reply_to_query(case_id: str, message: str, attachments: list[dict] | None = None) -> dict:
    """Answer the insurer's document query via communication/on_request (workflow 19 / 161)."""
    with store.session() as s:
        row = s.get(store.CaseRow, case_id)
        if not row:
            raise NotFound(case_id)
        q = row.open_query
        if not q:
            raise Conflict("this case has no open insurer query")
        case = ClaimCase.model_validate(row.data)
        comm_id, org_id = str(uuid.uuid4()), str(uuid.uuid4())
        payload = [{"contentString": message}] + [
            {"contentAttachment": {"contentType": a["content_type"], "data": a["data"], "title": a.get("title")}}
            for a in (attachments or [])]
        communication = {
            "resourceType": "Communication", "id": comm_id, "status": "completed",
            "identifier": [{"type": {"coding": [codes.ID_CASE_NUMBER["type"]]}, "value": row.case_number}],
            "sent": hcx_timestamp(),
            "payload": payload,
            **({"basedOn": [{"reference": f"CommunicationRequest/{q['request_id']}"}]} if q.get("request_id") else {}),
        }
        provider = {"resourceType": "Organization", "id": org_id, "name": case.hospital.name,
                    "identifier": [{"type": {"coding": [codes.ID_PROVIDER_HFR["type"]]},
                                    "system": codes.ID_PROVIDER_HFR["system"], "value": case.hospital.hfr_id}]}
        fhir = {"resourceType": "Bundle", "id": str(uuid.uuid4()), "type": "collection",
                "timestamp": hcx_timestamp(),
                "entry": [{"fullUrl": f"urn:uuid:{r['id']}", "resource": r} for r in (communication, provider)]}
        headers = {**reply_headers(q["headers"]), "x-hcx-workflow_id": codes.WORKFLOW_QUERY_REPLY[q["workflow"]]}
        body = wrap(headers, fhir, callback=True)
        x = store.ExchangeRow(case_id=case_id, workflow="communication", direction="outbound", action="on_request",
                              api_call_id=headers["x-hcx-api_call_id"], correlation_id=headers["x-hcx-correlation_id"],
                              status="sending", body=body, plain={"headers": headers, "fhir": fhir})
        s.add(x)
        # Same ordering rule as submit(): move to *_query_answered BEFORE sending, because the
        # insurer's final decision can arrive while this send is still in flight.
        _transition(s, row, CaseStatus(f"{q['workflow']}_query_answered"), note=f"Replied to insurer: {message[:120]}")
        row.open_query = None
        s.commit()
        exchange_id, workflow, saved_query = x.id, q["workflow"], q

    error, http_status = None, None
    try:
        r = await client.post("/hcx/v1/communication/on_request", body)
        http_status = r.status_code
        if r.status_code != 202:
            err = (r.json() if r.headers.get("content-type", "").startswith("application/json") else {}).get("error", {})
            error = f"{err.get('code', 'HTTP_' + str(r.status_code))}: {err.get('message', r.text[:300])}"
    except httpx.HTTPError as e:
        error = f"network error: {type(e).__name__}: {e}"

    with store.session() as s:
        x = s.get(store.ExchangeRow, exchange_id)
        x.status, x.http_status, x.error = ("failed" if error else "acked"), http_status, error
        row = s.get(store.CaseRow, case_id)
        if error and row.status == CaseStatus(f"{workflow}_query_answered"):
            # the reply never reached NHCX: reopen the query so staff can try again
            _transition(s, row, lifecycle.decided(workflow, "query"), note=f"Reply not delivered: {error}")
            row.open_query = saved_query
        s.commit()
        return x.summary()


def handle_payment_notice(body: dict) -> tuple[dict, dict | None]:
    """Process a payer-initiated paymentnotice/request.

    Returns (received headers, (ack body, ack api_call_id) to send via paymentnotice/on_request,
    or None if this notice was already processed).
    """
    headers, fhir = parse(body, expected_status="request.initiated")
    with store.session() as s:
        if store.exchange_by_api_call_id(s, headers["x-hcx-api_call_id"]):
            return headers, None
        claim = store.outbound_by_correlation(s, headers["x-hcx-correlation_id"], "claim")
        if not claim:
            raise InboundError(400, "ERR_INVALID_CORRELATION_ID", "no claim with this correlation_id")
        row = s.get(store.CaseRow, claim.case_id)
        notices = _resources(fhir, "PaymentNotice")
        if not notices:
            raise InboundError(400, "ERR_INVALID_DOMAIN_PAYLOAD", "bundle has no PaymentNotice")
        notice = notices[0]
        recs = _resources(fhir, "PaymentReconciliation")
        utr = _ident(recs[0], "UTR") if recs else None
        _record_inbound(s, row.case_id, "paymentnotice", "request", headers, body, fhir)
        amount = notice.get("amount") or {}
        try:
            _transition(s, row, CaseStatus.PAID,
                        note=f"paid {amount.get('value')} {amount.get('currency')}" + (f", UTR {utr}" if utr else ""))
        except lifecycle.InvalidTransition as e:
            raise InboundError(400, "ERR_INVALID_STATE", str(e)) from e

        # NHA guide checkpoint 13: the hospital acknowledges with a Task, output claimNumber = case id,
        # description = remarks. ASSUMPTION: code system of the output type.
        task = {"resourceType": "Task", "id": str(uuid.uuid4()), "status": "completed", "intent": "order",
                "description": "Payment notice received" + (f" (UTR {utr})" if utr else ""),
                "output": [{"type": {"coding": [{"system": codes.NDHM_ID_TYPE, "code": "claimNumber"}]},
                            "valueString": row.case_number}]}
        ack_fhir = {"resourceType": "Bundle", "id": str(uuid.uuid4()), "type": "collection",
                    "timestamp": hcx_timestamp(), "entry": [{"fullUrl": f"urn:uuid:{task['id']}", "resource": task}]}
        ack_headers = reply_headers(headers)
        ack_body = wrap(ack_headers, ack_fhir, callback=True)
        fhir = ack_fhir
        s.add(store.ExchangeRow(case_id=row.case_id, workflow="paymentnotice", direction="outbound",
                                action="on_request", api_call_id=ack_headers["x-hcx-api_call_id"],
                                correlation_id=headers["x-hcx-correlation_id"], status="sending", body=ack_body,
                                plain={"headers": ack_headers, "fhir": fhir}))
        s.commit()
        return headers, (ack_body, ack_headers["x-hcx-api_call_id"])


def get_exchange(case_id: str, exchange_id: int) -> dict:
    """Full detail of one message: wire form, readable form, and how we interpreted it."""
    with store.session() as s:
        x = s.get(store.ExchangeRow, exchange_id)
        if not x or x.case_id != case_id:
            raise NotFound(f"{case_id}/messages/{exchange_id}")
        detail = {**x.summary(), "wire": x.body, "plain": x.plain, "sync_response": x.response,
                  "encrypted": isinstance((x.body or {}).get("payload"), str), "interpretation": None}
        fhir = (x.plain or {}).get("fhir") or {}
        if any((e.get("resource") or {}).get("resourceType") == "ClaimResponse" for e in fhir.get("entry", [])):
            try:
                detail["interpretation"] = interpret(fhir).as_dict()
            except UnreadableResponse as e:
                detail["interpretation"] = {"error": str(e)}
        return detail


async def demo_pay(case_id: str) -> dict:
    """Demo only: ask nhcx-mock's insurer to send a payment notice for the case's claim."""
    with store.session() as s:
        claim = s.query(store.ExchangeRow).filter_by(case_id=case_id, workflow="claim", direction="outbound") \
            .order_by(store.ExchangeRow.id.desc()).first()
        if not claim:
            raise NotFound(case_id)
        corr = claim.correlation_id
    r = await client.post(f"/mock/paymentnotice/{corr}")
    return {"http_status": r.status_code, "detail": r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text}


async def send_payment_ack(ack_body: dict, api_call_id: str) -> None:
    error, http_status = None, None
    try:
        r = await client.post("/hcx/v1/paymentnotice/on_request", ack_body)
        http_status = r.status_code
        if r.status_code != 202:
            error = r.text[:500]
    except httpx.HTTPError as e:
        error = f"network error: {e}"
    with store.session() as s:
        x = store.exchange_by_api_call_id(s, api_call_id)
        x.status, x.http_status, x.error = ("failed" if error else "acked"), http_status, error
        s.commit()
