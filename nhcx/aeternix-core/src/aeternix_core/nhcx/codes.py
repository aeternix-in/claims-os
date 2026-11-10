"""Code systems and codes used in NHCX bundles.

Sources:
  [IG]  ABDM FHIR IG v6.5.0 example "ClaimBundle-preauthorization-example-01"
  [NHA] NHA "NHCX Implementation Guide for Payers" 2026-27 (identifier types, workflow ids,
        Claim.type codes). NHCX matches identifiers on type.coding.code.
Do not add codes from memory: add them only with a source noted next to them.
"""
from ..domain.models import CareSetting, ClaimType, Gender, Relationship

PROFILE = "https://nrces.in/ndhm/fhir/r4/StructureDefinition/"
NDHM_ID_TYPE = "https://nrces.in/ndhm/fhir/r4/CodeSystem/ndhm-identifier-type-code"
V2_0203 = "http://terminology.hl7.org/CodeSystem/v2-0203"
SNOMED = "http://snomed.info/sct"
ICD10 = "http://hl7.org/fhir/sid/icd-10"

SECURITY_VERY_RESTRICTED = {
    "system": "http://terminology.hl7.org/CodeSystem/v3-Confidentiality",
    "code": "V", "display": "very restricted",
}

CLAIM_USE = {ClaimType.PREAUTH: "preauthorization", ClaimType.CLAIM: "claim"}

CLAIM_TYPE = {   # [NHA] 737481003 inpatient, 737492002 outpatient (IP/OP is read from Claim.type)
    CareSetting.INPATIENT: {"system": SNOMED, "code": "737481003",
                            "display": "Inpatient care management (procedure)"},
    CareSetting.OUTPATIENT: {"system": SNOMED, "code": "737492002",
                             "display": "Outpatient care management (procedure)"},
}

PRIORITY_NORMAL = {"system": "http://terminology.hl7.org/CodeSystem/processpriority", "code": "normal"}

CARE_TEAM_ROLE = {"system": SNOMED, "code": "223366009", "display": "Healthcare professional (occupation)"}
CARE_TEAM_QUALIFICATION = {"system": SNOMED, "code": "394658006",
                           "display": "Clinical specialty (qualifier value)"}

DIAGNOSIS_TYPE_FINAL = {"system": SNOMED, "code": "89100005",
                        "display": "Final diagnosis (discharge) (contextual qualifier) (qualifier value)"}

ORG_TYPE_INSURER = {"system": "http://terminology.hl7.org/CodeSystem/organization-type",
                    "code": "ins", "display": "Insurance Company"}
ORG_TYPE_PROVIDER = {"system": "http://terminology.hl7.org/CodeSystem/organization-type",
                     "code": "prov", "display": "Healthcare Provider"}

# ---- Identifier types NHCX requires [NHA 3.3]. Codes are sourced; where the guide does not give
# the type's code system, the choice is marked ASSUMPTION (NHCX matches on the code).
ID_AADHAAR = {"type": {"system": NDHM_ID_TYPE, "code": "ADN", "display": "Adhaar number"},   # [IG]
              "system": "https://uidai.gov.in/"}
ID_PATIENT_INTERNAL = {"type": {"system": V2_0203, "code": "PI", "display": "Patient internal identifier"}}
ID_CASE_NUMBER = {"type": {"system": NDHM_ID_TYPE, "code": "CLN", "display": "Claim number"}}  # ASSUMPTION: system
ID_PROVIDER_HFR = {"type": {"system": V2_0203, "code": "NPI", "display": "National provider identifier"},
                   "system": "https://facility.ndhm.gov.in"}                                     # [IG] system
ID_PAYER = {"type": {"system": V2_0203, "code": "NIIP", "display": "National Insurance Payor Identifier (Payor)"},
            "system": "https://facility.abdm.gov.in"}                                            # [NHA 6.4] system
ID_PLAN_CODE = {"type": {"system": V2_0203, "code": "NH", "display": "National Health Plan Identifier"}}
ID_DOCTOR_HPR = {"type": {"system": NDHM_ID_TYPE, "code": "HPIN", "display": "Health Professional ID"},  # ASSUMPTION: system
                 "system": "https://doctor.ndhm.gov.in"}                                              # [IG] system

# ---- workflow_id codes [NHA 7.2 / 7.4 / 8.2]
WORKFLOW_SUBMIT = {"preauth": "12", "claim": "15"}           # new pre-auth / new claim
WORKFLOW_PREAUTH_RESUBMIT = "121"                            # replace a pending pre-auth (same CLN)
WORKFLOW_QUERY_REPLY = {"preauth": "19", "claim": "161"}     # hospital answers a document query
WORKFLOW_QUERY_ACK = "18"

COVERAGE_TYPE_HIP = {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                     "code": "HIP", "display": "health insurance plan policy"}
RELATIONSHIP_SYSTEM = "http://terminology.hl7.org/CodeSystem/subscriber-relationship"
# HL7 subscriber-relationship codes (example uses "self")
RELATIONSHIP = {Relationship.SELF: "self", Relationship.SPOUSE: "spouse",
                Relationship.CHILD: "child", Relationship.PARENT: "parent",
                Relationship.OTHER: "other"}

GENDER = {Gender.MALE: "male", Gender.FEMALE: "female", Gender.OTHER: "other", Gender.UNKNOWN: "unknown"}
