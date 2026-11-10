"""One ABHA number <-> one hospital patient ID (M1 TAGGING_UNIQUEPATIENTID_UNIQUEABHANUMBER).

Before a patient record is created, the ABHA number is looked up: if a patient already has it,
that record is reused (and refreshed), never duplicated. The database also enforces this with
a unique index on abha_number.
"""
import re
import secrets

from sqlalchemy import select

from .. import store
from ..store import PatientRow


def _key(abha_number: str | None) -> str | None:
    if not abha_number:
        return None
    d = re.sub(r"\D", "", abha_number)
    return f"{d[:2]}-{d[2:6]}-{d[6:10]}-{d[10:]}" if len(d) == 14 else abha_number


def _new_id() -> str:
    return f"AXP-{secrets.token_hex(3).upper()}"


def find(abha_number: str) -> PatientRow | None:
    with store.session() as s:
        return s.scalar(select(PatientRow).where(PatientRow.abha_number == _key(abha_number)))


def link(profile: dict, source: str) -> dict:
    """Find-or-create the patient for this ABHA profile. Returns {patient_id, existing}."""
    key = _key(profile.get("abhaNumber"))
    with store.session() as s:
        row = s.scalar(select(PatientRow).where(PatientRow.abha_number == key)) if key else None
        existing = row is not None
        if row is None:
            row = PatientRow(patient_id=_new_id(), abha_number=key, name=profile.get("name") or "", source=source)
            s.add(row)
        for field, attr in (("abhaAddress", "abha_address"), ("name", "name"), ("gender", "gender"),
                            ("dob", "dob"), ("mobile", "mobile"), ("address", "address")):
            if profile.get(field):
                setattr(row, attr, profile[field])
        s.commit()
        return {"patient_id": row.patient_id, "existing": existing}


def update_address(abha_number: str, abha_address: str) -> None:
    with store.session() as s:
        row = s.scalar(select(PatientRow).where(PatientRow.abha_number == _key(abha_number)))
        if row:
            row.abha_address = abha_address
            s.commit()


def list_all() -> list[dict]:
    with store.session() as s:
        return [r.summary() for r in s.scalars(select(PatientRow).order_by(PatientRow.updated_at.desc()))]
