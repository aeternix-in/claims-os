"""Transformer: Aeternix ClaimCase -> NHCX ClaimBundle (ABDM FHIR IG v6.5.0).

Structure follows the official NRCeS example "ClaimBundle-preauthorization-example-01";
identifiers follow NHA's NHCX Implementation Guide (CLN, NPI, NIIP, NH, HPIN, PI/ADN),
which NHCX's processing requires and the NRCeS example does not use.
Layout: a collection Bundle with the Claim first,
then Patient, insurer Organization, provider Organization, Practitioner and Coverage,
all linked by urn:uuid references.

Resource ids are deterministic (uuid5 of case_id + role), so transforming the same
case twice yields the same references - useful for retries and diffing.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from ..domain.models import ClaimCase
from . import codes as c

NAMESPACE = uuid.UUID("6f1c6b8e-0d1a-4b8e-9a51-3c7a2e9d4f10")  # Aeternix fixed namespace


def _rid(case: ClaimCase, role: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{case.case_id}:{role}"))


def _ref(rid: str, display: str) -> dict:
    return {"reference": f"urn:uuid:{rid}", "display": display}


def _money(value: Decimal, currency: str) -> dict:
    # FHIR JSON decimals: emit integers as ints (70000, not 70000.0)
    v = int(value) if value == value.to_integral_value() else float(value)
    return {"value": v, "currency": currency}


def _cc(coding: dict) -> dict:
    return {"coding": [coding]}


def _ident(template: dict, value: str) -> dict:
    out = {}
    if "type" in template:
        out["type"] = _cc(template["type"])
    if template.get("system"):
        out["system"] = template["system"]
    out["value"] = value
    return out


def _telecom(phone: str | None, email: str | None, use: str) -> list[dict]:
    t = []
    if phone:
        t.append({"system": "phone", "value": phone, "use": use})
    if email:
        t.append({"system": "email", "value": email, "use": use})
    return t


def to_claim_bundle(case: ClaimCase, submission_id: str | None = None,
                    timestamp: datetime | None = None) -> dict:
    """Build the NHCX ClaimBundle for a case.

    submission_id becomes Bundle.identifier.value (one per submission attempt);
    defaults to a fresh UUID.
    """
    ids = {role: _rid(case, role) for role in
           ("claim", "patient", "insurer", "provider", "practitioner", "coverage")}
    ts = (timestamp or datetime.now(timezone.utc)).isoformat(timespec="milliseconds")
    cur = case.currency

    patient = {
        "resourceType": "Patient", "id": ids["patient"],
        "meta": {"profile": [c.PROFILE + "Patient"]},
        "name": [{"text": case.patient.name}],
        "gender": c.GENDER[case.patient.gender],
        "birthDate": case.patient.birth_date.isoformat(),
    }
    # [NHA 3.3] PI = case / registration reference, ADN = Aadhaar
    patient["identifier"] = [_ident(c.ID_PATIENT_INTERNAL, case.patient.hospital_patient_id or case.case_number)]
    if case.patient.aadhaar_number:
        patient["identifier"].append(_ident(c.ID_AADHAAR, case.patient.aadhaar_number))
    # ABHA travels in the x-hcx-ben-abha-id protocol header (no Patient identifier type is sourced for it).
    if case.patient.phone:
        patient["telecom"] = [{"system": "phone", "value": case.patient.phone, "use": "home"}]

    insurer = {
        "resourceType": "Organization", "id": ids["insurer"],
        "meta": {"profile": [c.PROFILE + "Organization"]},
        "identifier": [_ident(c.ID_PAYER, case.insurer.nhcx_participant_code)],   # [NHA] NIIP + name
        "type": [_cc(c.ORG_TYPE_INSURER)],
        "name": case.insurer.name,
    }
    if tel := _telecom(case.insurer.phone, case.insurer.email, "work"):
        insurer["telecom"] = tel

    provider = {
        "resourceType": "Organization", "id": ids["provider"],
        "meta": {"profile": [c.PROFILE + "Organization"]},
        "identifier": [_ident(c.ID_PROVIDER_HFR, case.hospital.hfr_id)],   # [NHA] NPI (HFR) + name
        "type": [_cc(c.ORG_TYPE_PROVIDER)],
        "name": case.hospital.name,
    }
    if tel := _telecom(case.hospital.phone, case.hospital.email, "work"):
        provider["telecom"] = tel

    practitioner = {
        "resourceType": "Practitioner", "id": ids["practitioner"],
        "meta": {"profile": [c.PROFILE + "Practitioner"]},
        "identifier": [_ident(c.ID_DOCTOR_HPR, case.doctor.registration_number)],   # [NHA] HPIN
        "name": [{"text": case.doctor.name}],
    }

    pol = case.policy
    coverage = {
        "resourceType": "Coverage", "id": ids["coverage"],
        "meta": {"profile": [c.PROFILE + "Coverage"]},
        "identifier": [
            _ident(c.ID_PLAN_CODE, pol.product_code),                       # [NHA] NH = product / care-plan
            {"value": pol.policy_number,
             **({"system": pol.policy_number_system} if pol.policy_number_system else {})},
        ],
        "status": "active",
        "type": _cc(c.COVERAGE_TYPE_HIP),
        "subscriber": _ref(ids["patient"], "Patient"),
        "subscriberId": pol.member_id,
        "beneficiary": _ref(ids["patient"], "Patient"),
        "relationship": {"coding": [{"system": c.RELATIONSHIP_SYSTEM, "code": c.RELATIONSHIP[pol.relationship]}]},
        "payor": [_ref(ids["insurer"], "Organization")],
    }
    if pol.valid_from or pol.valid_to:
        coverage["period"] = {k: v.isoformat() for k, v in
                              (("start", pol.valid_from), ("end", pol.valid_to)) if v}

    items = []
    for n, li in enumerate(case.line_items, start=1):
        item = {
            "sequence": n,
            "careTeamSequence": [1],
            "productOrService": _cc({"system": c.SNOMED, "code": li.snomed_code, "display": li.description}),
            "unitPrice": _money(li.unit_price, cur),
            "net": _money(li.amount, cur),
        }
        if li.quantity != 1:
            item["quantity"] = {"value": li.quantity}
        items.append(item)

    claim = {
        "resourceType": "Claim", "id": ids["claim"],
        "meta": {"profile": [c.PROFILE + "Claim"]},
        "identifier": [_ident(c.ID_CASE_NUMBER, case.case_number)],          # [NHA] CLN
        "status": "active",
        "type": _cc(c.CLAIM_TYPE[case.care_setting]),
        "use": c.CLAIM_USE[case.claim_type],
        "patient": _ref(ids["patient"], "Patient"),
        "billablePeriod": {"start": case.admission_at.isoformat(), "end": case.discharge_at.isoformat()},
        "created": case.created_at.isoformat(),
        "insurer": _ref(ids["insurer"], "Organization"),
        "provider": _ref(ids["provider"], "Organization"),
        "priority": _cc(c.PRIORITY_NORMAL),
        "careTeam": [{
            "sequence": 1,
            "provider": _ref(ids["practitioner"], "Practitioner"),
            "role": _cc(c.CARE_TEAM_ROLE),
            "qualification": _cc(c.CARE_TEAM_QUALIFICATION),
        }],
        "diagnosis": [{
            "sequence": n,
            "diagnosisCodeableConcept": _cc({"system": c.ICD10, "code": d.icd10_code, "display": d.description}),
            # TODO(diagnosis-type): only "final" is sourced from the example; provisional/admitting
            # diagnosis types need a confirmed code before being added.
            "type": [_cc(c.DIAGNOSIS_TYPE_FINAL)],
        } for n, d in enumerate(case.diagnoses, start=1)],
        "insurance": [{"sequence": 1, "focal": True, "coverage": _ref(ids["coverage"], "Coverage")}],
        "item": items,
        "total": _money(case.total, cur),
    }
    if case.procedures:
        claim["procedure"] = [{
            "sequence": n,
            "procedureCodeableConcept": _cc({"system": c.SNOMED, "code": p.snomed_code, "display": p.description}),
        } for n, p in enumerate(case.procedures, start=1)]

    resources = [claim, patient, insurer, provider, practitioner, coverage]
    return {
        "resourceType": "Bundle",
        "id": str(uuid.uuid4()),
        "meta": {"profile": [c.PROFILE + "ClaimBundle"], "security": [c.SECURITY_VERY_RESTRICTED]},
        "identifier": {"value": submission_id or str(uuid.uuid4())},
        "type": "collection",
        "timestamp": ts,
        "entry": [{"fullUrl": f"urn:uuid:{r['id']}", "resource": r} for r in resources],
    }
