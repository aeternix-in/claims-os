"""The M1 flows, step by step, as the counter screen uses them.

Creation (CRT_ABHA_101-114), ABHA v3:
  1  enrol_request_otp      POST /v3/enrollment/request/otp        Aadhaar -> OTP to Aadhaar mobile
  2  (resend)               same call, at most 2 resends, 60 s apart
  3  enrol_verify           POST /v3/enrollment/enrol/byAadhaar    OTP + communication mobile + consent
  4  enrol_mobile_otp/verify  request/otp + auth/byAbdm            only if that mobile != Aadhaar mobile
  5  enrol_suggestions      GET  /v3/enrollment/enrol/suggestion
  6  enrol_address          POST /v3/enrollment/enrol/abha-address
  then profile / card with the user token from step 3.

Verification (VRFY_ABHA_101-405): login_request_otp + login_verify for
  abha-number + Aadhaar OTP | abha-number + ABHA mobile OTP      (/v3/profile/login/...)
  abha-address + Aadhaar OTP | abha-address + ABHA mobile OTP    (/v3/phr/web/login/abha/...)
  mobile OTP (may list several ABHAs -> login_select)            (/v3/profile/login/verify/user)
  Aadhaar OTP ("find my ABHA", or offer creation if none)

ABDM's user tokens stay on the server; the screen only gets an opaque session id (`sid`).
"""
import secrets
import threading
import time
from datetime import datetime, timezone

from . import client, crypto, patients
from . import validate as v
from .client import AbhaError

CONSENT = {"code": "abha-enrollment", "version": "1.4"}
MAX_RESENDS = 2
RESEND_AFTER_SECONDS = 60

_lock = threading.Lock()
_pending: dict[str, dict] = {}    # txnId -> {flow, method, sent_at, resends}
_sessions: dict[str, dict] = {}   # sid -> {token, kind, exp, txnId, accounts}


# ---- small helpers -----------------------------------------------------------------------------

def _remember_otp(txn_id: str, flow: str, method: str, previous: str | None = None) -> None:
    with _lock:
        prev = _pending.pop(previous, None) if previous else None
        _pending[txn_id] = {"flow": flow, "method": method, "sent_at": time.time(),
                            "resends": (prev["resends"] + 1) if prev else 0}


def check_resend(previous_txn: str) -> None:
    """VRFY_ABHA_305 / CRT_ABHA_106: resend at most twice, each after 60 s."""
    with _lock:
        p = _pending.get(previous_txn)
    if not p:
        return
    if p["resends"] >= MAX_RESENDS:
        raise v.InvalidInput("OTP can be resent only 2 times. Please start again.")
    wait = RESEND_AFTER_SECONDS - (time.time() - p["sent_at"])
    if wait > 0:
        raise v.InvalidInput(f"Please wait {int(wait) + 1} seconds before resending the OTP.")


def _method_of(txn_id: str, default: str) -> str:
    with _lock:
        return _pending.get(txn_id, {}).get("method", default)


def _new_session(token: str, kind: str, expires_in, txn_id: str | None = None,
                 accounts: list | None = None) -> str:
    sid = secrets.token_urlsafe(18)
    with _lock:
        now = time.time()
        for k in [k for k, s in _sessions.items() if s["exp"] < now]:
            del _sessions[k]
        _sessions[sid] = {"token": token, "kind": kind, "exp": now + float(expires_in or 1800),
                          "txnId": txn_id, "accounts": accounts or []}
    return sid


def _session(sid: str, kind: str | None = None) -> dict:
    with _lock:
        s = _sessions.get(sid)
    if not s or s["exp"] < time.time():
        raise AbhaError(401, "SESSION-EXPIRED", "This ABHA session has expired. Please verify again.")
    if kind and s["kind"] != kind:
        raise AbhaError(400, "WRONG-SESSION", "Select the ABHA account first.")
    return s


def end_session(sid: str) -> None:
    with _lock:
        _sessions.pop(sid, None)


def _otp_value(otp: str) -> str:
    return crypto.encrypt(v.otp(otp))


def _first(d: dict, *keys, default=None):
    for k in keys:
        if d.get(k) not in (None, "", []):
            return d[k]
    return default


