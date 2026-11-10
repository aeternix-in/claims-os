"""Hospital setup, payer identity and per-case details (incl. the identifiers NHCX needs)."""
import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, field_validator

from .auth import User, current_user
from .db import tx

router = APIRouter(tags=["setup"])
HCX = re.compile(r".+@hcx$")
ICD10 = re.compile(r"^[A-Z][0-9]{2}(\.[0-9A-Z]{1,4})?$")


def _hcx(v):
    if v and not HCX.match(v):
        raise ValueError("NHCX participant codes end in @hcx")
    return v or None


class HospitalIn(BaseModel):
    name: str = Field(min_length=2)
    city: str | None = None
    beds: int | None = Field(default=None, ge=1)
    address: str | None = None
    letter_signatory: str | None = None
    hfr_id: str | None = None
    nhcx_participant_code: str | None = None
    rooms: dict[str, float] | None = None
    differential_billing: bool | None = None
    preauth_sla_min: int | None = Field(default=None, ge=1)
    discharge_sla_min: int | None = Field(default=None, ge=1)
    _v = field_validator("nhcx_participant_code")(lambda cls, v: _hcx(v))


@router.get("/hospital")
def get_hospital(user: User = Depends(current_user)):
    with tx() as c:
        return c.execute("select * from hospitals where id=%s", (user.hospital_id,)).fetchone()


@router.put("/hospital")
def put_hospital(body: HospitalIn, user: User = Depends(current_user)):
    if user.role not in ("owner", "admin"):
        raise HTTPException(403, "Only the owner or an admin changes hospital setup")
    with tx() as c:
        h = c.execute("select settings from hospitals where id=%s for update", (user.hospital_id,)).fetchone()
        st = dict(h["settings"])
        if body.rooms is not None:
            bad = [k for k in body.rooms if k not in ("general", "twin", "single", "deluxe", "icu")]
            if bad or any(v <= 0 for v in body.rooms.values()):
                raise HTTPException(422, "Room tariff: general, twin, single, deluxe, icu, each above zero")
            st["rooms"] = {**st.get("rooms", {}), **body.rooms}
        if body.differential_billing is not None:
            st["differentialBilling"] = body.differential_billing
        if body.preauth_sla_min:
            st["preauthSlaMin"] = body.preauth_sla_min
        if body.discharge_sla_min:
            st["dischargeSlaMin"] = body.discharge_sla_min
        return c.execute("""update hospitals set name=%s, city=%s, beds=%s, address=%s, letter_signatory=%s, hfr_id=%s,
                            nhcx_participant_code=%s, settings=%s where id=%s returning *""",
                         (body.name, body.city, body.beds, body.address, body.letter_signatory, body.hfr_id or None,
                          body.nhcx_participant_code, Jsonb(st), user.hospital_id)).fetchone()


class PayerIn(BaseModel):
    name: str = Field(min_length=1)
    via_tpa: str | None = None
    nhcx_participant_code: str | None = None
    nhcx_processing_code: str | None = None
    _v = field_validator("nhcx_participant_code", "nhcx_processing_code")(lambda cls, v: _hcx(v))


@router.put("/payers/{payer_id}")
def put_payer(payer_id: str, body: PayerIn, user: User = Depends(current_user)):
    if user.role not in ("owner", "admin", "accounts"):
        raise HTTPException(403, "Only the owner, admin or accounts edits payers")
    with tx() as c:
        r = c.execute("""update payers set name=%s, via_tpa=%s, nhcx_participant_code=%s, nhcx_processing_code=%s
                         where id=%s and hospital_id=%s returning *""",
                      (body.name, body.via_tpa, body.nhcx_participant_code, body.nhcx_processing_code, payer_id, user.hospital_id)).fetchone()
    if not r:
        raise HTTPException(404, "Payer not found")
    return r


