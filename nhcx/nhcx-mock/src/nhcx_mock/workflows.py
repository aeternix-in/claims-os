from dataclasses import dataclass


@dataclass(frozen=True)
class Workflow:
    name: str            # URL segment, e.g. "preauth"
    action: str          # e.g. "submit"
    on_action: str       # e.g. "on_submit"
    request_resource: str
    claim_use: str | None = None  # Claim.use value expected for Claim-based flows


# Provider -> HCX -> payer flows (Aeternix initiates, mock answers via callback)
PROVIDER_INITIATED = {
    "preauth": Workflow("preauth", "submit", "on_submit", "Claim", "preauthorization"),
    "claim": Workflow("claim", "submit", "on_submit", "Claim", "claim"),
}

# Payer -> HCX -> provider flow (mock initiates, Aeternix acknowledges)
# NOTE: direction inferred from HCX semantics (payer sends PaymentNotice); confirm on sandbox.
PAYMENT_NOTICE = Workflow("paymentnotice", "request", "on_request", "PaymentNotice")

# Payer -> provider document query (NHA guide ch.8): CommunicationRequest, answered on communication/on_request
COMMUNICATION = Workflow("communication", "request", "on_request", "CommunicationRequest")
QUERY_WORKFLOW_ID = {"preauth": "24", "claim": "27"}
