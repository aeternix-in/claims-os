"""M1 endpoints.

/abha/...                    used by the counter screen (/ui/abha)
/api/v3/hip/patient/share    called BY the ABDM gateway (Scan & Share), on our bridge URL
"""
import logging
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Body, Header
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from ..abha import consent, flows, patients, share
from ..abha.client import AbhaError
from ..abha.validate import InvalidInput
from ..config import get_settings

log = logging.getLogger("aeternix.api.abha")
UI_FILE = Path(__file__).parent / "static" / "abha.html"

router = APIRouter()


def install(app) -> None:
    app.include_router(router)

    @app.exception_handler(AbhaError)
    async def abha_error(_, e: AbhaError):
        status = e.status if 400 <= e.status < 600 else 502
        return JSONResponse(status_code=status, content={"detail": e.message, "code": e.code})

    @app.exception_handler(InvalidInput)
    async def invalid_input(_, e: InvalidInput):
        return JSONResponse(status_code=400, content={"detail": str(e), "code": "INVALID-INPUT"})


@router.get("/ui/abha", include_in_schema=False)
def abha_ui():
    return FileResponse(UI_FILE, media_type="text/html")


@router.get("/abha/config", tags=["ABHA (M1)"])
def abha_config():
    s = get_settings()
    return {"consent_version": consent.VERSION, "consent": consent.TEXT,
            "hip_id": s.hip_id, "counter_id": s.counter_id, "qr_url": share.counter_qr_url(),
            "resend_after_seconds": flows.RESEND_AFTER_SECONDS, "max_resends": flows.MAX_RESENDS}


# ---- creation ----------------------------------------------------------------------------------

class AadhaarIn(BaseModel):
    aadhaar: str
    previous_txn: str | None = None     # set when this is a resend


class EnrolVerifyIn(BaseModel):
    txnId: str
    otp: str
    mobile: str
    consent: bool


class TxnMobileIn(BaseModel):
    txnId: str
    mobile: str


class TxnOtpIn(BaseModel):
    txnId: str
    otp: str


class AddressIn(BaseModel):
    txnId: str
    abhaAddress: str


@router.post("/abha/enrol/otp", tags=["ABHA (M1)"], summary="1-2. Send / resend Aadhaar OTP")
def enrol_otp(body: AadhaarIn):
    return flows.enrol_request_otp(body.aadhaar, body.previous_txn)


@router.post("/abha/enrol/verify", tags=["ABHA (M1)"], summary="3. Verify Aadhaar OTP and create ABHA")
def enrol_verify(body: EnrolVerifyIn):
    return flows.enrol_verify(body.txnId, body.otp, body.mobile, body.consent)


@router.post("/abha/enrol/mobile/otp", tags=["ABHA (M1)"], summary="4a. OTP to the communication mobile")
def enrol_mobile_otp(body: TxnMobileIn):
    return flows.enrol_mobile_otp(body.txnId, body.mobile)


@router.post("/abha/enrol/mobile/verify", tags=["ABHA (M1)"], summary="4b. Verify the communication mobile")
def enrol_mobile_verify(body: TxnOtpIn):
    return flows.enrol_mobile_verify(body.txnId, body.otp)


@router.get("/abha/enrol/suggestions", tags=["ABHA (M1)"], summary="5. Suggested ABHA addresses")
def enrol_suggestions(txnId: str):
    return flows.enrol_suggestions(txnId)


@router.post("/abha/enrol/address", tags=["ABHA (M1)"], summary="6. Set the ABHA address")
def enrol_address(body: AddressIn):
    return flows.enrol_address(body.txnId, body.abhaAddress)


# ---- verification ------------------------------------------------------------------------------

class LoginOtpIn(BaseModel):
    method: Literal["abha-number-aadhaar-otp", "abha-number-mobile-otp", "abha-address-aadhaar-otp",
                    "abha-address-mobile-otp", "mobile-otp", "aadhaar-otp"]
    value: str
    previous_txn: str | None = None


class LoginVerifyIn(BaseModel):
    txnId: str
    otp: str
    method: str | None = None


class SelectIn(BaseModel):
    sid: str
    abhaNumber: str


@router.post("/abha/login/otp", tags=["ABHA (M1)"], summary="Verify ABHA: send / resend OTP")
def login_otp(body: LoginOtpIn):
    return flows.login_request_otp(body.method, body.value, body.previous_txn)


@router.post("/abha/login/verify", tags=["ABHA (M1)"], summary="Verify ABHA: check OTP")
def login_verify(body: LoginVerifyIn):
    return flows.login_verify(body.txnId, body.otp, body.method)


@router.post("/abha/login/select", tags=["ABHA (M1)"], summary="Pick one ABHA linked to a mobile")
def login_select(body: SelectIn):
    return flows.login_select(body.sid, body.abhaNumber)


@router.get("/abha/profile", tags=["ABHA (M1)"], summary="ABHA profile (and link to a patient ID)")
def abha_profile(sid: str):
    return flows.profile(sid)


@router.get("/abha/card", tags=["ABHA (M1)"], summary="ABHA card image from ABDM")
def abha_card(sid: str, download: bool = False):
    data, ctype = flows.card(sid)
    ext = "pdf" if "pdf" in ctype else ("jpg" if "jpeg" in ctype else "png")
    headers = {"Cache-Control": "no-store"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="abha-card.{ext}"'
    return Response(content=data, media_type=ctype, headers=headers)


@router.post("/abha/logout", tags=["ABHA (M1)"])
def abha_logout(sid: str):
    flows.end_session(sid)
    return {"ok": True}


# ---- patients and Scan & Share -----------------------------------------------------------------

@router.get("/abha/qr.svg", tags=["ABHA (M1)"], summary="Counter QR for Scan & Share (SVG)")
def abha_qr(counter_id: str | None = None):
    url = share.counter_qr_url(counter_id)
    if not url:
        return JSONResponse(status_code=404, content={"detail": "Set AETERNIX_HIP_ID first."})
    import io

    import segno
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=6, border=2, dark="#12343B")
    return Response(content=buf.getvalue(), media_type="image/svg+xml")


@router.get("/abha/patients", tags=["ABHA (M1)"], summary="Patients linked to an ABHA (one ABHA = one patient)")
def abha_patients():
    return patients.list_all()


@router.get("/abha/shares", tags=["ABHA (M1)"], summary="Recent Scan & Share registrations")
def abha_shares():
    return share.recent()


@router.post("/api/v3/hip/patient/share", status_code=202, tags=["ABDM gateway callbacks"],
             summary="Scan & Share: patient profile pushed by the ABDM gateway")
@router.post("/v3/hip/patient/share", status_code=202, include_in_schema=False)
def patient_share(background: BackgroundTasks, body: dict[str, Any] = Body(...),
                  request_id: str | None = Header(default=None, alias="REQUEST-ID")):
    rid = request_id or (body.get("requestId")) or ""
    if not rid:
        return JSONResponse(status_code=400, content={"error": {"code": "ABDM-1006",
                                                                "message": "REQUEST-ID header missing"}})
    row = share.receive(body, rid)
    if row.status != "acknowledged":
        background.add_task(share.send_on_share, row.id)
    return JSONResponse(status_code=202, content={})
