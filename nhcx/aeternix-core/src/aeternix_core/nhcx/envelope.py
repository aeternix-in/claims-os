"""HCX envelope for outgoing requests (plain or JWE mode, see config.envelope_mode).

Matches the nhcx-mock plain mode: {"payload": {"headers": {...}, "fhir": {...}}}.
The real sandbox expects "payload" to be a JWE string with these headers inside the
JWE protected header (alg RSA-OAEP-256, enc A256GCM) - added in a later step.
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..config import get_settings
from ..domain.models import ClaimCase


def hcx_timestamp(dt: datetime | None = None) -> str:
    # Sandbox sample format: 2023-11-06T13:22:06+0530
    return (dt or datetime.now(timezone.utc)).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


@dataclass
class Conversation:
    """IDs that stay constant across one exchange (request + callback)."""
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    workflow_id: str | None = None        # NHCX workflow code (12 new pre-auth, 15 new claim, ...)
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    last_api_call_id: str | None = None   # set by build_request (the payload may be encrypted)
    last_headers: dict | None = None


def build_request(case: ClaimCase, bundle: dict, conv: Conversation | None = None,
                  api_call_id: str | None = None, workflow_id: str | None = None) -> tuple[dict, Conversation]:
    """workflow_id defaults to the NHCX code for a NEW submission of the case's claim_type
    (12 pre-auth, 15 claim). Use codes.WORKFLOW_PREAUTH_RESUBMIT (121) to replace a pending pre-auth."""
    from .codes import WORKFLOW_SUBMIT
    conv = conv or Conversation()
    conv.workflow_id = workflow_id or conv.workflow_id or WORKFLOW_SUBMIT[case.claim_type.value]
    headers = {
        "x-hcx-api_call_id": api_call_id or str(uuid.uuid4()),
        "x-hcx-correlation_id": conv.correlation_id,
        "x-hcx-workflow_id": conv.workflow_id,
        "x-hcx-request_id": conv.request_id,
        "x-hcx-timestamp": hcx_timestamp(),
        "x-hcx-sender_code": case.hospital.nhcx_participant_code,
        "x-hcx-recipient_code": case.recipient_code,   # processingid (insurer or its TPA)
        "x-hcx-status": "request.initiated",
    }
    if case.patient.abha_number:
        headers["x-hcx-ben-abha-id"] = case.patient.abha_number
    conv.last_api_call_id = headers["x-hcx-api_call_id"]
    conv.last_headers = headers
    return wrap(headers, bundle), conv


def wrap(headers: dict, fhir: dict, callback: bool = False) -> dict:
    """Envelope body in the configured mode. callback=True adds "type": "JWEPayload"
    (the shape the sandbox uses for on_* calls)."""
    if get_settings().envelope_mode == "jwe":
        from .jose import encrypt
        body = {"payload": encrypt(headers, fhir)}
    else:
        body = {"payload": {"headers": headers, "fhir": fhir}}
    return {"type": "JWEPayload", **body} if callback else body


ENDPOINT = {"preauth": "/hcx/v1/preauth/submit", "claim": "/hcx/v1/claim/submit"}
