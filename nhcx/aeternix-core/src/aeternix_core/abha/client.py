"""HTTP to the ABDM ABHA v3 APIs.

Every call carries REQUEST-ID (new UUID), TIMESTAMP (ISO-8601, ms, UTC) and the gateway session
token (`Authorization: Bearer`, the same token as NHCX, from nhcx.session). User-level calls
add `X-token: Bearer <user token>` (profile, card) or `T-token` (multi-account selection).

A 401 refreshes the session token and retries once. ABDM errors come back in several shapes;
all become AbhaError(status, code, message) with a message that is safe to show at the counter.
Patient data is never logged.
"""
import logging
import uuid
from datetime import datetime, timezone

import httpx

from ..config import get_settings
from ..nhcx import session

log = logging.getLogger("aeternix.abha")

# Tests replace this with httpx.MockTransport(...).
transport: httpx.BaseTransport | None = None


class AbhaError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.status, self.code, self.message = status, code, message


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _headers(extra: dict | None) -> dict:
    return {"Content-Type": "application/json", "Accept": "application/json",
            "REQUEST-ID": str(uuid.uuid4()), "TIMESTAMP": timestamp(),
            "Authorization": f"Bearer {session.token()}", **(extra or {})}


def _error(r: httpx.Response) -> AbhaError:
    code, message = f"HTTP-{r.status_code}", ""
    try:
        data = r.json()
    except ValueError:
        data = None
    if isinstance(data, list) and data:
        data = data[0]
    if isinstance(data, dict):
        err = data.get("error") if isinstance(data.get("error"), dict) else data
        code = str(err.get("code") or code)
        message = err.get("message") or err.get("details") or ""
        if not message:
            # field-level validation: {"loginId": "Invalid LoginId"}
            fields = {k: v for k, v in err.items() if isinstance(v, str) and k not in ("code", "timestamp")}
            message = "; ".join(fields.values())
    if not message:
        message = {400: "ABDM rejected the request.", 401: "ABDM session expired. Please try again.",
                   404: "Not found at ABDM.", 422: "ABDM could not process the request.",
                   429: "Too many attempts. Please wait a minute and try again."}.get(
            r.status_code, "ABDM is not responding properly. Please try again shortly.")
    return AbhaError(r.status_code, code, str(message))


def _send(method: str, path: str, *, json: dict | None = None, headers: dict | None = None) -> httpx.Response:
    s = get_settings()

    def once() -> httpx.Response:
        with httpx.Client(base_url=s.abha_base_url, transport=transport,
                          timeout=s.abha_timeout_seconds) as c:
            return c.request(method, path, json=json, headers=_headers(headers))

    try:
        r = once()
        if r.status_code == 401 and not (headers or {}).get("X-token"):
            session.invalidate()
            r = once()
    except session.SessionError as e:
        raise AbhaError(503, "NO-SESSION", f"Cannot log in to ABDM: {e}") from e
    except httpx.HTTPError as e:
        raise AbhaError(503, "NETWORK", f"Cannot reach ABDM ({type(e).__name__}). Please try again.") from e
    log.info("ABHA %s %s -> %s", method, path, r.status_code)
    if r.status_code >= 400:
        err = _error(r)
        if r.status_code == 400 and "decrypt" in err.message.lower():
            from . import crypto
            crypto.forget()   # key may have rotated: the next attempt refetches it
        raise err
    return r


def get_json(path: str, headers: dict | None = None):
    return _send("GET", path, headers=headers).json()


def post_json(path: str, body: dict, headers: dict | None = None):
    r = _send("POST", path, json=body, headers=headers)
    return r.json() if r.content else {}


def get_raw(path: str, headers: dict | None = None) -> tuple[bytes, str]:
    r = _send("GET", path, headers={**(headers or {}), "Accept": "*/*"})
    return r.content, r.headers.get("content-type", "application/octet-stream")
