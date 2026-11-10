"""Sample cases (dummy data) for tests and demos.

sample_preauth() carries the same clinical/financial content as the official NRCeS
example ClaimBundle-preauthorization-example-01 (angiography + angioplasty + 10 days ICU,
total INR 70,000), expressed in Aeternix's internal model. All people, IDs and numbers
are the IG's sample values or made up - never real patient data.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from .domain.models import (ClaimCase, ClaimType, Diagnosis, Doctor, Gender, Hospital, Insurer,
                            LineItem, Patient, Policy, Procedure, Relationship)

IST = timezone(timedelta(hours=5, minutes=30))


def sample_preauth(case_id: str = "AETX-CASE-0001") -> ClaimCase:
    return ClaimCase(
        case_id=case_id,
        claim_type=ClaimType.PREAUTH,
        hospital_reference="7612345",
        created_at=datetime(2025, 3, 8, 11, 1, tzinfo=IST),
        admission_at=datetime(2025, 3, 7, 11, 1, tzinfo=IST),
        discharge_at=datetime(2025, 4, 7, 11, 1, tzinfo=IST),
        patient=Patient(name="Adarsh Agrawal", hospital_patient_id="UHID-0042", gender=Gender.MALE, birth_date=date(1981, 1, 12),
                        phone="+919818512600", abha_number="91-0000-0000-0001",
                        aadhaar_number="7225-4829-5255"),
        hospital=Hospital(name="XYZ Hospital Co. Ltd.", nhcx_participant_code="aeternix-provider@hcx",
                          hfr_id="45675454", phone="+91 243 2634 1278", email="contact@xyz.org"),
        insurer=Insurer(name="XYZ Insurance Co. Ltd.", nhcx_participant_code="mock-payer@hcx",
                        phone="+91 243 2634 1234", email="contact@xyz.org"),
        doctor=Doctor(name="Dr. Aayush Agrawal", registration_number="21-1521-3828-3227"),
        policy=Policy(product_code="XYZHLIP21001V012021", policy_number="235037120", policy_number_system="https://xyzinsurance.in/policynumber/",
                      member_id="ABC123456BI007", relationship=Relationship.SELF,
                      valid_to=date(2026, 3, 17)),
        diagnoses=[Diagnosis(icd10_code="I46.9", description="Cardiac arrest, unspecified")],
        procedures=[Procedure(snomed_code="77343006", description="Angiography"),
                    Procedure(snomed_code="418285008", description="Angioplasty of blood vessel")],
        line_items=[
            LineItem(snomed_code="77343006", description="Angiography", unit_price=Decimal("10000")),
            LineItem(snomed_code="418285008", description="Angioplasty of blood vessel",
                     unit_price=Decimal("40000")),
            LineItem(snomed_code="309904001", description="Intensive care unit",
                     unit_price=Decimal("2000"), quantity=10),
        ],
    )
