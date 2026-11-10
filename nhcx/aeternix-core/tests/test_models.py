from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from aeternix_core.domain.models import ClaimCase, Diagnosis, LineItem
from aeternix_core.samples import sample_preauth


def test_sample_is_valid_and_totals():
    case = sample_preauth()
    assert case.total == Decimal("70000")
    assert case.line_items[2].amount == Decimal("20000")


def rebuild(**changes) -> ClaimCase:
    data = sample_preauth().model_dump()
    data.update(changes)
    return ClaimCase.model_validate(data)


def test_bad_icd10_rejected():
    with pytest.raises(ValidationError, match="ICD-10"):
        Diagnosis(icd10_code="CARDIAC", description="x")


def test_icd10_normalised_to_upper():
    assert Diagnosis(icd10_code="i46.9", description="x").icd10_code == "I46.9"


def test_discharge_before_admission_rejected():
    case = sample_preauth()
    with pytest.raises(ValidationError, match="discharge_at"):
        rebuild(discharge_at=case.admission_at - timedelta(days=1))


def test_expired_policy_rejected():
    policy = sample_preauth().policy.model_dump()
    policy["valid_to"] = "2025-01-01"
    with pytest.raises(ValidationError, match="expired"):
        rebuild(policy=policy)


def test_needs_line_items_and_diagnoses():
    with pytest.raises(ValidationError):
        rebuild(line_items=[])
    with pytest.raises(ValidationError):
        rebuild(diagnoses=[])


def test_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        rebuild(surprise="field")


def test_money_must_be_positive_and_two_decimals():
    with pytest.raises(ValidationError):
        LineItem(snomed_code="77343006", description="x", unit_price=Decimal("-1"))
    with pytest.raises(ValidationError):
        LineItem(snomed_code="77343006", description="x", unit_price=Decimal("10.001"))


def test_same_participant_code_rejected():
    hospital = sample_preauth().hospital.model_dump()
    hospital["nhcx_participant_code"] = "mock-payer@hcx"
    with pytest.raises(ValidationError, match="participant"):
        rebuild(hospital=hospital)


def test_doctor_registration_na_rejected():
    from aeternix_core.domain.models import Doctor
    with pytest.raises(ValidationError, match="NA"):
        Doctor(name="Dr X", registration_number="NA")


def test_recipient_is_processing_code_when_tpa_handles():
    case = sample_preauth()
    assert case.recipient_code == "mock-payer@hcx"
    case.insurer.processing_code = "some-tpa@hcx"
    assert case.recipient_code == "some-tpa@hcx"