def normalize_profile(p: dict) -> dict:
    """One shape for the enrol, ABHA-number and ABHA-address (PHR) profile replies."""
    name = _first(p, "name", "fullName") or " ".join(
        x for x in (p.get("firstName"), p.get("middleName"), p.get("lastName")) if x)
    dob = _first(p, "dob", "dateOfBirth")
    if not dob and p.get("yearOfBirth"):
        dob = "-".join(str(x).zfill(2) for x in (p.get("dayOfBirth") or "", p.get("monthOfBirth") or "",
                                                  p.get("yearOfBirth")) if x)
    phr = p.get("phrAddress") or []
    address = _first(p, "preferredAbhaAddress", "abhaAddress", "healthId",
                     default=phr[0] if phr else None)
    return {
        "abhaNumber": _first(p, "ABHANumber", "abhaNumber", "healthIdNumber"),
        "abhaAddress": address,
        "name": name,
        "gender": p.get("gender"),
        "dob": dob,
        "mobile": p.get("mobile"),
        "email": p.get("email"),
        "address": p.get("address"),
        "district": p.get("districtName"),
        "state": p.get("stateName"),
        "pincode": _first(p, "pinCode", "pincode"),
        "photo": _first(p, "photo", "profilePhoto", "kycPhoto"),
        "status": _first(p, "abhaStatus", "status"),
        "kycVerified": p.get("kycVerified"),
    }


# ---- creation via Aadhaar OTP ------------------------------------------------------------------

def enrol_request_otp(aadhaar: str, previous_txn: str | None = None) -> dict:
    number = v.aadhaar(aadhaar)
    if previous_txn:
        check_resend(previous_txn)
    r = client.post_json("/v3/enrollment/request/otp", {
        "txnId": "", "scope": ["abha-enrol"], "loginHint": "aadhaar",
        "loginId": crypto.encrypt(number), "otpSystem": "aadhaar"})
    _remember_otp(r["txnId"], "enrol", "aadhaar", previous_txn)
    return {"txnId": r["txnId"], "message": r.get("message"),
            "resendsLeft": MAX_RESENDS - _pending[r["txnId"]]["resends"]}


def enrol_verify(txn_id: str, otp: str, mobile: str, consent: bool) -> dict:
    if not consent:
        raise v.InvalidInput("Consent is required before creating an ABHA.")
    comm_mobile = v.mobile(mobile)
    r = client.post_json("/v3/enrollment/enrol/byAadhaar", {
        "authData": {"authMethods": ["otp"],
                     "otp": {"txnId": txn_id, "otpValue": _otp_value(otp), "mobile": comm_mobile}},
        "consent": CONSENT})
    profile = normalize_profile(r.get("ABHAProfile") or {})
    tokens = r.get("tokens") or {}
    sid = _new_session(tokens["token"], "abha", tokens.get("expiresIn"), r.get("txnId")) \
        if tokens.get("token") else None
    linked = patients.link(profile, source="created" if r.get("isNew", True) else "verified")
    return {
        "txnId": r.get("txnId", txn_id),
        "isNew": r.get("isNew", True),
        "message": r.get("message"),
        "profile": profile,
        "mobileVerified": profile.get("mobile") is not None,
        "communicationMobile": v.mask(comm_mobile),
        "sid": sid,
        "patient": linked,
    }


def enrol_mobile_otp(txn_id: str, mobile: str) -> dict:
    r = client.post_json("/v3/enrollment/request/otp", {
        "txnId": txn_id, "scope": ["abha-enrol", "mobile-verify"], "loginHint": "mobile",
        "loginId": crypto.encrypt(v.mobile(mobile)), "otpSystem": "abdm"})
    _remember_otp(r["txnId"], "enrol-mobile", "mobile")
    return {"txnId": r["txnId"], "message": r.get("message")}


