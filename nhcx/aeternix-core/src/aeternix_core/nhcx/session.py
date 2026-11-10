"""NHCX session token (NHA guide checkpoint 06 "Get Auth Token").

The token comes from the ABDM gateway v3 sessions API with the sandbox client credentials.
It is cached and refreshed `session_refresh_margin_seconds` before expiry (tokens last ~20 min);
`invalidate()` forces a fresh one after a 401. Only the backend ever holds the token.

Synchronous on purpose: one short call per ~20 minutes, and it is also needed from synchronous
code (certificate lookup during encryption).
"""
import logging
import threading
import time
import uuid
from datetime import datetime, timezone

import httpx

from ..config import get_settings

log = logging.getLogger("aeternix.session")

# Tests replace this with httpx.MockTransport(...).
transport: httpx.BaseTransport | None = None

_lock = threading.Lock()
_token: str | None = None
_expires_at: float = 0.0


class SessionError(Exception):
    pass


def _fetch() -> tuple[str, float]:
    s = get_settings()
    if not s.sandbox_client_id or not s.sandbox_client_secret:
        raise SessionError("auth_mode is 'session' but AETERNIX_SANDBOX_CLIENT_ID / _SECRET are not set")
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               "REQUEST-ID": str(uuid.uuid4()),
               "TIMESTAMP": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
               "X-CM-ID": "sbx"}
    body = {"clientId": s.sandbox_client_id, "clientSecret": s.sandbox_client_secret,
            "grantType": "client_credentials"}
    try:
        with httpx.Client(transport=transport, timeout=s.nhcx_timeout_seconds) as c:
            r = c.post(s.session_url, json=body, headers=headers)
    except httpx.HTTPError as e:
        raise SessionError(f"session request failed: {type(e).__name__}") from e
    if r.status_code != 200:
        raise SessionError(f"session request refused: HTTP {r.status_code}")
    data = r.json()
    token = data.get("accessToken") or data.get("access_token")
    if not token:
        raise SessionError("session reply has no accessToken")
    lifetime = float(data.get("expiresIn") or data.get("expires_in") or 600)
    return token, time.time() + lifetime


def token() -> str:
    """A valid token, refreshed proactively before it expires."""
    global _token, _expires_at
    margin = get_settings().session_refresh_margin_seconds
    with _lock:
        if _token is None or time.time() >= _expires_at - margin:
            _token, _expires_at = _fetch()
            log.info("NHCX session token refreshed; valid for %d s", int(_expires_at - time.time()))
        return _token


def invalidate() -> None:
    global _token, _expires_at
    with _lock:
        _token, _expires_at = None, 0.0


def bearer() -> str:
    s = get_settings()
    return f"Bearer {token() if s.auth_mode == 'session' else s.nhcx_token}"
