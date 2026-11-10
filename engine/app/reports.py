"""Leakage, payer scorecards, owner pack, CSV exports, and the claim export hook for the
existing NHCX codebase (the FHIR / NHCX layer lives there, not here)."""
import csv
import io
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from . import adjudicate as adj
from . import leakage as lk
from .auth import User, current_user
from .billing import hospital_from, rules_from, _f
from .db import tx

router = APIRouter(tags=["reports"])
REPORT_ROLES = {"owner", "accounts", "admin", "desk"}


def _need(user: User):
    if user.role not in REPORT_ROLES:
        raise HTTPException(403, "Your role cannot see reports")


def case_facts(c, hospital_id: str, frm: date | None = None, to: date | None = None, payer_id: str | None = None) -> list[dict]:
    """Everything the reports need, per case, in a handful of set-based queries."""
    cases = c.execute("""
        select cs.*, pt.full_name as patient, py.name as payer, py.kind as payer_kind, py.rules as payer_rules,
               p.sum_insured, p.balance_available, p.copay_pct, p.sub_limit, p.room_rule, h.settings as hsettings
        from cases cs join patients pt on pt.id=cs.patient_id left join payers py on py.id=cs.payer_id
        left join policies p on p.id=cs.policy_id join hospitals h on h.id=cs.hospital_id
        where cs.hospital_id=%s and (%s::date is null or cs.admitted_at::date >= %s) and (%s::date is null or cs.admitted_at::date <= %s)
          and (%s::uuid is null or cs.payer_id=%s::uuid)
        order by cs.admitted_at desc""", (hospital_id, frm, frm, to, to, payer_id, payer_id)).fetchall()
    if not cases:
        return []
    ids = [x["id"] for x in cases]
    lines: dict = {}
    for l in c.execute("select * from bill_lines where case_id = any(%s) order by position", (ids,)).fetchall():
        lines.setdefault(l["case_id"], []).append(l)
    evs: dict = {}
    for e in c.execute("""select case_id, event, payload, created_at from case_events where case_id = any(%s)
                          and event in ('preauth_approved','enhancement_approved','final_auth_approved','submit_claim',
                                        'settlement_letter','claim_rejected','preauth_denied','bank_credit_matched','preauth_query')
                          order by id""", (ids,)).fetchall():
        evs.setdefault(e["case_id"], []).append(e)
    letters: dict = {}
    for l in c.execute("select case_id, kind from letters where case_id = any(%s) and status='sent'", (ids,)).fetchall():
        letters.setdefault(l["case_id"], set()).add(l["kind"])
    qn = {r["case_id"]: r["n"] for r in c.execute("select case_id, count(*)::int as n from queries where case_id = any(%s) group by case_id", (ids,)).fetchall()}
    clocks: dict = {}
    for k in c.execute("select case_id, kind, breached from clocks where case_id = any(%s) and stopped_at is not null", (ids,)).fetchall():
        d = clocks.setdefault(k["case_id"], {"preauth": 0, "discharge": 0})
        d[k["kind"]] = d.get(k["kind"], 0) + (1 if k["breached"] else 0)

    out = []
    for cs in cases:
        e = evs.get(cs["id"], [])
        last = lambda name: next((x for x in reversed(e) if x["event"] == name), None)
        first = lambda name: next((x for x in e if x["event"] == name), None)
        amt = lambda x, key="approved_amount": float(x["payload"].get(key)) if x and x["payload"].get(key) is not None else None
        ev = {"preauth_approved": amt(first("preauth_approved")),
              "enhancements": sum(1 for x in e if x["event"] == "enhancement_approved"),
              "final_approved": amt(last("final_auth_approved")),
              "settled_at": (last("settlement_letter") or {}).get("created_at"),
              "rejected_at": ((last("claim_rejected") or last("preauth_denied")) or {}).get("created_at"),
              "paid_at": (last("bank_credit_matched") or {}).get("created_at")}
        rules = rules_from(cs["payer_rules"])
        audit = None
        if lines.get(cs["id"]):
            si = _f(cs["sum_insured"]) or 0
            pol = adj.Policy(si, _f(cs["balance_available"]) if cs["balance_available"] is not None else si,
                             _f(cs["copay_pct"]) or 0, _f(cs["sub_limit"]), cs["room_rule"] or {"kind": "none"})
            audit = adj.adjudicate([adj.BillLine(l["category"], l["description"], _f(l["qty"]), _f(l["rate"]), _f(l["mou_rate"]), l["to_payer"])
                                    for l in lines[cs["id"]]], pol, rules, hospital_from(cs["hsettings"]),
                                   cs["procedure_id"], cs["room_category"], ev["final_approved"] or 0)
        case = {"stage": cs["stage"], "approved_amount": _f(cs["approved_amount"]), "settled_amount": _f(cs["settled_amount"]),
                "received_amount": _f(cs["received_amount"])}
        leak = lk.case_leakage(case, audit, ev, letters.get(cs["id"], set()), rules.tds_pct)
        out.append({
            "id": str(cs["id"]), "case_number": cs["case_number"], "patient": cs["patient"], "payer": cs["payer"],
            "payer_id": str(cs["payer_id"]) if cs["payer_id"] else None, "stage": cs["stage"],
            "admitted_at": cs["admitted_at"], "discharged_at": cs["discharged_at"],
            "billed": float(audit["gross"]) if audit else float(cs["billed_amount"] or 0),
            "claimed": float(audit["expected_payable"]) if audit else float(cs["approved_amount"] or 0),
            "approved_amount": _f(cs["approved_amount"]), "settled_amount": _f(cs["settled_amount"]),
            "received_amount": _f(cs["received_amount"]),
            "submitted_at": (last("submit_claim") or {}).get("created_at"), "settled_at": ev["settled_at"], "paid_at": ev["paid_at"],
            "preauth_decided": bool(first("preauth_approved") or first("preauth_denied")),
            "preauth_queried": bool(first("preauth_query")), "queries": qn.get(cs["id"], 0),
            "preauth_breaches": clocks.get(cs["id"], {}).get("preauth", 0), "discharge_breaches": clocks.get(cs["id"], {}).get("discharge", 0),
            "leak": leak, "went": lk.where_it_went(audit, case, rules.tds_pct, leak),
        })
    return out


