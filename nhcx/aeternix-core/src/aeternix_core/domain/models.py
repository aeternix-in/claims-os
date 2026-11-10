"""Aeternix internal claim model.

Plain business terms, no FHIR vocabulary. This is what intake adapters (HIS export,
staff form, CSV) produce and what we store. The NHCX transformer turns it into FHIR.
"""
import re
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ICD10 = re.compile(r"^[A-Z][0-9]{2}(\.[0-9A-Z]{1,4})?$")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Gender(StrEnum):
    MALE = "male"
    FEMALE = "female"
    OTHER = "other"
    UNKNOWN = "unknown"


class Relationship(StrEnum):
    """Patient's relationship to the policy holder."""
    SELF = "self"
    SPOUSE = "spouse"
    CHILD = "child"
    PARENT = "parent"
    OTHER = "other"


class ClaimType(StrEnum):
    PREAUTH = "preauth"
    CLAIM = "claim"


class CareSetting(StrEnum):
    INPATIENT = "inpatient"
    OUTPATIENT = "outpatient"


class Patient(Model):
    name: str = Field(min_length=1)
    hospital_patient_id: str | None = None   # registration / UHID -> identifier type PI
    gender: Gender
    birth_date: date
    phone: str | None = None
    abha_number: str | None = None   # also sent in the x-hcx-ben-abha-id protocol header
    aadhaar_number: str | None = None


class Hospital(Model):
    name: str = Field(min_length=1)
    nhcx_participant_code: str = Field(pattern=r".+@hcx$")
    hfr_id: str = Field(min_length=1)  # Health Facility Registry ID -> identifier type NPI (required by NHCX)
    phone: str | None = None
    email: str | None = None


class Insurer(Model):
    name: str = Field(min_length=1)
    nhcx_participant_code: str = Field(pattern=r".+@hcx$")   # payerid -> identifier type NIIP
    # processingid from Get Policy: who actually handles the case (the insurer itself or its TPA).
    # Messages are addressed to it. Defaults to the insurer's own code.
    processing_code: str | None = Field(default=None, pattern=r".+@hcx$")
    phone: str | None = None
    email: str | None = None


class Doctor(Model):
    name: str = Field(min_length=1)
    registration_number: str = Field(min_length=1)  # HPR ID -> identifier type HPIN ("NA" is rejected)

    @field_validator("registration_number")
    @classmethod
    def not_na(cls, v: str) -> str:
        if v.strip().upper() in ("NA", "N/A"):
            raise ValueError("doctor registration number is required (NHCX rejects 'NA')")
        return v


class Policy(Model):
    # Product / care-plan code from Get Policy (IRDAI product code) -> Coverage identifier type NH
    product_code: str = Field(min_length=1)
    policy_number: str = Field(min_length=1)
    policy_number_system: str | None = None  # insurer's namespace URL, if known
    member_id: str = Field(min_length=1)
    relationship: Relationship = Relationship.SELF
    valid_from: date | None = None
    valid_to: date | None = None


class Diagnosis(Model):
    icd10_code: str
    description: str

    @field_validator("icd10_code")
    @classmethod
    def valid_icd10(cls, v: str) -> str:
        v = v.upper()
        if not ICD10.match(v):
            raise ValueError(f"not a valid ICD-10 code: {v!r}")
        return v


class Procedure(Model):
    snomed_code: str = Field(pattern=r"^\d{6,18}$")
    description: str


class LineItem(Model):
    snomed_code: str = Field(pattern=r"^\d{6,18}$")
    description: str
    unit_price: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    quantity: int = Field(default=1, ge=1)

    @property
    def amount(self) -> Decimal:
        return self.unit_price * self.quantity


class ClaimCase(Model):
    """One hospital case submitted to an insurer (pre-auth now, claim later)."""
    case_id: str = Field(min_length=1)
    claim_type: ClaimType
    care_setting: CareSetting = CareSetting.INPATIENT
    created_at: datetime
    admission_at: datetime
    discharge_at: datetime          # expected discharge for a pre-auth
    patient: Patient
    hospital: Hospital
    insurer: Insurer
    doctor: Doctor
    policy: Policy
    diagnoses: list[Diagnosis] = Field(min_length=1)
    procedures: list[Procedure] = Field(default_factory=list)
    line_items: list[LineItem] = Field(min_length=1)
    currency: str = Field(default="INR", pattern=r"^[A-Z]{3}$")
    hospital_reference: str | None = None   # hospital's own case number -> Claim identifier type CLN

    @property
    def case_number(self) -> str:
        """The case number (CLN) used on every NHCX message for this case."""
        return self.hospital_reference or self.case_id

    @property
    def recipient_code(self) -> str:
        return self.insurer.processing_code or self.insurer.nhcx_participant_code

    @property
    def total(self) -> Decimal:
        return sum((i.amount for i in self.line_items), Decimal("0"))

    @model_validator(mode="after")
    def check_consistency(self):
        if self.discharge_at < self.admission_at:
            raise ValueError("discharge_at must not be before admission_at")
        if self.policy.valid_to and self.admission_at.date() > self.policy.valid_to:
            raise ValueError("policy expired before admission")
        if self.hospital.nhcx_participant_code in (self.insurer.nhcx_participant_code,
                                                   self.insurer.processing_code):
            raise ValueError("hospital and insurer must have different NHCX participant codes")
        return self
