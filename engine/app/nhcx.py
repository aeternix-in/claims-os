"""NHCX channel. The protocol is aeternix-core's (FHIR ClaimBundle per the NHA guide, envelope,
JWE, sandbox session, inbound parsing, decision reading); this module connects it to our claims:

  our case ─► ClaimCase (core model) ─► ClaimBundle ─► envelope ─► NHCX
     ▲                                                              │
     └── apply(event) ◄── decision / query / payment notice ◄───────┘

Rules kept from aeternix-core:
- an outbound message is saved (and the case moved) BEFORE the HTTP call, because the insurer's
  callback can arrive while the send is still in flight;
- duplicate callbacks (same api_call_id) are acknowledged and ignored;
- callbacks for unknown conversations, or addressed to another hospital, are refused;
- a failed send leaves the case where it is, marked failed, and can be resent.
Our rule on top: a payment notice records the expected UTR and amount, but only a matched bank
credit closes a claim.
"""
from __future__ import annotations

import base64
import logging
import math
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import yaml
from fastapi import APIRouter, BackgroundTasks, Body, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ValidationError

from aeternix_core.config import get_settings as core_settings
from aeternix_core.domain import models as M
from aeternix_core.nhcx import client, codes
from aeternix_core.nhcx.envelope import ENDPOINT, build_request, hcx_timestamp, wrap
from aeternix_core.nhcx.fhir_claim import to_claim_bundle
from aeternix_core.nhcx.inbound import InboundError, ack, parse, reply_headers
from aeternix_core.nhcx.responses import UnreadableResponse, interpret

from . import storage
from .auth import User, current_user
from .config import get_settings
from .db import tx

log = logging.getLogger("aeternix.nhcx")
router = APIRouter(tags=["nhcx"])
gateway = APIRouter(prefix="/hcx/v1", tags=["NHCX callbacks (called by the gateway)"])
IST = timezone(timedelta(hours=5, minutes=30))
RULES = Path(__file__).resolve().parent.parent / "rules" / "nhcx_codes.yaml"
SUBMIT_EVENT = {"preauth": "submit_preauth", "claim": "submit_claim"}
DECISION_EVENT = {   # (workflow, decision) -> our event
    ("preauth", "approved"): "preauth_approved", ("preauth", "partially_approved"): "preauth_approved",
    ("preauth", "rejected"): "preauth_denied", ("preauth", "query"): "preauth_query",
    ("claim", "approved"): "settlement_letter", ("claim", "partially_approved"): "settlement_letter",
    ("claim", "rejected"): "claim_rejected", ("claim", "query"): "claim_query",
}
MAX_ATTACH_BYTES = 5 * 1024 * 1024


def enabled() -> bool:
    return get_settings().nhcx_enabled


def demo_mode() -> bool:
    return enabled() and core_settings().demo_mode


@lru_cache
def code_rules() -> dict:
    return yaml.safe_load(RULES.read_text())


def _system(hospital_id: str) -> User:
    return User(id=None, hospital_id=str(hospital_id), role="system", full_name="NHCX")


# ---------------- our case -> ClaimCase ----------------
LABEL = {   # pydantic location -> words the desk understands
    "patient.birth_date": "Patient date of birth", "patient.gender": "Patient sex", "patient.name": "Patient name",
    "hospital.hfr_id": "Hospital HFR ID (Setup)", "hospital.nhcx_participant_code": "Hospital NHCX participant code (Setup)",
    "insurer.nhcx_participant_code": "Payer's NHCX participant code (Payers)", "insurer.processing_code": "Payer's NHCX processing code (Payers)",
    "doctor.registration_number": "Treating doctor's HPR ID", "doctor.name": "Treating doctor's name",
    "policy.product_code": "Policy product code", "policy.member_id": "Policy member ID", "policy.policy_number": "Policy number",
    "diagnoses": "Diagnosis with ICD-10 code", "line_items": "Bill lines", "discharge_at": "Discharge date",
}