def _period(frm: str | None, to: str | None):
    try:
        return (date.fromisoformat(frm) if frm else None, date.fromisoformat(to) if to else None)
    except ValueError:
        raise HTTPException(422, "Dates must be YYYY-MM-DD")


@router.get("/reports/leakage")
def leakage(frm: str | None = None, to: str | None = None, payer_id: str | None = None, user: User = Depends(current_user)):
    _need(user)
    f, t = _period(frm, to)
    with tx() as c:
        rows = case_facts(c, user.hospital_id, f, t, payer_id)
    by_code = []
    for k in lk.ORDER:
        hits = [r for r in rows if r["leak"]["codes"][k] > 0]
        by_code.append({"code": k, **lk.CODES[k], "amount": sum(r["leak"]["codes"][k] for r in hits), "claims": len(hits)})
    went = {k: sum(r["went"].get(k, 0) for r in rows) for k in ("gross", "discount", "family", "leakage", "tds", "received", "outstanding")}
    return {"by_code": by_code, "where_it_went": went,
            "hospital_leakage": sum(r["leak"]["hospital"] for r in rows), "family_share": sum(r["leak"]["family"] for r in rows),
            "contestable": sum(r["leak"]["contestable"] for r in rows),
            "claims": [{k: r[k] for k in ("id", "case_number", "patient", "payer", "stage", "billed")} | {"leak": r["leak"]}
                       for r in rows if r["leak"]["hospital"] + r["leak"]["family"] > 0]}


@router.get("/reports/payers")
def payers(frm: str | None = None, to: str | None = None, user: User = Depends(current_user)):
    _need(user)
    f, t = _period(frm, to)
    with tx() as c:
        rows = case_facts(c, user.hospital_id, f, t)
        names = {str(p["id"]): (p["name"], p["kind"]) for p in c.execute("select id, name, kind from payers where hospital_id=%s", (user.hospital_id,)).fetchall()}
    groups: dict = {}
    for r in rows:
        groups.setdefault(r["payer_id"], []).append(r)
    cards = [{"payer_id": pid, "payer": names.get(pid, ("Unknown", ""))[0], "kind": names.get(pid, ("", ""))[1], **lk.scorecard(g)}
             for pid, g in groups.items()]
    cards.sort(key=lambda x: -x["outstanding"])
    return {"scorecards": cards}


@router.get("/reports/owner")
def owner_pack(user: User = Depends(current_user)):
    """The owner's one-page view: money buckets, leakage, slowest payers. Case numbers only, no names."""
    _need(user)
    from .billing import overview
    ov = overview(user)
    lkg = leakage(None, None, None, user)
    sc = payers(None, None, user)["scorecards"]
    return {"as_of": datetime.now(timezone.utc).isoformat(), "buckets": ov["buckets"], "in_house": ov["in_house"],
            "leakage": {k: lkg[k] for k in ("hospital_leakage", "family_share", "contestable", "where_it_went")},
            "top_leakage": sorted([x for x in lkg["by_code"] if x["amount"] > 0], key=lambda x: -x["amount"])[:5],
            "payers": [{k: s[k] for k in ("payer", "outstanding", "ageing", "days_submit_to_bank", "settled_vs_claimed_pct")} for s in sc[:6]]}


