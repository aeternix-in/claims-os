"""Case routes. `apply_event` is the ONLY code that changes a case's stage."""
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import state_machine as sm
from .auth import User, current_user
from .checks import gate
from .db import tx

router = APIRouter(prefix="/cases", tags=["cases"])

CASE_FIELDS = {"estimate", "provisional_dx", "icd10", "approved_amount", "billed_amount",
               "settled_amount", "received_amount", "discharged_at", "payer_ref"}


class NewAdmission(BaseModel):
    patient_name: str = Field(min_length=1)
    sex: str | None = None
    dob: str | None = None
    uhid: str | None = None
    payer_id: str
    policy_number: str
    sum_insured: Decimal | None = None
    room_rent_cap: Decimal | None = None
    copay_pct: Decimal = Decimal(0)
    balance: Decimal | None = None
    sub_limit: Decimal | None = None
    room_rule: dict = {"kind": "none"}
    room_category: str | None = None
    procedure_id: str | None = None
    abha_id: str | None = None
    product_code: str | None = None
    member_id: str | None = None
    doctor_hpr_id: str | None = None
    icd10: str | None = None
    treating_doctor: str | None = None
    ward_category: str | None = None
    room_rent_per_day: Decimal | None = None
    provisional_dx: str | None = None


class EventIn(BaseModel):
    event: str
    payload: dict = {}
    note: str | None = None
    expected_version: int | None = None   # pass the version you saw; stale clicks are refused


def _case_number(conn, hospital_id: str) -> str:
    ym = datetime.now(timezone.utc).strftime("%y%m")
    n = conn.execute("select count(*) as n from cases where hospital_id=%s and case_number like %s",
                     (hospital_id, f"AX-{ym}-%")).fetchone()["n"]
    return f"AX-{ym}-{n + 1:04d}"


