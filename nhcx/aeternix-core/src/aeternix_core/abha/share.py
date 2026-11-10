"""Scan & Share (M1 SHARE_PATIENT_PROFILE_701): OPD registration from the patient's ABHA app.

1. The patient scans our counter QR (https://phrsbx.abdm.gov.in/share-profile?hip-id=..&counter-id=..)
   in the ABHA / PHR app and taps Share.
2. The ABDM gateway POSTs the profile to our bridge URL: {bridge}/api/v3/hip/patient/share.
   We answer 202 at once.
3. Within 10 seconds we call {gateway}/api/hiecm/patient-share/v3/on-share with an OPD token
   number; the app shows that token to the patient.

The ABHA address in on-share is sent in plain text (not encrypted). A repeated push with the
same REQUEST-ID gets the same token (idempotent).
"""
import logging
import threading
import uuid
from datetime import date

import httpx
from sqlalchemy import func, select

from .. import store
from ..config import get_settings
from ..nhcx import session
from ..store import ShareRow
from . import client, patients

log = logging.getLogger("aeternix.scan_share")

# Tests replace this with httpx.MockTransport(...).
transport: httpx.BaseTransport | None = None
_token_lock = threading.Lock()


def counter_qr_url(counter_id: str | None = None) -> str | None:
    s = get_settings()
    if not s.hip_id:
        return None
    return f"https://{s.phr_host}/share-profile?hip-id={s.hip_id}&counter-id={counter_id or s.counter_id}"


def _profile_from_push(body: dict) -> dict:
    patient = ((body.get("profile") or {}).get("patient")) or body.get("patient") or {}
    addr = patient.get("address") or {}
    if isinstance(addr, str):
        addr = {"line": addr}
    dob = "-".join(str(x).zfill(2) for x in (patient.get("dayOfBirth"), patient.get("monthOfBirth"),
                                             patient.get("yearOfBirth")) if x)
    return {
        "abhaNumber": patient.get("abhaNumber") or patient.get("healthIdNumber"),
        "abhaAddress": patient.get("abhaAddress") or patient.get("healthId"),
        "name": patient.get("name"),
        "gender": patient.get("gender"),
        "dob": dob or None,
        "mobile": patient.get("phoneNumber") or patient.get("mobile"),
        "address": ", ".join(x for x in (addr.get("line"), addr.get("district"), addr.get("state"),
                                         addr.get("pincode")) if x) or None,
    }


def _next_token() -> str:
    """Daily running OPD token number per hospital (simple, shown to the patient)."""
    with _token_lock, store.session() as s:
        today = date.today()
        n = s.scalar(select(func.count(ShareRow.id)).where(func.date(ShareRow.created_at) == today.isoformat()))
        return str((n or 0) + 1)


def receive(body: dict, request_id: str) -> ShareRow:
    """Store the push and allocate the token. Same REQUEST-ID again -> same row."""
    with store.session() as s:
        row = s.scalar(select(ShareRow).where(ShareRow.request_id == request_id))
        if row:
            return row
    profile = _profile_from_push(body)
    linked = patients.link(profile, source="scan_share") if profile.get("abhaNumber") else None
    meta = body.get("metaData") or body.get("metadata") or {}
    row = ShareRow(request_id=request_id, hip_id=meta.get("hipId"), counter_id=meta.get("context"),
                   abha_address=profile.get("abhaAddress"),
                   patient_id=linked["patient_id"] if linked else None,
                   token_number=_next_token(), status="received", profile=profile)
    with store.session() as s:
        s.add(row)
        s.commit()
    return row


def on_share_body(row: ShareRow) -> dict:
    return {
        "acknowledgement": {
            "abhaAddress": row.abha_address,          # plain text, NOT encrypted
            "status": "SUCCESS",
            "profile": {"context": row.counter_id or get_settings().counter_id,
                        "tokenNumber": row.token_number,
                        "expiry": str(get_settings().share_token_minutes * 60)},
        },
        "response": {"requestId": row.request_id},
    }


def send_on_share(row_id: int) -> None:
    """Call the gateway's on-share. Runs right after the 202 (10-second SLA)."""
    s = get_settings()
    with store.session() as db:
        row = db.get(ShareRow, row_id)
        body = on_share_body(row)
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "REQUEST-ID": str(uuid.uuid4()), "TIMESTAMP": client.timestamp(),
                   "X-CM-ID": s.abdm_cm_id}
        try:
            headers["Authorization"] = f"Bearer {session.token()}"
            with httpx.Client(base_url=s.abdm_gateway_url, transport=transport, timeout=8.0) as c:
                r = c.post(s.patient_share_on_share_path, json=body, headers=headers)
            ok = r.status_code < 300
            row.status = "acknowledged" if ok else "ack_failed"
            row.error = None if ok else f"HTTP {r.status_code}: {r.text[:300]}"
        except (httpx.HTTPError, session.SessionError) as e:
            row.status, row.error = "ack_failed", f"{type(e).__name__}: {e}"
        db.commit()
        log.info("scan&share on-share request_id=%s token=%s -> %s", row.request_id, row.token_number, row.status)


def recent(limit: int = 50) -> list[dict]:
    with store.session() as s:
        return [r.summary() for r in s.scalars(select(ShareRow).order_by(ShareRow.id.desc()).limit(limit))]
