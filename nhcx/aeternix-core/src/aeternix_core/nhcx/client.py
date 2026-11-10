"""HTTP client for the NHCX gateway (the mock locally, the sandbox later)."""
import httpx

from ..config import get_settings
from . import session

# Tests replace this with httpx.ASGITransport(app=mock_app).
transport: httpx.AsyncBaseTransport | None = None


async def post(path: str, body: dict | None = None, extra_headers: dict | None = None) -> httpx.Response:
    """POST to NHCX with the sandbox's auth header. Raises httpx.HTTPError on network failure.

    Headers match NHA's Postman collection (Accept, Content-Type, bearer_auth): without Accept
    the sandbox answers NHCX-1060 "Mandatory headers are missing" (seen 2026-10-13).
    In session mode a 401 refreshes the token and retries once (NHA guide checkpoint 06)."""
    s = get_settings()

    async def send() -> httpx.Response:
        headers = {"Accept": "application/json", "bearer_auth": session.bearer(), **(extra_headers or {})}
        async with httpx.AsyncClient(base_url=s.nhcx_base_url, transport=transport,
                                     timeout=s.nhcx_timeout_seconds) as client:
            return await client.post(path, json=body, headers=headers)

    try:
        r = await send()
    except session.SessionError as e:
        raise httpx.HTTPError(f"no NHCX session: {e}") from e
    if r.status_code == 401 and s.auth_mode == "session":
        session.invalidate()
        r = await send()
    return r
