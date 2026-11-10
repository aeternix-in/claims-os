"""Parse messages NHCX sends to us (callbacks and payer-initiated requests)."""
import uuid

from ..config import get_settings
from . import jose
from .envelope import hcx_timestamp

REQUIRED = ["x-hcx-api_call_id", "x-hcx-correlation_id", "x-hcx-workflow_id", "x-hcx-request_id",
            "x-hcx-timestamp", "x-hcx-sender_code", "x-hcx-recipient_code", "x-hcx-status"]


class InboundError(Exception):
    """Becomes an HCX-style error response to the sender."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def parse(body: dict, expected_status: str) -> tuple[dict, dict]:
    payload = body.get("payload")
    if get_settings().envelope_mode == "jwe":
        if not isinstance(payload, str):
            raise InboundError(400, "ERR_INVALID_ENCRYPTION", "expected a JWE payload")
        try:
            headers = jose.read_protected(payload)
        except jose.JoseError as e:
            raise InboundError(400, "ERR_INVALID_ENCRYPTION", str(e)) from e
        _check(headers, expected_status)
        try:
            fhir = jose.decrypt(payload, headers["x-hcx-recipient_code"])
        except jose.JoseError as e:
            raise InboundError(400, "ERR_INVALID_ENCRYPTION", str(e)) from e
        if not isinstance(fhir, dict):
            raise InboundError(400, "ERR_INVALID_PAYLOAD", "decrypted payload is not a JSON object")
        return headers, fhir

    if isinstance(payload, str):
        raise InboundError(400, "ERR_INVALID_ENCRYPTION", "JWE payload received but envelope_mode is plain")
    if not isinstance(payload, dict):
        raise InboundError(400, "ERR_INVALID_PAYLOAD", "missing or invalid 'payload'")
    headers, fhir = payload.get("headers"), payload.get("fhir")
    if not isinstance(headers, dict) or not isinstance(fhir, dict):
        raise InboundError(400, "ERR_INVALID_PAYLOAD", "payload must contain 'headers' and 'fhir' objects")
    _check(headers, expected_status)
    return headers, fhir


def _check(headers: dict, expected_status: str) -> None:
    missing = [h for h in REQUIRED if not headers.get(h)]
    if missing:
        raise InboundError(400, "ERR_MANDATORY_HEADER_MISSING", f"missing protocol headers: {missing}")
    if headers["x-hcx-status"] != expected_status:
        raise InboundError(400, "ERR_INVALID_STATUS", f"x-hcx-status must be '{expected_status}'")


def reply_headers(received: dict) -> dict:
    """Headers for our response leg: new api_call_id, conversation ids kept, roles swapped."""
    return {**received,
            "x-hcx-api_call_id": str(uuid.uuid4()),
            "x-hcx-timestamp": hcx_timestamp(),
            "x-hcx-status": "response.complete",
            "x-hcx-sender_code": received["x-hcx-recipient_code"],
            "x-hcx-recipient_code": received["x-hcx-sender_code"]}


def ack(headers: dict) -> dict:
    return {"timestamp": hcx_timestamp(), "api_call_id": headers.get("x-hcx-api_call_id"),
            "correlation_id": headers.get("x-hcx-correlation_id")}
