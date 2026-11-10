import asyncio
import logging

import httpx

from . import db
from .config import get_settings

log = logging.getLogger("nhcx_mock.callbacks")

# Tests replace this with httpx.ASGITransport(app=app) so callbacks hit the in-process app.
transport: httpx.AsyncBaseTransport | None = None

GATEWAY_TOKEN = "Bearer mock-gateway-token"


async def deliver(path: str, body: dict, txn_id: int) -> bool:
    """POST a callback with retries; record attempts and outcome on the transaction."""
    settings = get_settings()
    url = settings.callback_base_url.rstrip("/") + path
    await asyncio.sleep(settings.callback_delay_seconds)

    last_error = None
    for attempt in range(1, settings.callback_max_attempts + 1):
        try:
            async with httpx.AsyncClient(transport=transport, timeout=10) as client:
                r = await client.post(url, json=body, headers={"bearer_auth": GATEWAY_TOKEN})
            if r.status_code < 300:
                _record(txn_id, attempt, "delivered", None)
                log.info("callback delivered txn=%s url=%s attempt=%s", txn_id, url, attempt)
                return True
            last_error = f"HTTP {r.status_code}: {r.text[:200]}"
        except httpx.HTTPError as e:
            last_error = f"{type(e).__name__}: {e}"
        log.warning("callback failed txn=%s url=%s attempt=%s error=%s", txn_id, url, attempt, last_error)
        _record(txn_id, attempt, "retrying", last_error)
        if attempt < settings.callback_max_attempts and settings.callback_delay_seconds > 0:
            await asyncio.sleep(min(2 ** (attempt - 1), 10))

    _record(txn_id, settings.callback_max_attempts, "failed", last_error)
    return False


def _record(txn_id: int, attempts: int, status: str, error: str | None) -> None:
    with db.session() as s:
        txn = s.get(db.Transaction, txn_id)
        txn.callback_attempts = attempts
        txn.callback_status = status
        txn.last_error = error
        s.commit()
