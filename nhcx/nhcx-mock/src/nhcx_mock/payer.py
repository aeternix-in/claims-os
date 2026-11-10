"""Mock payer: turns a submitted ClaimBundle into a ClaimResponseBundle.

Response shape follows the official NHCX example
"ClaimResponseBundle-preauthorization-example-01" (ABDM FHIR IG v6.5.0):
  - Bundle (collection) with meta.profile ClaimResponseBundle + meta.security "V"
  - Bundle.identifier echoes the request bundle identifier (as in the example)
  - entry[0] = ClaimResponse; remaining entries echo the request bundle (Claim, Patient,
    Organizations, Practitioner, Coverage), so every reference resolves inside the bundle
  - ClaimResponse.request references the Claim entry's fullUrl
  - per-item adjudication: "submitted" + "eligpercent" (with ndhm reason "covered")
  - totals: "submitted" + "eligpercent"; preAuthRef + preAuthPeriod for approved pre-auths
Not reproduced: formCode/form (the example attaches a base64 approval-letter image).
"""
import copy
import uuid
from datetime import datetime, timedelta, timezone

from .fhir_check import find_entry, find_resources

SCENARIOS = {"approve", "partial", "reject", "query", "timeout", "callback_fail"}

PROFILE_BASE = "https://nrces.in/ndhm/fhir/r4/StructureDefinition/"
ADJ = "http://terminology.hl7.org/CodeSystem/adjudication"
NDHM_REASON = "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-adjudication-reason"
SECURITY_V = {"system": "http://terminology.hl7.org/CodeSystem/v3-Confidentiality",
              "code": "V", "display": "very restricted"}

# scenario -> (outcome, disposition, eligible fraction or None, transaction status)
# ASSUMPTION: no official rejection/query example exists in the IG; "error"/"partial"
# outcomes and the dispositions below are mock choices.
_DECISIONS = {
    "approve": ("complete", "The enclosed services are authorized for your provision "
                            "within 30 days of this notice.", 1.0, "approved"),
    "callback_fail": ("complete", "The enclosed services are authorized for your provision "
                                  "within 30 days of this notice.", 1.0, "approved"),
    "partial": ("complete", "Partially approved: eligible amount reduced as per policy limits.",
                0.5, "partially_approved"),
    "reject": ("error", "Rejected: requested services are not covered under the policy.",
               None, "rejected"),
    "query": ("partial", "Additional information required before adjudication.",
              None, "query_raised"),
}


def _now() -> datetime:
    return datetime.now(timezone(timedelta(hours=5, minutes=30)))


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


def _money(value: float, currency: str) -> dict:
    v = round(value, 2)
    return {"value": int(v) if v == int(v) else v, "currency": currency}


def _cat(code: str, display: str) -> dict:
    return {"coding": [{"system": ADJ, "code": code, "display": display}]}


COVERED = {"coding": [{"system": NDHM_REASON, "code": "covered",
                       "display": "Product or Service requested is covered by the policy."}]}


def decide(request_bundle: dict, scenario: str) -> tuple[dict, str]:
    outcome, disposition, fraction, status = _DECISIONS[scenario]
    claim_entry = find_entry(request_bundle, "Claim")
    claim = claim_entry["resource"]
    currency = (claim.get("total") or {}).get("currency", "INR")
    now = _now()

    items, submitted_total, eligible_total = [], 0.0, 0.0
    for item in claim.get("item") or []:
        net = (item.get("net") or {}).get("value")
        if net is None:
            continue
        adjudication = [{"category": _cat("submitted", "Submitted Amount"),
                         "amount": _money(net, currency)}]
        submitted_total += net
        if fraction is not None:
            eligible = net * fraction
            eligible_total += eligible
            adjudication.append({"category": _cat("eligpercent", "Eligible %"),
                                 "reason": COVERED, "amount": _money(eligible, currency)})
            adjudication.append({"category": _cat("benefit", "Benefit Amount"),
                                 "amount": _money(eligible, currency)})
        items.append({"itemSequence": item.get("sequence"), "adjudication": adjudication})

    cr_id = str(uuid.uuid4())
    claim_response = {
        "resourceType": "ClaimResponse",
        "id": cr_id,
        "meta": {"profile": [PROFILE_BASE + "ClaimResponse"]},
        "status": "active",
        "type": claim.get("type"),
        "use": claim.get("use"),
        "patient": claim.get("patient"),
        "created": _iso(now),
        "insurer": claim.get("insurer"),
        "requestor": claim.get("provider"),
        "request": {"reference": claim_entry["fullUrl"]},
        "outcome": outcome,
        "disposition": disposition,
        "payeeType": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/payeetype",
                                  "code": "provider"}]},
    }
    if claim.get("use") == "preauthorization" and fraction is not None:
        claim_response["preAuthRef"] = f"PA-{cr_id[:8].upper()}"
        claim_response["preAuthPeriod"] = {"start": _iso(now), "end": _iso(now + timedelta(days=31))}
    if items:
        claim_response["item"] = items
        totals = [{"category": _cat("submitted", "Submitted Amount"),
                   "amount": _money(submitted_total, currency)}]
        if fraction is not None:
            totals.append({"category": _cat("eligpercent", "Eligible %"),
                           "amount": _money(eligible_total, currency)})
            totals.append({"category": _cat("benefit", "Benefit Amount"),
                           "amount": _money(eligible_total, currency)})
        claim_response["total"] = totals
    if claim.get("insurance"):
        claim_response["insurance"] = claim["insurance"]

    bundle = {
        "resourceType": "Bundle",
        "id": str(uuid.uuid4()),
        "meta": {"profile": [PROFILE_BASE + "ClaimResponseBundle"], "security": [SECURITY_V]},
        "identifier": copy.deepcopy(request_bundle.get("identifier")) or {"value": str(uuid.uuid4())},
        "type": "collection",
        "timestamp": _iso(now),
        "entry": [{"fullUrl": f"urn:uuid:{cr_id}", "resource": claim_response}]
                 + copy.deepcopy(request_bundle["entry"]),
    }
    return bundle, status


