"""HCX envelope handling.

Sandbox contract (from NHA's AWS(Sandbox) use-case Postman collection):
  action calls    : {"payload": "<JWE>"}
  callback calls  : {"type": "JWEPayload", "payload": "<JWE>"}
  JWE protected header carries alg/enc + x-hcx-* protocol headers.

PLAIN MODE (this mock): same body structure, but `payload` is a JSON object
    {"headers": {<x-hcx-* headers>}, "fhir": {<FHIR Bundle>}}
so switching to real JWE later only changes that one field.
"""
import uuid
from datetime import datetime, timezone

from . import jose
from .config import get_settings
from .errors import HcxError

REQUIRED_HEADERS = [
    "x-hcx-api_call_id",
    "x-hcx-correlation_id",
    "x-hcx-workflow_id",
    "x-hcx-request_id",
    "x-hcx-timestamp",
    "x-hcx-sender_code",
    "x-hcx-recipient_code",
    "x-hcx-status",
]
# Seen in the sandbox samples but not required by the generic HCX spec.
OPTIONAL_HEADERS = ["x-hcx-ben-abha-id"]


def now_ts() -> str:
    # Sandbox sample format: 2023-11-06T13:22:06+0530
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def unpack(body: dict, expected_status: str) -> tuple[dict, dict]:
    if "payload" not in body:
        raise HcxError(400, "ERR_INVALID_PAYLOAD", "Body must contain 'payload'")
    payload = body["payload"]
    if get_settings().envelope_mode == "jwe":
        if not isinstance(payload, str):
            raise HcxError(400, "ERR_INVALID_ENCRYPTION", "jwe mode: 'payload' must be a compact JWE string")
        headers = jose.protocol_headers(jose.read_protected(payload))
        _check_headers(headers, expected_status)
        # Gateway checks done on the readable header; the payer (recipient) decrypts.
        fhir = jose.decrypt(payload, headers["x-hcx-recipient_code"])
        if not isinstance(fhir, dict):
            raise HcxError(400, "ERR_INVALID_PAYLOAD", "decrypted payload is not a JSON object")
        return headers, fhir

    if isinstance(payload, str):
        raise HcxError(400, "ERR_INVALID_ENCRYPTION",
                       "JWE payloads are not supported in plain mode; send {'headers':..., 'fhir':...}")
    if not isinstance(payload, dict):
        raise HcxError(400, "ERR_INVALID_PAYLOAD", "'payload' must be an object in plain mode")

    headers, fhir = payload.get("headers"), payload.get("fhir")
    if not isinstance(headers, dict):
        raise HcxError(400, "ERR_INVALID_PAYLOAD", "payload.headers missing or not an object")
    if not isinstance(fhir, dict):
        raise HcxError(400, "ERR_INVALID_PAYLOAD", "payload.fhir missing or not an object")
    _check_headers(headers, expected_status)
    return headers, fhir


def _check_headers(headers: dict, expected_status: str) -> None:
    missing = [h for h in REQUIRED_HEADERS if not headers.get(h)]
    if missing:
        raise HcxError(400, "ERR_MANDATORY_HEADER_MISSING", f"Missing protocol headers: {missing}")
    for h in ("x-hcx-sender_code", "x-hcx-recipient_code"):
        if not str(headers[h]).endswith("@hcx"):
            raise HcxError(400, "ERR_INVALID_SENDER_OR_RECIPIENT", f"{h} must look like 'xxxxx@hcx'")
    if headers["x-hcx-sender_code"] == headers["x-hcx-recipient_code"]:
        raise HcxError(400, "ERR_INVALID_SENDER_OR_RECIPIENT", "sender and recipient must differ")
    if headers["x-hcx-status"] != expected_status:
        raise HcxError(400, "ERR_INVALID_STATUS", f"x-hcx-status must be '{expected_status}'")


def response_headers(request_headers: dict, status: str = "response.complete") -> dict:
    """Headers for the on_action leg: new api_call_id, ids preserved, sender/recipient swapped."""
    h = dict(request_headers)
    h["x-hcx-api_call_id"] = str(uuid.uuid4())
    h["x-hcx-timestamp"] = now_ts()
    h["x-hcx-status"] = status
    h["x-hcx-sender_code"] = request_headers["x-hcx-recipient_code"]
    h["x-hcx-recipient_code"] = request_headers["x-hcx-sender_code"]
    return h


def pack(headers: dict, fhir: dict, callback: bool = True) -> dict:
    if get_settings().envelope_mode == "jwe":
        body = {"payload": jose.encrypt(headers, fhir)}
    else:
        body = {"payload": {"headers": headers, "fhir": fhir}}
    if callback:
        body = {"type": "JWEPayload", **body}
    return body


def ack(headers: dict) -> dict:
    # ASSUMPTION: HCX-style 202 ack. No saved responses exist in NHA's collections.
    return {
        "timestamp": now_ts(),
        "api_call_id": headers.get("x-hcx-api_call_id"),
        "correlation_id": headers.get("x-hcx-correlation_id"),
    }