class DetailsIn(BaseModel):
    """Anything here may be omitted; only what is sent changes."""
    patient_dob: date | None = None
    patient_sex: str | None = Field(default=None, pattern="^(M|F|O)$")
    patient_abha: str | None = None
    treating_doctor: str | None = None
    doctor_hpr_id: str | None = None
    icd10: str | None = None
    provisional_dx: str | None = None
    product_code: str | None = None
    member_id: str | None = None
    relationship: str | None = Field(default=None, pattern="^(self|spouse|child|parent|other)$")
    policy_valid_to: date | None = None

    @field_validator("icd10")
    @classmethod
    def icd(cls, v):
        if v and not ICD10.match(v.upper()):
            raise ValueError("not a valid ICD-10 code")
        return v.upper() if v else v

    @field_validator("doctor_hpr_id")
    @classmethod
    def hpr(cls, v):
        if v and v.strip().upper() in ("NA", "N/A"):
            raise ValueError("NHCX rejects 'NA' as a doctor ID")
        return v


@router.patch("/cases/{case_id}/details")
def patch_details(case_id: str, body: DetailsIn, user: User = Depends(current_user)):
    if user.role not in ("desk", "doctor", "accounts", "admin"):
        raise HTTPException(403, "Your role cannot edit case details")
    d = body.model_dump(exclude_unset=True)
    with tx() as c:
        cs = c.execute("select id, stage, patient_id, policy_id from cases where id=%s and hospital_id=%s for update",
                       (case_id, user.hospital_id)).fetchone()
        if not cs:
            raise HTTPException(404, "Case not found")
        sets = lambda m: {k2: d[k] for k, k2 in m.items() if k in d}
        for table, key, m in (("patients", cs["patient_id"], {"patient_dob": "dob", "patient_sex": "sex", "patient_abha": "abha_id"}),
                              ("policies", cs["policy_id"], {"product_code": "product_code", "member_id": "member_id",
                                                              "relationship": "relationship", "policy_valid_to": "valid_to"}),
                              ("cases", cs["id"], {"treating_doctor": "treating_doctor", "doctor_hpr_id": "doctor_hpr_id",
                                                   "icd10": "icd10", "provisional_dx": "provisional_dx"})):
            v = sets(m)
            if v and key:
                c.execute(f"update {table} set {', '.join(f'{k} = %s' for k in v)} where id=%s", (*v.values(), key))
        c.execute("""insert into case_events (hospital_id, case_id, event, from_stage, to_stage, actor_id, actor_role, payload)
                     values (%s,%s,'details_updated',%s,%s,%s,%s,%s)""",
                  (user.hospital_id, case_id, cs["stage"], cs["stage"], user.id, user.role, Jsonb(sorted(d), dumps=lambda o: __import__('json').dumps(o))))
        return c.execute("""select cs.treating_doctor, cs.doctor_hpr_id, cs.icd10, cs.provisional_dx, pt.dob as patient_dob,
                                   pt.sex as patient_sex, pt.abha_id as patient_abha, p.product_code, p.member_id, p.relationship,
                                   p.valid_to as policy_valid_to
                            from cases cs join patients pt on pt.id=cs.patient_id left join policies p on p.id=cs.policy_id
                            where cs.id=%s""", (case_id,)).fetchone()


@router.get("/cases/{case_id}/details")
def get_details(case_id: str, user: User = Depends(current_user)):
    with tx() as c:
        r = c.execute("""select cs.treating_doctor, cs.doctor_hpr_id, cs.icd10, cs.provisional_dx, pt.dob as patient_dob,
                                pt.sex as patient_sex, pt.abha_id as patient_abha, p.product_code, p.member_id, p.relationship,
                                p.valid_to as policy_valid_to
                         from cases cs join patients pt on pt.id=cs.patient_id left join policies p on p.id=cs.policy_id
                         where cs.id=%s and cs.hospital_id=%s""", (case_id, user.hospital_id)).fetchone()
    if not r:
        raise HTTPException(404, "Case not found")
    return r