def eligible_amount(claim_response: dict) -> dict | None:
    """Total 'eligpercent' amount from a ClaimResponse (what the payer will pay)."""
    for t in claim_response.get("total") or []:
        codes = [c.get("code") for c in (t.get("category") or {}).get("coding", [])]
        if "eligpercent" in codes:
            return t.get("amount")
    return None


NDHM_ID_TYPE = "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-identifier-type-code"
QUERY_TEXT = {"preauth": "Please share the ECG report and the cardiologist's notes supporting the admission.",
              "claim": "Please share the final bill breakup and the discharge summary signed by the treating doctor."}


def case_number(request_bundle: dict) -> str | None:
    """Claim.identifier of type CLN (NHA guide), as the hospital sent it."""
    claim = find_resources(request_bundle, "Claim")[0]
    for i in claim.get("identifier") or []:
        if any(c.get("code") == "CLN" for c in (i.get("type") or {}).get("coding", [])):
            return i.get("value")
    return None


def communication_request(request_bundle: dict, workflow: str) -> dict:
    """Insurer's document query (NHA guide ch.8): a CommunicationRequest carrying the hospital's CLN.
    ASSUMPTION: shape follows R4 CommunicationRequest; the guide's example is not reproduced."""
    claim_entry = find_entry(request_bundle, "Claim")
    cr_id = str(uuid.uuid4())
    req = {
        "resourceType": "CommunicationRequest", "id": cr_id,
        "meta": {"profile": [PROFILE_BASE + "CommunicationRequest"]},
        "identifier": [{"type": {"coding": [{"system": NDHM_ID_TYPE, "code": "CLN", "display": "Claim number"}]},
                        "value": case_number(request_bundle)}],
        "status": "active", "about": [{"reference": claim_entry["fullUrl"]}],
        "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/communication-category",
                                  "code": "instruction"}]}],
        "payload": [{"contentString": QUERY_TEXT[workflow]}],
        "authoredOn": _iso(_now()),
    }
    return {"resourceType": "Bundle", "id": str(uuid.uuid4()), "type": "collection", "timestamp": _iso(_now()),
            "entry": [{"fullUrl": f"urn:uuid:{cr_id}", "resource": req}]}


def payment_notice(response_bundle: dict) -> dict:
    """PaymentNotice for an approved claim.

    NOTE: still a minimal R4 PaymentNotice; not yet aligned with the official NHCX
    PaymentNotice example (next step).
    """
    claim_response = find_resources(response_bundle, "ClaimResponse")[0]
    claim_entry = find_entry(response_bundle, "Claim")
    claim = claim_entry["resource"]
    amount = eligible_amount(claim_response) or {"value": 0, "currency": "INR"}
    pn_id = str(uuid.uuid4())
    notice = {
        "resourceType": "PaymentNotice",
        "id": pn_id,
        "meta": {"profile": [PROFILE_BASE + "PaymentNotice"]},
        "status": "active",
        "request": {"reference": claim_entry["fullUrl"]},
        "response": {"reference": f"urn:uuid:{claim_response['id']}"},
        "created": _iso(_now()),
        "provider": claim.get("insurer"),
        "payment": {"reference": f"PaymentReconciliation/{uuid.uuid4()}"},
        "paymentDate": _now().date().isoformat(),
        "payee": claim.get("provider"),
        "recipient": claim.get("provider"),
        "amount": amount,
        "paymentStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/paymentstatus",
                                      "code": "paid"}]},
    }
    rec_id = str(uuid.uuid4())
    notice["payment"] = {"reference": f"urn:uuid:{rec_id}"}
    utr = f"UTR{uuid.uuid4().int % 10**12:012d}"
    reconciliation = {   # carries the bank UTR the hospital will see on its statement
        "resourceType": "PaymentReconciliation", "id": rec_id, "status": "active",
        "identifier": [{"type": {"coding": [{"system": NDHM_ID_TYPE, "code": "UTR", "display": "Unique transaction reference"}]},
                        "value": utr}],
        "created": _iso(_now()), "paymentDate": _now().date().isoformat(), "paymentAmount": amount,
        "outcome": "complete",
    }
    return {
        "resourceType": "Bundle",
        "id": str(uuid.uuid4()),
        "type": "collection",
        "timestamp": _iso(_now()),
        "entry": [{"fullUrl": f"urn:uuid:{pn_id}", "resource": notice},
                  {"fullUrl": f"urn:uuid:{rec_id}", "resource": reconciliation}],
    }