def _csv(rows: list[list], name: str) -> Response:
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/reports/claims.csv")
def claims_csv(frm: str | None = None, to: str | None = None, user: User = Depends(current_user)):
    _need(user)
    f, t = _period(frm, to)
    with tx() as c:
        rows = case_facts(c, user.hospital_id, f, t)
    head = ["case_number", "patient", "payer", "stage", "admitted", "discharged", "billed", "claimed", "approved", "settled", "received",
            "hospital_leakage", "family_share"] + lk.ORDER
    body = [[r["case_number"], r["patient"], r["payer"], r["stage"], r["admitted_at"].date().isoformat() if r["admitted_at"] else "",
             r["discharged_at"].date().isoformat() if r["discharged_at"] else "", r["billed"], r["claimed"], r["approved_amount"],
             r["settled_amount"], r["received_amount"], round(r["leak"]["hospital"]), round(r["leak"]["family"])]
            + [round(r["leak"]["codes"][k]) for k in lk.ORDER] for r in rows]
    return _csv([head] + body, "claims-register.csv")


@router.get("/reports/payers.csv")
def payers_csv(user: User = Depends(current_user)):
    cards = payers(None, None, user)["scorecards"]
    keys = ["payer", "claims", "first_time_preauth_pct", "query_rate", "preauth_clock_breaches", "discharge_clock_breaches",
            "settled_vs_claimed_pct", "deductions_after_discharge", "short_payments", "rejections", "days_submit_to_settle",
            "days_submit_to_bank", "outstanding"]
    return _csv([keys] + [[s[k] for k in keys] for s in cards], "payer-scorecards.csv")


# ---------- hook for the existing NHCX codebase ----------
@router.get("/cases/{case_id}/export")
def export_claim(case_id: str, user: User = Depends(current_user)):
    """A neutral, complete snapshot of one claim for the existing NHCX / FHIR code to map and send.
    Contains personal data: for server-to-server use inside India only."""
    with tx() as c:
        cs = c.execute("""select cs.*, pt.full_name, pt.sex, pt.dob, pt.abha_id, pt.uhid, p.policy_number, p.member_id, p.sum_insured,
                                 py.name as payer_name, py.kind as payer_kind, h.name as hospital_name
                          from cases cs join patients pt on pt.id=cs.patient_id left join policies p on p.id=cs.policy_id
                          left join payers py on py.id=cs.payer_id join hospitals h on h.id=cs.hospital_id
                          where cs.id=%s and cs.hospital_id=%s""", (case_id, user.hospital_id)).fetchone()
        if not cs:
            raise HTTPException(404, "Case not found")
        lines = c.execute("select category, description, qty, rate, mou_rate, to_payer from bill_lines where case_id=%s order by position", (case_id,)).fetchall()
        docs = c.execute("select id, doc_type, filename, mime, sha256, pages, storage_path from documents where case_id=%s order by created_at", (case_id,)).fetchall()
        fields = c.execute("select name, value, confirmed from extracted_fields where case_id=%s and confirmed", (case_id,)).fetchall()
        events = c.execute("select event, from_stage, to_stage, created_at from case_events where case_id=%s order by id", (case_id,)).fetchall()
    return {
        "schema": "aeternix.claim-export/1",
        "hospital": {"id": str(cs["hospital_id"]), "name": cs["hospital_name"]},
        "case": {k: cs[k] for k in ("id", "case_number", "stage", "admitted_at", "discharged_at", "provisional_dx", "icd10",
                                     "procedure_id", "room_category", "treating_doctor", "estimate", "approved_amount",
                                     "billed_amount", "settled_amount", "received_amount", "payer_ref")},
        "patient": {"name": cs["full_name"], "sex": cs["sex"], "dob": cs["dob"], "abha_id": cs["abha_id"], "uhid": cs["uhid"]},
        "coverage": {"payer": cs["payer_name"], "payer_kind": cs["payer_kind"], "policy_number": cs["policy_number"],
                     "member_id": cs["member_id"], "sum_insured": cs["sum_insured"]},
        "bill_lines": lines, "documents": docs, "confirmed_fields": fields, "timeline": events,
    }