def _plain(e: ValidationError) -> list[str]:
    out = []
    for err in e.errors():
        loc = [str(x) for x in err["loc"]]
        key = ".".join(x for x in loc if not x.isdigit())
        label = LABEL.get(key) or LABEL.get(loc[0]) or key.replace("_", " ").replace(".", " › ")
        idx = next((int(x) for x in loc if x.isdigit()), None)
        if idx is not None:
            label += f" #{idx + 1}"
        msg = "missing" if err.get("input") is None or err["type"] == "missing" else err["msg"].removeprefix("Value error, ")
        if err["type"] == "too_short" and key in ("diagnoses", "line_items"):
            msg = "missing"
        out.append(f"{label}: {msg}")
    return out


def build_claim_case(c, case_id: str, hospital_id: str, workflow: str) -> tuple[M.ClaimCase | None, list[str]]:
    r = c.execute("""
        select cs.*, pt.full_name, pt.sex, pt.dob, pt.phone, pt.abha_id, pt.uhid,
               p.policy_number, p.product_code, p.member_id, p.relationship, p.valid_from, p.valid_to,
               py.name as payer_name, py.nhcx_participant_code as payer_code, py.nhcx_processing_code as processing_code,
               h.name as hospital_name, h.hfr_id, h.nhcx_participant_code as hospital_code
        from cases cs join patients pt on pt.id=cs.patient_id left join policies p on p.id=cs.policy_id
        left join payers py on py.id=cs.payer_id join hospitals h on h.id=cs.hospital_id
        where cs.id=%s and cs.hospital_id=%s""", (case_id, hospital_id)).fetchone()
    if not r:
        raise HTTPException(404, "Case not found")
    problems: list[str] = []
    rules = code_rules()
    lines = c.execute("select * from bill_lines where case_id=%s order by position", (case_id,)).fetchall()
    items = []
    for i, l in enumerate(lines, 1):
        if not l["to_payer"] or l["category"] == "nonpayable":
            continue
        snomed = l["snomed_code"] or ((rules["categories"].get(l["category"]) or {}).get("code"))
        if not snomed:
            problems.append(f"Bill line {i} ({l['description']}): SNOMED code needed for NHCX (Bill tab)")
            continue
        qty, rate = float(l["qty"]), float(l["rate"])
        if l["mou_rate"] is not None:
            rate = min(rate, float(l["mou_rate"]))
        if qty != math.floor(qty) or qty < 1:   # NHCX quantities are whole units: bill the total as one unit
            rate, qty = round(rate * qty, 2), 1
        items.append({"snomed_code": snomed, "description": l["description"], "unit_price": Decimal(str(round(rate, 2))),
                      "quantity": int(qty)})
    if not lines:
        problems.append("Bill lines: add the estimate or bill on the Bill tab (NHCX needs itemised lines)")
    procs = [{"snomed_code": p["code"], "description": p["display"]} for p in (rules["procedures"].get(r["procedure_id"] or "") or [])]
    admitted = r["admitted_at"]
    discharge = r["discharged_at"] or (admitted + timedelta(days=3))
    gender = {"M": "male", "F": "female", "O": "other"}.get(r["sex"] or "", None)
    data: dict[str, Any] = {
        "case_id": str(r["id"]), "claim_type": workflow, "created_at": datetime.now(IST),
        "admission_at": admitted, "discharge_at": discharge, "hospital_reference": r["case_number"],
        "patient": {"name": r["full_name"], "hospital_patient_id": r["uhid"], "gender": gender, "birth_date": r["dob"],
                    "phone": r["phone"], "abha_number": r["abha_id"]},
        "hospital": {"name": r["hospital_name"], "nhcx_participant_code": r["hospital_code"], "hfr_id": r["hfr_id"]},
        "insurer": {"name": r["payer_name"] or "", "nhcx_participant_code": r["payer_code"], "processing_code": r["processing_code"] or None},
        "doctor": {"name": r["treating_doctor"], "registration_number": r["doctor_hpr_id"]},
        "policy": {"product_code": r["product_code"], "policy_number": r["policy_number"], "member_id": r["member_id"],
                   "relationship": r["relationship"] or "self", "valid_from": r["valid_from"], "valid_to": r["valid_to"]},
        "diagnoses": [{"icd10_code": r["icd10"], "description": r["provisional_dx"] or "Diagnosis"}] if r["icd10"] else [],
        "procedures": procs, "line_items": items,
    }
    try:
        case = M.ClaimCase.model_validate(data)
    except ValidationError as e:
        problems += [p for p in _plain(e) if not (p.startswith("Bill lines") and any(x.startswith("Bill line") for x in problems))]
        return None, problems
    return (None, problems) if problems else (case, [])


