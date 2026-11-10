"""Bill, policy check, payer rulebook and overview routes."""
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import adjudicate as adj
from .auth import User, current_user
from .db import tx

router = APIRouter(tags=["billing"])

BILL_EDITABLE = {"admitted", "preauth", "preauth_pending", "preauth_query", "in_treatment", "discharge",
                 "discharge_pending", "discharge_query", "discharged", "claim"}
BILL_ROLES = {"desk", "accounts", "admin"}


def _f(x):
    return float(x) if isinstance(x, Decimal) else x


def rules_from(row: dict) -> adj.PayerRules:
    r = row or {}
    return adj.PayerRules(basis=r.get("basis", "itemised"), mou_discount_pct=r.get("mou_discount_pct", 0),
                          discount_excludes=r.get("discount_excludes", ["pharmacy", "implants"]),
                          applies_proportionate=r.get("applies_proportionate", True),
                          package_rates=r.get("package_rates", {}), tds_pct=r.get("tds_pct", 10))


def hospital_from(settings: dict) -> adj.Hospital:
    return adj.Hospital(rooms=settings.get("rooms", adj.DEFAULT_ROOMS),
                        differential_billing=settings.get("differentialBilling", True))


def _context(c, case_id: str, hospital_id: str):
    row = c.execute("""
        select cs.*, p.sum_insured, p.balance_available, p.copay_pct, p.sub_limit, p.room_rule,
               py.rules as payer_rules, h.settings as hospital_settings
        from cases cs
        left join policies p on p.id = cs.policy_id
        left join payers py on py.id = cs.payer_id
        join hospitals h on h.id = cs.hospital_id
        where cs.id = %s and cs.hospital_id = %s""", (case_id, hospital_id)).fetchone()
    if not row:
        raise HTTPException(404, "Case not found")
    si = _f(row["sum_insured"]) or 0
    policy = adj.Policy(sum_insured=si, balance=_f(row["balance_available"]) if row["balance_available"] is not None else si,
                        copay_pct=_f(row["copay_pct"]) or 0, sub_limit=_f(row["sub_limit"]),
                        room_rule=row["room_rule"] or {"kind": "none"})
    return row, policy, rules_from(row["payer_rules"]), hospital_from(row["hospital_settings"])


def _audit(row, lines, policy, rules, hospital):
    bill = [adj.BillLine(l["category"], l["description"], _f(l["qty"]), _f(l["rate"]), _f(l["mou_rate"]), l["to_payer"])
            for l in lines]
    a = adj.adjudicate(bill, policy, rules, hospital, procedure_id=row["procedure_id"],
                       room_category=row["room_category"], authorised=_f(row["approved_amount"]) or 0)
    if row["settled_amount"] is not None:
        a["credit"] = adj.expected_credit(_f(row["settled_amount"]), rules.tds_pct)
    a["room_advice"] = adj.room_advice(policy, hospital)
    return a


# ---------- bill ----------
class LineIn(BaseModel):
    category: str
    description: str = Field(min_length=1)
    qty: Decimal = Field(ge=0)
    rate: Decimal = Field(ge=0)
    mou_rate: Decimal | None = None
    to_payer: bool = True
    snomed_code: str | None = Field(default=None, pattern=r"^\d{6,18}$")


class BillIn(BaseModel):
    lines: list[LineIn]
    note: str | None = None


@router.get("/cases/{case_id}/bill")
def get_bill(case_id: str, user: User = Depends(current_user)):
    with tx() as c:
        row, policy, rules, hospital = _context(c, case_id, user.hospital_id)
        lines = c.execute("select * from bill_lines where case_id=%s order by position", (case_id,)).fetchall()
    return {"lines": lines, "audit": _audit(row, lines, policy, rules, hospital),
            "editable": row["stage"] in BILL_EDITABLE and user.role in BILL_ROLES,
            "locked": row["stage"] not in BILL_EDITABLE,
            "categories": [{"id": k, "label": v} for k, v in adj.CATEGORY_LABEL.items()]}


