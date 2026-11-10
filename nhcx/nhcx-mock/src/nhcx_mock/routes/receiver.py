"""Built-in stand-in for Aeternix's callback endpoints, so the loop works on day one.

Point NHCX_MOCK_CALLBACK_BASE_URL at Aeternix once it exposes the same paths.
"""
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Request

router = APIRouter(prefix="/receiver", tags=["Built-in callback receiver"])
INBOX: list[dict] = []


@router.post("/hcx/v1/{workflow}/{action}", status_code=202)
async def receive(workflow: str, action: str, request: Request, body: dict[str, Any] = Body(...)):
    INBOX.append({
        "path": f"/hcx/v1/{workflow}/{action}",
        "received_at": datetime.now(timezone.utc).isoformat(),
        "bearer_auth": request.headers.get("bearer_auth"),
        "body": body,
    })
    return {"received": True}


@router.get("/inbox")
def inbox():
    return INBOX


@router.delete("/inbox")
def clear_inbox():
    INBOX.clear()
    return {"cleared": True}