# ---------------- exchange log ----------------
def _log(c, hospital_id, case_id, workflow, action, direction, headers, wire, plain_fhir, status, **kw) -> int:
    return c.execute("""insert into nhcx_exchanges (hospital_id, case_id, workflow, action, direction, api_call_id, correlation_id,
                        workflow_id, status, wire, plain, http_status, error, sync_response)
                        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                     (hospital_id, case_id, workflow, action, direction, headers["x-hcx-api_call_id"], headers["x-hcx-correlation_id"],
                      headers.get("x-hcx-workflow_id"), status, Jsonb(wire), Jsonb({"headers": headers, "fhir": plain_fhir}, dumps=_dumps),
                      kw.get("http_status"), kw.get("error"), Jsonb(kw.get("sync_response")))).fetchone()["id"]


def _dumps(o):
    import json
    return json.dumps(o, default=str)


async def _post(path: str, body: dict, scenario: str | None = None) -> tuple[int | None, dict | None, str | None]:
    try:
        r = await client.post(path, body, extra_headers={"x-mock-scenario": scenario} if scenario and demo_mode() else None)
        try:
            resp = r.json()
        except ValueError:
            resp = {"raw": r.text[:2000]}
        if r.status_code != 202:
            err = (resp or {}).get("error") or {}
            return r.status_code, resp, f"{err.get('code', 'HTTP_' + str(r.status_code))}: {err.get('message', r.text[:300])}"
        return r.status_code, resp, None
    except httpx.HTTPError as e:
        return None, None, f"network error: {type(e).__name__}: {e}"


def _finish(xid: int, http_status, resp, error):
    with tx() as c:
        c.execute("update nhcx_exchanges set status=%s, http_status=%s, sync_response=%s, error=%s where id=%s",
                  ("failed" if error else "acked", http_status, Jsonb(resp), error, xid))


# ---------------- routes for staff ----------------
class SendIn(BaseModel):
    scenario: str | None = None      # demo mode only: approve | partial | reject | query | timeout


def _channel(c, case_id: str, user: User) -> dict:
    r = c.execute("""select cs.stage, py.nhcx_participant_code from cases cs left join payers py on py.id=cs.payer_id
                     where cs.id=%s and cs.hospital_id=%s""", (case_id, user.hospital_id)).fetchone()
    if not r:
        raise HTTPException(404, "Case not found")
    return r


def _last_out(c, case_id: str, workflow: str):
    return c.execute("""select * from nhcx_exchanges where case_id=%s and workflow=%s and direction='outbound' and action='submit'
                        order by id desc limit 1""", (case_id, workflow)).fetchone()


@router.get("/nhcx/status")
def status(user: User = Depends(current_user)):
    s = core_settings()
    with tx() as c:
        h = c.execute("select hfr_id, nhcx_participant_code from hospitals where id=%s", (user.hospital_id,)).fetchone()
    return {"enabled": enabled(), "demo_mode": demo_mode(), "envelope_mode": s.envelope_mode, "auth_mode": s.auth_mode,
            "gateway": s.nhcx_base_url, "hospital_ready": bool(h["hfr_id"] and h["nhcx_participant_code"]),
            "scenarios": ["approve", "partial", "reject", "query", "timeout"] if demo_mode() else []}


@router.get("/cases/{case_id}/nhcx")
def case_view(case_id: str, user: User = Depends(current_user)):
    with tx() as c:
        ch = _channel(c, case_id, user)
        cs = c.execute("select stage, nhcx_last_decision, nhcx_utr, nhcx_paid_amount from cases where id=%s", (case_id,)).fetchone()
        xs = c.execute("""select id, workflow, action, direction, workflow_id, status, http_status, error, api_call_id,
                                 correlation_id, created_at from nhcx_exchanges where case_id=%s order by id""", (case_id,)).fetchall()
        ready = {}
        for wf in ("preauth", "claim"):
            case, problems = build_claim_case(c, case_id, user.hospital_id, wf)
            ready[wf] = {"ok": case is not None, "problems": problems, "total": float(case.total) if case else None}
        q = c.execute("select id, stage, query_text, status from queries where case_id=%s and source='nhcx' and status<>'sent' order by received_at desc limit 1",
                      (case_id,)).fetchone()
    actions = []
    if enabled() and ch["nhcx_participant_code"]:
        for wf, stages in (("preauth", ("preauth", "preauth_query")), ("claim", ("claim",))):
            if cs["stage"] in stages:
                actions.append({"workflow": wf, "kind": "resubmit" if cs["stage"] == "preauth_query" else "send"})
            last = next((x for x in reversed(xs) if x["workflow"] == wf and x["action"] == "submit"), None)
            if last and last["status"] == "failed" and cs["stage"] in ("preauth_pending", "submitted"):
                actions.append({"workflow": wf, "kind": "resend"})
    return {"enabled": enabled(), "payer_on_nhcx": bool(ch["nhcx_participant_code"]), "demo_mode": demo_mode(),
            "stage": cs["stage"], "last_decision": cs["nhcx_last_decision"], "utr": cs["nhcx_utr"],
            "paid_amount": float(cs["nhcx_paid_amount"]) if cs["nhcx_paid_amount"] is not None else None,
            "ready": ready, "actions": actions, "open_query": q, "messages": xs,
            "can_simulate_payment": demo_mode() and cs["stage"] in ("settled",) and any(x["workflow"] == "claim" for x in xs)}


@router.post("/cases/{case_id}/nhcx/send/{workflow}")
async def send(case_id: str, workflow: str, body: SendIn = SendIn(), user: User = Depends(current_user)):
    """Send (or resend) the pre-auth / claim over NHCX. The pre-flight check still gates a first send."""
    from .cases import apply
    if not enabled():
        raise HTTPException(409, {"code": "nhcx_off", "message": "NHCX is not switched on for this deployment"})
    if workflow not in ENDPOINT:
        raise HTTPException(404, "Unknown workflow")
    if user.role not in ("desk", "accounts", "admin"):
        raise HTTPException(403, "Only the desk sends to NHCX")
    with tx() as c:
        ch = _channel(c, case_id, user)
        if not ch["nhcx_participant_code"]:
            raise HTTPException(409, {"code": "payer_not_on_nhcx", "message": "This payer has no NHCX participant code (Payers)"})
        case, problems = build_claim_case(c, case_id, user.hospital_id, workflow)
        if problems:
            raise HTTPException(422, {"code": "nhcx_incomplete", "message": f"{len(problems)} thing(s) to fill in before NHCX", "problems": problems})
        last = _last_out(c, case_id, workflow)
    stage = ch["stage"]
    bundle = to_claim_bundle(case, submission_id=str(uuid.uuid4()))
    resubmit = workflow == "preauth" and stage == "preauth_query"
    wire, conv = build_request(case, bundle, workflow_id=codes.WORKFLOW_PREAUTH_RESUBMIT if resubmit else None)
    headers = conv.last_headers
    holder: dict = {}

    def record(c, _case):
        holder["xid"] = _log(c, user.hospital_id, case_id, workflow, "submit", "outbound", headers, wire, bundle, "sending")
        if resubmit:   # the revised pre-auth answers the open NHCX query
            c.execute("update queries set status='sent', sent_by=%s, sent_at=now() where case_id=%s and source='nhcx' and status<>'sent'",
                      (user.id, case_id))

    # 1. move the case and record the message BEFORE sending
    if stage in ("preauth", "claim"):
        apply(case_id, SUBMIT_EVENT[workflow], {"estimate": float(case.total), "icd10": case.diagnoses[0].icd10_code} if workflow == "preauth" else {},
              user, note=f"Sent over NHCX (workflow {headers['x-hcx-workflow_id']})", hook=record)
    elif resubmit:
        apply(case_id, "answer_preauth_query", {"reply_text": "Revised pre-auth sent over NHCX (workflow 121)"}, user,
              note="Revised pre-auth (NHCX 121)", hook=record)
    elif last and last["status"] == "failed" and stage in ("preauth_pending", "submitted"):
        with tx() as c:
            record(c, None)
    else:
        raise HTTPException(409, {"code": "bad_stage", "message": f"Nothing to send to NHCX at stage '{stage}'"})
    # 2. send, 3. record the outcome
    http_status, resp, error = await _post(ENDPOINT[workflow], wire, body.scenario)
    _finish(holder["xid"], http_status, resp, error)
    return {"exchange_id": holder["xid"], "status": "failed" if error else "acked", "error": error,
            "workflow_id": headers["x-hcx-workflow_id"], "correlation_id": headers["x-hcx-correlation_id"]}


@router.get("/cases/{case_id}/nhcx/messages/{xid}")
def message(case_id: str, xid: int, user: User = Depends(current_user)):
    """One message: as it travelled, readable headers + FHIR, the gateway's reply, and how we read it."""
    with tx() as c:
        x = c.execute("select * from nhcx_exchanges where id=%s and case_id=%s and hospital_id=%s", (xid, case_id, user.hospital_id)).fetchone()
    if not x:
        raise HTTPException(404, "Message not found")
    fhir = (x["plain"] or {}).get("fhir") or {}
    interp = None
    if any((e.get("resource") or {}).get("resourceType") == "ClaimResponse" for e in fhir.get("entry", [])):
        try:
            interp = interpret(fhir).as_dict()
        except UnreadableResponse as e:
            interp = {"error": str(e)}
    resources = [(e.get("resource") or {}).get("resourceType") for e in fhir.get("entry", [])]
    return {**x, "encrypted": isinstance((x["wire"] or {}).get("payload"), str), "interpretation": interp, "resources": resources}


@router.post("/cases/{case_id}/nhcx/demo-pay")
async def demo_pay(case_id: str, user: User = Depends(current_user)):
    """Demo only: the mock insurer sends a payment notice for this claim."""
    if not demo_mode():
        raise HTTPException(404, "Not found")
    with tx() as c:
        _channel(c, case_id, user)
        last = _last_out(c, case_id, "claim")
    if not last:
        raise HTTPException(409, "No claim has been sent over NHCX")
    r = await client.post(f"/mock/paymentnotice/{last['correlation_id']}")
    return {"http_status": r.status_code}


# ---------------- query reply over NHCX (called by the query desk) ----------------
def build_query_reply(c, q: dict, user: User) -> tuple[dict, dict, dict]:
    """Communication (workflow 19 pre-auth / 161 claim) with the reply text and attached documents."""
    req = q["nhcx_request"] or {}
    case = c.execute("""select cs.case_number, h.name as hospital_name, h.hfr_id from cases cs join hospitals h on h.id=cs.hospital_id
                        where cs.id=%s""", (q["case_id"],)).fetchone()
    payload = [{"contentString": q["draft"]}]
    total = 0
    for d in c.execute("select * from documents where id = any(%s::uuid[]) and case_id=%s", (list(q["attachments"] or []), q["case_id"])).fetchall():
        data = storage.get_bytes(d["storage_path"])
        total += len(data)
        if total > MAX_ATTACH_BYTES:
            raise HTTPException(413, "Attachments over 5 MB in total: attach fewer documents")
        payload.append({"contentAttachment": {"contentType": d["mime"], "title": d["filename"], "data": base64.b64encode(data).decode()}})
    comm_id, org_id = str(uuid.uuid4()), str(uuid.uuid4())
    communication = {"resourceType": "Communication", "id": comm_id, "status": "completed",
                     "identifier": [{"type": {"coding": [codes.ID_CASE_NUMBER["type"]]}, "value": case["case_number"]}],
                     "sent": hcx_timestamp(), "payload": payload,
                     **({"basedOn": [{"reference": f"CommunicationRequest/{req['request_id']}"}]} if req.get("request_id") else {})}
    provider = {"resourceType": "Organization", "id": org_id, "name": case["hospital_name"],
                "identifier": [{"type": {"coding": [codes.ID_PROVIDER_HFR["type"]]}, "system": codes.ID_PROVIDER_HFR["system"],
                                "value": case["hfr_id"]}]}
    fhir = {"resourceType": "Bundle", "id": str(uuid.uuid4()), "type": "collection", "timestamp": hcx_timestamp(),
            "entry": [{"fullUrl": f"urn:uuid:{r['id']}", "resource": r} for r in (communication, provider)]}
    headers = {**reply_headers(req["headers"]), "x-hcx-workflow_id": codes.WORKFLOW_QUERY_REPLY[req.get("workflow", q["stage"])]}
    return headers, fhir, wrap(headers, fhir, callback=True)


async def send_query_reply(xid: int, wire: dict) -> str | None:
    http_status, resp, error = await _post("/hcx/v1/communication/on_request", wire)
    _finish(xid, http_status, resp, error)
    return error


# ---------------- gateway-facing callbacks ----------------
def _require_bearer(bearer_auth: str | None):
    if not enabled():
        raise InboundError(404, "ERR_NOT_FOUND", "NHCX is not enabled here")
    if not bearer_auth or not bearer_auth.startswith("Bearer "):
        raise InboundError(401, "ERR_ACCESS_DENIED", "missing 'bearer_auth' header")


def _seen(c, api_call_id: str) -> bool:
    return bool(c.execute("select 1 from nhcx_exchanges where api_call_id=%s", (api_call_id,)).fetchone())


def _case_for(c, correlation_id: str, workflow: str, recipient: str):
    out = c.execute("""select x.case_id, x.hospital_id, h.nhcx_participant_code from nhcx_exchanges x join hospitals h on h.id=x.hospital_id
                       where x.correlation_id=%s and x.workflow=%s and x.direction='outbound' order by x.id desc limit 1""",
                    (correlation_id, workflow)).fetchone()
    if not out:
        raise InboundError(400, "ERR_INVALID_CORRELATION_ID", f"no {workflow} request with this correlation_id")
    if recipient != out["nhcx_participant_code"]:
        raise InboundError(400, "ERR_INVALID_SENDER_OR_RECIPIENT", "recipient is not this hospital")
    return out


def _move(case_id: str, hospital_id: str, event: str, payload: dict, note: str, open_query: bool = True):
    from .cases import apply
    try:
        apply(str(case_id), event, payload, _system(hospital_id), note=note, open_query=open_query)
    except HTTPException as e:
        d = e.detail if isinstance(e.detail, dict) else {"message": str(e.detail)}
        raise InboundError(400, "ERR_INVALID_STATE", d.get("message", "invalid state")) from e


def handle_decision(workflow: str, body: dict) -> dict:
    headers, fhir = parse(body, expected_status="response.complete")
    with tx() as c:
        if _seen(c, headers["x-hcx-api_call_id"]):
            return headers
        out = _case_for(c, headers["x-hcx-correlation_id"], workflow, headers["x-hcx-recipient_code"])
        try:
            d = interpret(fhir)
        except UnreadableResponse as e:
            raise InboundError(400, "ERR_INVALID_DOMAIN_PAYLOAD", str(e)) from e
        _log(c, out["hospital_id"], out["case_id"], workflow, "on_submit", "inbound", headers, body, fhir, "received")
        c.execute("update cases set nhcx_last_decision=%s where id=%s", (Jsonb(d.as_dict()), out["case_id"]))
        stage = c.execute("select stage from cases where id=%s", (out["case_id"],)).fetchone()["stage"]
    if d.decision == "submitted":          # queued: still with the insurer
        return headers
    event = DECISION_EVENT[(workflow, d.decision)]
    amount = d.eligible if d.eligible is not None else d.submitted
    payload = {"preauth_approved": {"approved_amount": amount},
               "settlement_letter": {"settled_amount": amount, "payer_ref": d.preauth_ref or headers["x-hcx-correlation_id"][:13]},
               "preauth_denied": {"reason": d.disposition or "Rejected by the insurer"},
               "claim_rejected": {"reason": d.disposition or "Rejected by the insurer"},
               "preauth_query": {"query_text": d.disposition or "The insurer asked for more information"},
               "claim_query": {"query_text": d.disposition or "The insurer asked for more information"}}[event]
    already_queried = event.endswith("_query") and stage == event   # the CommunicationRequest came first
    if not already_queried:
        label = {"partially_approved": "partly approved", "approved": "approved"}.get(d.decision, d.decision)
        _move(out["case_id"], out["hospital_id"], event, payload, note=f"NHCX: insurer {label}" + (f" — {d.disposition}" if d.disposition else ""))
    return headers


def handle_communication(body: dict) -> dict:
    headers, fhir = parse(body, expected_status="request.initiated")
    reqs = [e["resource"] for e in fhir.get("entry", []) if (e.get("resource") or {}).get("resourceType") == "CommunicationRequest"]
    cln = None
    for i in (reqs[0].get("identifier") or []) if reqs else []:
        if any(cd.get("code") == "CLN" for cd in (i.get("type") or {}).get("coding", [])):
            cln = i.get("value")
    if not cln:
        raise InboundError(400, "PAYR-1037", "CommunicationRequest with identifier type CLN is required")
    remarks = " ".join(p.get("contentString", "") for p in reqs[0].get("payload", []) if p.get("contentString")) \
        or "The insurer requested more information."
    with tx() as c:
        if _seen(c, headers["x-hcx-api_call_id"]):
            return headers
        row = c.execute("""select cs.id, cs.hospital_id, cs.stage from cases cs join hospitals h on h.id=cs.hospital_id
                           where cs.case_number=%s and h.nhcx_participant_code=%s""", (cln, headers["x-hcx-recipient_code"])).fetchone()
        if not row:
            raise InboundError(400, "ERR_UNKNOWN_CASE", f"no case with case number {cln!r}")
        workflow = {"24": "preauth", "27": "claim"}.get(headers["x-hcx-workflow_id"]) or \
            ("claim" if row["stage"] in ("submitted", "claim_query") else "preauth")
        _log(c, row["hospital_id"], row["id"], "communication", "request", "inbound", headers, body, fhir, "received")
        stage = row["stage"]
    target = f"{workflow}_query"
    if stage != target:
        _move(row["id"], row["hospital_id"], target, {"query_text": remarks}, note=f"NHCX query: {remarks[:120]}")
    from .drafting import classify
    with tx() as c:   # the open query on the desk now carries the insurer's own words and reply route
        c.execute("""update queries set query_text=%s, code=%s, source='nhcx', nhcx_request=%s
                     where id = (select id from queries where case_id=%s and stage=%s and status<>'sent' order by received_at desc limit 1)""",
                  (remarks, classify(remarks), Jsonb({"headers": headers, "request_id": reqs[0].get("id"), "workflow": workflow}),
                   row["id"], workflow))
    return headers


def handle_payment_notice(body: dict) -> tuple[dict, tuple[int, dict] | None]:
    headers, fhir = parse(body, expected_status="request.initiated")
    with tx() as c:
        if _seen(c, headers["x-hcx-api_call_id"]):
            return headers, None
        out = _case_for(c, headers["x-hcx-correlation_id"], "claim", headers["x-hcx-recipient_code"])
        res = {(e.get("resource") or {}).get("resourceType"): e["resource"] for e in fhir.get("entry", []) if e.get("resource")}
        notice = res.get("PaymentNotice")
        if not notice:
            raise InboundError(400, "ERR_INVALID_DOMAIN_PAYLOAD", "bundle has no PaymentNotice")
        utr = None
        for i in (res.get("PaymentReconciliation") or {}).get("identifier") or []:
            if any(cd.get("code") == "UTR" for cd in (i.get("type") or {}).get("coding", [])):
                utr = i.get("value")
        amount = (notice.get("amount") or {}).get("value")
        _log(c, out["hospital_id"], out["case_id"], "paymentnotice", "request", "inbound", headers, body, fhir, "received")
        cs = c.execute("update cases set nhcx_utr=%s, nhcx_paid_amount=%s where id=%s returning case_number, stage",
                       (utr, amount, out["case_id"])).fetchone()
        # not "paid": only a matched bank credit closes a claim. The UTR makes that match certain.
        c.execute("""insert into case_events (hospital_id, case_id, event, from_stage, to_stage, actor_role, payload, note)
                     values (%s,%s,'nhcx_payment_notice',%s,%s,'system',%s,%s)""",
                  (out["hospital_id"], out["case_id"], cs["stage"], cs["stage"], Jsonb({"utr": utr, "amount": amount}),
                   f"Insurer reports payment of ₹{amount} (UTR {utr}); waiting for the bank credit"))
        # NHA guide checkpoint 13: acknowledge with a Task carrying the case number
        task = {"resourceType": "Task", "id": str(uuid.uuid4()), "status": "completed", "intent": "order",
                "description": "Payment notice received" + (f" (UTR {utr})" if utr else ""),
                "output": [{"type": {"coding": [{"system": codes.NDHM_ID_TYPE, "code": "claimNumber"}]}, "valueString": cs["case_number"]}]}
        ack_fhir = {"resourceType": "Bundle", "id": str(uuid.uuid4()), "type": "collection", "timestamp": hcx_timestamp(),
                    "entry": [{"fullUrl": f"urn:uuid:{task['id']}", "resource": task}]}
        ack_headers = reply_headers(headers)
        ack_wire = wrap(ack_headers, ack_fhir, callback=True)
        xid = _log(c, out["hospital_id"], out["case_id"], "paymentnotice", "on_request", "outbound", ack_headers, ack_wire, ack_fhir, "sending")
    return headers, (xid, ack_wire)


def _gw_error(e: InboundError) -> JSONResponse:
    log.warning("rejected inbound NHCX message: %s %s", e.code, e.message)
    return JSONResponse(status_code=e.status, content={"timestamp": hcx_timestamp(), "error": {"code": e.code, "message": e.message, "trace": None}})


def _make_on_submit(workflow: str):
    def on_submit(body: dict[str, Any] = Body(...),
                  bearer_auth: str | None = Header(default=None, alias="bearer_auth", convert_underscores=False)):
        try:
            _require_bearer(bearer_auth)
            return JSONResponse(status_code=202, content=ack(handle_decision(workflow, body)))
        except InboundError as e:
            return _gw_error(e)
    on_submit.__name__ = f"{workflow}_on_submit"
    return on_submit


for _wf in ("preauth", "claim"):
    gateway.add_api_route(f"/{_wf}/on_submit", _make_on_submit(_wf), methods=["POST"], status_code=202,
                          summary=f"Insurer's decision on our {_wf}")


@gateway.post("/communication/request", status_code=202, summary="Insurer asks for more information (workflow 24 / 27)")
def communication_request(body: dict[str, Any] = Body(...),
                          bearer_auth: str | None = Header(default=None, alias="bearer_auth", convert_underscores=False)):
    try:
        _require_bearer(bearer_auth)
        return JSONResponse(status_code=202, content=ack(handle_communication(body)))
    except InboundError as e:
        return _gw_error(e)


@gateway.post("/paymentnotice/request", status_code=202, summary="Insurer reports payment; we acknowledge with a Task")
def paymentnotice_request(background: BackgroundTasks, body: dict[str, Any] = Body(...),
                          bearer_auth: str | None = Header(default=None, alias="bearer_auth", convert_underscores=False)):
    try:
        _require_bearer(bearer_auth)
        headers, pending = handle_payment_notice(body)
    except InboundError as e:
        return _gw_error(e)
    if pending:
        xid, wire = pending

        async def send_ack():
            http_status, resp, error = await _post("/hcx/v1/paymentnotice/on_request", wire)
            _finish(xid, http_status, resp, error)
        background.add_task(send_ack)
    return JSONResponse(status_code=202, content=ack(headers))