def enrol_mobile_verify(txn_id: str, otp: str) -> dict:
    r = client.post_json("/v3/enrollment/auth/byAbdm", {
        "scope": ["abha-enrol", "mobile-verify"],
        "authData": {"authMethods": ["otp"], "otp": {
            "timeStamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "txnId": txn_id, "otpValue": _otp_value(otp)}}})
    ok = str(r.get("authResult", "")).lower() == "success"
    if not ok:
        raise AbhaError(400, "OTP-FAILED", r.get("message") or "Incorrect OTP. Please try again.")
    return {"txnId": r.get("txnId", txn_id), "message": r.get("message")}


def enrol_suggestions(txn_id: str) -> dict:
    r = client.get_json("/v3/enrollment/enrol/suggestion", headers={"Transaction_Id": txn_id})
    return {"txnId": r.get("txnId", txn_id), "suggestions": r.get("abhaAddressList") or []}


def enrol_address(txn_id: str, address: str) -> dict:
    handle = v.abha_handle(address)
    r = client.post_json("/v3/enrollment/enrol/abha-address",
                         {"txnId": txn_id, "abhaAddress": handle, "preferred": 1})
    abha_no = r.get("healthIdNumber") or r.get("ABHANumber")
    preferred = r.get("preferredAbhaAddress") or handle
    if "@" not in preferred:
        preferred = f"{preferred}@sbx"
    if abha_no:
        patients.update_address(abha_no, preferred)
    return {"txnId": r.get("txnId", txn_id), "abhaNumber": abha_no, "abhaAddress": preferred}


# ---- verification / login ----------------------------------------------------------------------

# method -> (endpoint family, scope, loginHint, otpSystem)
LOGIN_METHODS = {
    "abha-number-aadhaar-otp": ("profile", ["abha-login", "aadhaar-verify"], "abha-number", "aadhaar"),
    "abha-number-mobile-otp": ("profile", ["abha-login", "mobile-verify"], "abha-number", "abdm"),
    "abha-address-aadhaar-otp": ("phr", ["abha-address-login", "aadhaar-verify"], "abha-address", "aadhaar"),
    "abha-address-mobile-otp": ("phr", ["abha-address-login", "mobile-verify"], "abha-address", "abdm"),
    "mobile-otp": ("profile", ["abha-login", "mobile-verify"], "mobile", "abdm"),
    "aadhaar-otp": ("profile", ["abha-login", "aadhaar-verify"], "aadhaar", "aadhaar"),
}
_PATHS = {"profile": ("/v3/profile/login/request/otp", "/v3/profile/login/verify"),
          "phr": ("/v3/phr/web/login/abha/request/otp", "/v3/phr/web/login/abha/verify")}


def _login_id(hint: str, value: str) -> str:
    if hint == "abha-number":
        return v.abha_number(value)
    if hint == "abha-address":
        return v.abha_address(value)
    if hint == "mobile":
        return v.mobile(value)
    return v.aadhaar(value)


def login_request_otp(method: str, value: str, previous_txn: str | None = None) -> dict:
    if method not in LOGIN_METHODS:
        raise v.InvalidInput("Unknown verification method.")
    family, scope, hint, otp_system = LOGIN_METHODS[method]
    login_id = _login_id(hint, value)
    if previous_txn:
        check_resend(previous_txn)
    extra = {}
    if family == "phr":
        # tells us early if the address exists and which methods it allows
        found = client.post_json("/v3/phr/web/login/abha/search", {"abhaAddress": login_id})
        allowed = found.get("authMethods") or []
        want = "AADHAAR_OTP" if otp_system == "aadhaar" else "MOBILE_OTP"
        if allowed and want not in allowed:
            raise AbhaError(400, "AUTH-METHOD", f"This ABHA address does not allow {want.replace('_', ' ')}. "
                                                f"Available: {', '.join(allowed)}.")
        extra = {"name": found.get("fullName")}
    r = client.post_json(_PATHS[family][0], {"scope": scope, "loginHint": hint,
                                             "loginId": crypto.encrypt(login_id), "otpSystem": otp_system})
    _remember_otp(r["txnId"], "login", method, previous_txn)
    return {"txnId": r["txnId"], "message": r.get("message"),
            "resendsLeft": MAX_RESENDS - _pending[r["txnId"]]["resends"], **extra}


def login_verify(txn_id: str, otp: str, method: str | None = None) -> dict:
    method = method or _method_of(txn_id, "")
    if method not in LOGIN_METHODS:
        raise v.InvalidInput("Unknown verification method.")
    family, scope, _, _ = LOGIN_METHODS[method]
    r = client.post_json(_PATHS[family][1], {"scope": scope, "authData": {
        "authMethods": ["otp"], "otp": {"txnId": txn_id, "otpValue": _otp_value(otp)}}})
    if str(r.get("authResult", "success")).lower() != "success":
        raise AbhaError(400, "OTP-FAILED", r.get("message") or "Incorrect OTP. Please try again.")

    if family == "phr":
        tokens = r.get("tokens") or {}
        users = r.get("users") or []
        sid = _new_session(tokens.get("token", ""), "phr", tokens.get("expiresIn"), txn_id)
        return {"message": r.get("message"), "sid": sid, "needsSelection": False,
                "accounts": [_account(u) for u in users]}

    accounts = [_account(a) for a in (r.get("accounts") or [])]
    if not accounts:
        what = "mobile number" if method == "mobile-otp" else "Aadhaar number"
        return {"message": f"No ABHA is registered with this {what}.", "sid": None,
                "needsSelection": False, "accounts": [], "noAbha": True}
    if method == "mobile-otp":
        # T-token (5 min): choose the account, then login/verify/user gives the user token
        sid = _new_session(r["token"], "t", r.get("expiresIn") or 300, r.get("txnId", txn_id), accounts)
        if len(accounts) == 1:
            return login_select(sid, accounts[0]["abhaNumber"])
        return {"message": r.get("message"), "sid": sid, "needsSelection": True, "accounts": accounts}
    sid = _new_session(r["token"], "abha", r.get("expiresIn"), txn_id, accounts)
    return {"message": r.get("message"), "sid": sid, "needsSelection": False, "accounts": accounts}


def _account(a: dict) -> dict:
    return {"abhaNumber": _first(a, "ABHANumber", "abhaNumber", "healthIdNumber"),
            "abhaAddress": _first(a, "preferredAbhaAddress", "abhaAddress"),
            "name": _first(a, "name", "fullName"), "gender": a.get("gender"), "dob": a.get("dob"),
            "status": a.get("status"), "kycVerified": _first(a, "kycVerified", "kycStatus"),
            "photo": a.get("profilePhoto")}


def login_select(sid: str, abha_number: str) -> dict:
    s = _session(sid, "t")
    number = v.abha_number(abha_number)
    known = {a["abhaNumber"] for a in s["accounts"] if a.get("abhaNumber")}
    if known and number not in known and abha_number not in known:
        raise v.InvalidInput("Choose one of the listed ABHA accounts.")
    chosen = abha_number if abha_number in known else number
    r = client.post_json("/v3/profile/login/verify/user", {"ABHANumber": chosen, "txnId": s["txnId"]},
                         headers={"T-token": f"Bearer {s['token']}"})
    end_session(sid)
    new_sid = _new_session(r["token"], "abha", r.get("expiresIn"))
    return {"message": "ABHA verified", "sid": new_sid, "needsSelection": False,
            "accounts": [a for a in s["accounts"] if a.get("abhaNumber") == chosen]}


# ---- profile and card --------------------------------------------------------------------------

def profile(sid: str) -> dict:
    s = _session(sid)
    if s["kind"] == "t":
        raise AbhaError(400, "WRONG-SESSION", "Select the ABHA account first.")
    path = "/v3/phr/web/login/profile/abha-profile" if s["kind"] == "phr" else "/v3/profile/account"
    p = normalize_profile(client.get_json(path, headers={"X-token": f"Bearer {s['token']}"}))
    return {"profile": p, "patient": patients.link(p, source="verified")}


def card(sid: str) -> tuple[bytes, str]:
    s = _session(sid)
    if s["kind"] == "t":
        raise AbhaError(400, "WRONG-SESSION", "Select the ABHA account first.")
    path = "/v3/phr/web/login/profile/abha/phr-card" if s["kind"] == "phr" else "/v3/profile/account/abha-card"
    data, ctype = client.get_raw(path, headers={"X-token": f"Bearer {s['token']}"})
    if ctype.startswith("application/json"):
        import base64, json
        body = json.loads(data or b"{}")
        b64 = _first(body, "card", "abhaCard", "data", "image")
        if not b64:
            raise AbhaError(502, "NO-CARD", "ABDM did not return a card.")
        return base64.b64decode(b64), "image/png"
    return data, ctype