@router.put("/cases/{case_id}/bill")
def put_bill(case_id: str, body: BillIn, user: User = Depends(current_user)):
    if user.role not in BILL_ROLES:
        raise HTTPException(403, "Only the desk or accounts edits the bill")
    bad = [l.category for l in body.lines if l.category not in adj.CATEGORIES]
    if bad:
        raise HTTPException(422, {"code": "bad_category", "message": f"Unknown category: {bad[0]}"})
    with tx() as c:
        row, policy, rules, hospital = _context(c, case_id, user.hospital_id)
        c.execute("select 1 from cases where id=%s for update", (case_id,))
        if row["stage"] not in BILL_EDITABLE:
            raise HTTPException(409, {"code": "locked", "message": "The claim file has been submitted; the bill is locked"})
        c.execute("delete from bill_lines where case_id=%s", (case_id,))
        for i, l in enumerate(body.lines):
            c.execute("""insert into bill_lines (hospital_id, case_id, position, category, description, qty, rate,
                         mou_rate, to_payer, snomed_code) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                      (user.hospital_id, case_id, i, l.category, l.description, l.qty, l.rate, l.mou_rate, l.to_payer,
                       l.snomed_code or None))
        lines = c.execute("select * from bill_lines where case_id=%s order by position", (case_id,)).fetchall()
        audit = _audit(row, lines, policy, rules, hospital)
        c.execute("update cases set billed_amount=%s where id=%s", (audit["gross"], case_id))
        c.execute("""insert into case_events (hospital_id, case_id, event, from_stage, to_stage, actor_id, actor_role, payload, note)
                     values (%s,%s,'bill_updated',%s,%s,%s,%s,%s,%s)""",
                  (user.hospital_id, case_id, row["stage"], row["stage"], user.id, user.role,
                   Jsonb({"lines": len(lines), "gross": audit["gross"], "expected_payable": audit["expected_payable"]}), body.note))
    return {"lines": lines, "audit": audit}


# ---------- policy check at admission (no case yet) ----------
class PolicyCheckIn(BaseModel):
    payer_id: str
    sum_insured: float = Field(gt=0)
    balance: float | None = None
    copay_pct: float = 0
    sub_limit: float | None = None
    room_rule: dict = {"kind": "none"}
    room_category: str = "general"
    procedure_id: str | None = None
    estimate_lines: list[LineIn] = []


@router.post("/policy-check")
def policy_check(body: PolicyCheckIn, user: User = Depends(current_user)):
    with tx() as c:
        p = c.execute("select rules from payers where id=%s and hospital_id=%s", (body.payer_id, user.hospital_id)).fetchone()
        if not p:
            raise HTTPException(404, "Payer not found")
        h = c.execute("select settings from hospitals where id=%s", (user.hospital_id,)).fetchone()
    hospital, rules = hospital_from(h["settings"]), rules_from(p["rules"])
    policy = adj.Policy(body.sum_insured, body.balance if body.balance is not None else body.sum_insured,
                        body.copay_pct, body.sub_limit, body.room_rule)
    out = {"room_advice": adj.room_advice(policy, hospital),
           "room_rule_label": adj.room_eligibility(policy, hospital)["label"], "rooms": hospital.rooms}
    if body.estimate_lines:
        bill = [adj.BillLine(l.category, l.description, float(l.qty), float(l.rate),
                             float(l.mou_rate) if l.mou_rate is not None else None, l.to_payer) for l in body.estimate_lines]
        out["estimate"] = adj.adjudicate(bill, policy, rules, hospital, body.procedure_id, body.room_category)
    else:
        # quick view: what a room above eligibility does to room-linked charges
        cap = out["room_advice"]["cap"]
        rate = hospital.rooms.get(body.room_category, 0)
        out["chosen_room"] = {"category": body.room_category, "rate": rate,
                              "payer_share_of_room_linked": min(1, cap / rate) if cap and rate else 1}
    return out


# ---------- payer rulebook ----------
class RulesIn(BaseModel):
    basis: str = Field(pattern="^(itemised|package)$")
    mou_discount_pct: float = Field(ge=0, le=100)
    discount_excludes: list[str] = []
    applies_proportionate: bool = True
    package_rates: dict[str, float] = {}
    tds_pct: float = Field(10, ge=0, le=100)
    payment_term_days: int = 30
    claim_file_window_days: int = 15


@router.put("/payers/{payer_id}/rules")
def put_rules(payer_id: str, body: RulesIn, user: User = Depends(current_user)):
    if user.role not in ("owner", "admin", "accounts"):
        raise HTTPException(403, "Only the owner, admin or accounts edits payer terms")
    with tx() as c:
        r = c.execute("update payers set rules=%s where id=%s and hospital_id=%s returning *",
                      (Jsonb(body.model_dump()), payer_id, user.hospital_id)).fetchone()
    if not r:
        raise HTTPException(404, "Payer not found")
    return r


# ---------- overview ----------
@router.get("/overview")
def overview(user: User = Depends(current_user)):
    h = user.hospital_id
    with tx() as c:
        by_stage = {r["stage"]: r for r in c.execute("""
            select stage, count(*)::int as n, coalesce(sum(approved_amount),0) as approved,
                   coalesce(sum(billed_amount),0) as billed, coalesce(sum(settled_amount),0) as settled,
                   coalesce(sum(received_amount),0) as received,
                   coalesce(sum(greatest(approved_amount - coalesce(settled_amount, approved_amount),0)),0) as short
            from cases where hospital_id=%s group by stage""", (h,)).fetchall()}
        clocks = c.execute("""
            select k.id, k.kind, k.due_at, cs.id as case_id, cs.case_number, cs.stage, pt.full_name as patient
            from clocks k join cases cs on cs.id=k.case_id join patients pt on pt.id=cs.patient_id
            where k.hospital_id=%s and k.stopped_at is null order by k.due_at limit 20""", (h,)).fetchall()
        breaches = c.execute("""select kind, count(*)::int as total, count(*) filter (where breached)::int as breached
            from clocks where hospital_id=%s and stopped_at is not null group by kind""", (h,)).fetchall()

    def s(stages, field="approved"):
        return sum(_f(by_stage[x][field]) for x in stages if x in by_stage)

    def n(stages):
        return sum(by_stage[x]["n"] for x in stages if x in by_stage)

    buckets = [
        {"id": "A", "label": "Discharged, not yet submitted", "stages": ["discharged", "claim"], "amount": s(["discharged", "claim"])},
        {"id": "S", "label": "With payer", "stages": ["submitted"], "amount": s(["submitted"])},
        {"id": "C", "label": "In query", "stages": ["claim_query"], "amount": s(["claim_query"])},
        {"id": "D", "label": "Rejected or cut", "stages": ["rejected", "settled", "paid", "closed"],
         "amount": s(["rejected"]) + s(["settled", "paid", "closed"], "short")},
        {"id": "E", "label": "Settled, credit not received", "stages": ["settled"], "amount": s(["settled"], "settled")},
        {"id": "F", "label": "Received in bank", "stages": ["paid", "closed"], "amount": s(["paid", "closed"], "received")},
    ]
    for b in buckets:
        b["count"] = n(b["stages"]) if b["id"] != "D" else n(["rejected"])
    return {
        "in_house": n(["admitted", "preauth", "preauth_pending", "preauth_query", "in_treatment",
                       "discharge", "discharge_pending", "discharge_query"]),
        "needs_action": {"preauth_pending": n(["preauth_pending"]), "queries": n(["preauth_query", "discharge_query", "claim_query"]),
                         "discharge_pending": n(["discharge_pending"]), "to_submit": n(["discharged", "claim"])},
        "buckets": buckets, "open_clocks": clocks, "clock_record": breaches,
        "stage_counts": {k: v["n"] for k, v in by_stage.items()},
    }