@router.post("", status_code=201)
def create_admission(body: NewAdmission, user: User = Depends(current_user)):
    if user.role not in (sm.DESK, sm.ADMIN):
        raise HTTPException(403, "Only the insurance desk creates admissions")
    h = user.hospital_id
    with tx() as c:
        if not c.execute("select 1 from payers where id=%s and hospital_id=%s", (body.payer_id, h)).fetchone():
            raise HTTPException(404, "Payer not found")
        c.execute("select pg_advisory_xact_lock(hashtext(%s))", (h,))  # serialise case numbers
        pat = c.execute("insert into patients (hospital_id, full_name, sex, dob, uhid, abha_id) values (%s,%s,%s,%s,%s,%s) returning id",
                        (h, body.patient_name, body.sex, body.dob or None, body.uhid, body.abha_id)).fetchone()["id"]
        pol = c.execute("""insert into policies (hospital_id, patient_id, payer_id, policy_number, sum_insured,
                           balance_available, room_rent_cap, copay_pct, sub_limit, room_rule, product_code, member_id)
                           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                        (h, pat, body.payer_id, body.policy_number, body.sum_insured,
                         body.balance if body.balance is not None else body.sum_insured, body.room_rent_cap,
                         body.copay_pct, body.sub_limit, Jsonb(body.room_rule), body.product_code, body.member_id)).fetchone()["id"]
        case = c.execute("""insert into cases (hospital_id, case_number, patient_id, policy_id, payer_id,
                            treating_doctor, ward_category, room_rent_per_day, provisional_dx, room_category, procedure_id,
                            doctor_hpr_id, icd10)
                            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *""",
                         (h, _case_number(c, h), pat, pol, body.payer_id, body.treating_doctor,
                          body.ward_category, body.room_rent_per_day, body.provisional_dx,
                          body.room_category, body.procedure_id, body.doctor_hpr_id, (body.icd10 or "").upper() or None)).fetchone()
        c.execute("""insert into case_events (hospital_id, case_id, event, from_stage, to_stage, actor_id, actor_role)
                     values (%s,%s,'admit',null,'admitted',%s,%s)""", (h, case["id"], user.id, user.role))
    return case


@router.get("/{case_id}")
def get_case(case_id: str, user: User = Depends(current_user)):
    with tx() as c:
        case = c.execute("select * from cases where id=%s and hospital_id=%s", (case_id, user.hospital_id)).fetchone()
        if not case:
            raise HTTPException(404, "Case not found")
        events = c.execute("select * from case_events where case_id=%s order by id", (case_id,)).fetchall()
        clocks = c.execute("select * from clocks where case_id=%s order by started_at", (case_id,)).fetchall()
    return {"case": case, "events": events, "clocks": clocks,
            "allowed_events": sm.allowed_events(case["stage"], user.role)}


QUERY_EVENTS = {"preauth_query": "preauth", "discharge_query": "discharge", "claim_query": "claim"}


def apply(case_id: str, event: str, payload: dict, user: User, note: str | None = None,
          expected_version: int | None = None, hook=None, open_query: bool = True) -> dict:
    """The ONLY code path that changes a case's stage. `hook(conn, case)` runs inside the same
    transaction (used by the query desk to mark a reply sent atomically with the move)."""
    payload = payload or {}
    with tx() as c:
        case = c.execute("select * from cases where id=%s and hospital_id=%s",
                         (case_id, user.hospital_id)).fetchone()
        if not case:
            raise HTTPException(404, "Case not found")
        if expected_version is not None and expected_version != case["version"]:
            raise HTTPException(409, {"code": "stale", "message": "Someone else moved this case; reload"})
        settings = c.execute("select settings from hospitals where id=%s", (user.hospital_id,)).fetchone()["settings"]
        try:
            sm.plan(case, event, payload, user.role, settings)
        except sm.TransitionError as e:
            raise HTTPException(422, {"code": e.code, "message": str(e)})
    # pre-flight gate runs (and is recorded) in its own transaction so a refusal still leaves the check run
    if event in ("submit_preauth", "request_final_auth", "submit_claim"):
        gate(case_id, user, event)
    with tx() as c:
        case = c.execute("select * from cases where id=%s and hospital_id=%s for update",
                         (case_id, user.hospital_id)).fetchone()
        if expected_version is not None and expected_version != case["version"]:
            raise HTTPException(409, {"code": "stale", "message": "Someone else moved this case; reload"})
        try:
            p = sm.plan(case, event, payload, user.role, settings)
        except sm.TransitionError as e:
            raise HTTPException(422, {"code": e.code, "message": str(e)})

        updates = {k: v for k, v in p.field_updates.items() if k in CASE_FIELDS}
        if updates.get("discharged_at") == "now":
            updates["discharged_at"] = datetime.now(timezone.utc)
        sets = ", ".join([f"{k} = %s" for k in updates] + ["stage = %s", "version = version + 1"])
        case = c.execute(f"update cases set {sets} where id=%s returning *",
                         (*updates.values(), p.to_stage, case_id)).fetchone()

        if p.clock_stop:
            c.execute("update clocks set stopped_at=now() where case_id=%s and kind=%s and stopped_at is null",
                      (case_id, p.clock_stop))
        if p.clock_start:
            kind, due = p.clock_start
            c.execute("insert into clocks (hospital_id, case_id, kind, due_at, source) values (%s,%s,%s,%s,%s)",
                      (user.hospital_id, case_id, kind, due, sm.CLOCK_SOURCE))
        c.execute("""insert into case_events (hospital_id, case_id, event, from_stage, to_stage, actor_id,
                     actor_role, payload, note) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                  (user.hospital_id, case_id, event, p.from_stage, p.to_stage, user.id, user.role,
                   Jsonb(payload), note))
        if event in QUERY_EVENTS and open_query:      # a payer query arrived: open it on the query desk
            from .querydesk import open_query
            open_query(c, user, case, QUERY_EVENTS[event], payload)
        if hook:
            hook(c, case)
    return {"case": case, "allowed_events": sm.allowed_events(case["stage"], user.role)}


@router.post("/{case_id}/events")
def apply_event(case_id: str, body: EventIn, user: User = Depends(current_user)):
    if body.event.startswith("answer_") and body.event.endswith("_query"):
        raise HTTPException(422, {"code": "use_query_desk",
                                  "message": "Answer queries from the Queries tab so the reply, attachments and sign-off are kept"})
    return apply(case_id, body.event, body.payload, user, body.note, body.expected_version)
