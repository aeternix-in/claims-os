"""Leakage codes (L1–L7, from the CTO demo) and payer scorecards. Pure functions over plain dicts.

Every rupee billed ends up in exactly one place:
  agreed discount (not leakage) · family share (L3a, L3b, L3d: collectable from the family)
  · hospital leakage (L1, L2, L3c, L4, L5, L6, L7) · TDS (tax credit, not a loss)
  · received in the bank · still outstanding
"""
from __future__ import annotations

from datetime import datetime, timezone

CODES = {
    "L1": {"name": "Under-approval never enhanced", "stage": "Pre-auth", "owner": "Desk", "avoidable": "Often", "family": False, "contestable": False},
    "L2": {"name": "Enhancement missed or late", "stage": "Stay", "owner": "Desk and doctor", "avoidable": "Yes", "family": False, "contestable": False},
    "L3a": {"name": "Non-payable items billed", "stage": "Discharge", "owner": "Billing", "avoidable": "Collectable; avoidable if explained at admission", "family": True, "contestable": False},
    "L3b": {"name": "Room-rent proportionate cut", "stage": "Discharge", "owner": "Admission desk", "avoidable": "Yes, with room advice at admission", "family": True, "contestable": False},
    "L3c": {"name": "Charges above the MoU tariff", "stage": "Discharge", "owner": "Billing", "avoidable": "Yes", "family": False, "contestable": False},
    "L3d": {"name": "Co-pay, sub-limit, deductible", "stage": "Discharge", "owner": "Policy terms", "avoidable": "No; collectable", "family": True, "contestable": False},
    "L4": {"name": "Cut after the patient has left", "stage": "Settlement", "owner": "Payer", "avoidable": "Contestable", "family": False, "contestable": True},
    "L5": {"name": "Unexplained short payment", "stage": "Settlement", "owner": "Payer or reconciliation", "avoidable": "Contestable", "family": False, "contestable": True},
    "L6": {"name": "Rejection for documents or late filing", "stage": "Submission", "owner": "Desk or MRD", "avoidable": "Yes", "family": False, "contestable": True},
    "L7": {"name": "Silent write-off, never chased", "stage": "Close", "owner": "Accounts", "avoidable": "Yes", "family": False, "contestable": False},
}
ORDER = ["L1", "L2", "L3a", "L3b", "L3c", "L3d", "L4", "L5", "L6", "L7"]
TOL = 100            # rupees below which a difference is rounding, not leakage
UNCHASED_DAYS = 60   # contestable loss with no dispute/appeal sent after this long → L7


def _days(a, b=None) -> float | None:
    if not a:
        return None
    b = b or datetime.now(timezone.utc)
    return (b - a).total_seconds() / 86400


def case_leakage(case: dict, audit: dict | None, ev: dict, letters_sent: set[str], tds_pct: float) -> dict:
    """
    case:  stage, approved_amount, settled_amount, received_amount
    audit: adjudicate() output for the final bill (None if no bill yet)
    ev:    {'preauth_approved': amt|None, 'enhancements': n, 'final_approved': amt|None,
            'settled_at': dt|None, 'rejected_at': dt|None, 'paid_at': dt|None}
    """
    out = {k: 0.0 for k in ORDER}
    notes: list[str] = []
    if audit:
        out["L3a"] = float(audit["family"]["nonpayable"])
        out["L3b"] = float(audit["pd"])
        out["L3d"] = float(audit["policy_terms"])
        out["L3c"] = float(audit["above_mou"])
        payable = float(audit["expected_payable"])
        final = ev.get("final_approved")
        if final is not None and payable - final > TOL:
            gap = payable - final
            if ev.get("enhancements", 0) == 0:
                out["L1"] = gap
                notes.append("Approved below what the bill supports and no enhancement was raised")
            else:
                out["L2"] = gap
                notes.append("Enhanced, but approval still short of the bill")
    final = ev.get("final_approved")
    settled = case.get("settled_amount")
    received = case.get("received_amount")
    if case["stage"] in ("rejected", "denied"):
        base = float(final or case.get("approved_amount") or (audit or {}).get("expected_payable") or 0)
        out["L6"] = base
        notes.append("Rejected")
    if settled is not None and final is not None and final - float(settled) > TOL:
        out["L4"] = final - float(settled)
    if settled is not None and received is not None:
        expect = float(settled) - round(float(settled) * tds_pct / 100)
        if expect - float(received) > TOL:
            out["L5"] = expect - float(received)

    # contestable losses nobody chased become silent write-offs
    when = ev.get("rejected_at") or ev.get("paid_at") or ev.get("settled_at")
    chased = bool(letters_sent & {"dispute", "appeal"})
    unchased = (out["L4"] + out["L5"] + out["L6"]) > 0 and not chased and (_days(when) or 0) > UNCHASED_DAYS
    if unchased:
        out["L7"] = out["L4"] + out["L5"] + out["L6"]
        out["L4"] = out["L5"] = out["L6"] = 0.0
        notes.append(f"Contestable loss not disputed within {UNCHASED_DAYS} days")
    hospital = sum(v for k, v in out.items() if not CODES[k]["family"])
    family = sum(v for k, v in out.items() if CODES[k]["family"])
    contestable = sum(out[k] for k in ("L4", "L5", "L6"))
    return {"codes": out, "hospital": hospital, "family": family, "contestable": contestable, "notes": notes}


def where_it_went(audit: dict | None, case: dict, tds_pct: float, leak: dict) -> dict:
    """One claim's billed rupee split into buckets that add back to the gross bill."""
    if not audit:
        return {}
    gross = float(audit["gross"])
    discount = float(audit["discount"])
    family = leak["family"]
    hosp = leak["hospital"]
    settled = case.get("settled_amount")
    received = float(case.get("received_amount") or 0)
    tds = round(float(settled) * tds_pct / 100) if settled is not None and case["stage"] in ("paid", "closed") else 0
    outstanding = max(0.0, gross - discount - family - hosp - tds - received)
    return {"gross": gross, "discount": discount, "family": family, "leakage": hosp, "tds": tds,
            "received": received, "outstanding": outstanding}


def scorecard(rows: list[dict]) -> dict:
    """rows: per-case dicts for ONE payer with keys from reports.case_facts()."""
    n = len(rows)
    sub = [r for r in rows if r["submitted_at"]]
    settled = [r for r in rows if r["settled_at"] and r["submitted_at"]]
    paid = [r for r in rows if r["paid_at"] and r["submitted_at"]]
    pre = [r for r in rows if r["preauth_decided"]]

    def avg(xs):
        xs = [x for x in xs if x is not None]
        return round(sum(xs) / len(xs), 1) if xs else None

    def pct(a, b):
        return round(100 * a / b, 1) if b else None

    claimed = sum(r["claimed"] for r in rows if r["settled_amount"] is not None)
    settled_amt = sum(float(r["settled_amount"]) for r in rows if r["settled_amount"] is not None)
    out_rows = [r for r in rows if r["stage"] in ("submitted", "claim_query", "settled")]
    ageing = {"0-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0}
    for r in out_rows:
        d = _days(r["submitted_at"]) or 0
        k = "0-30" if d <= 30 else "31-60" if d <= 60 else "61-90" if d <= 90 else "90+"
        ageing[k] += float(r["settled_amount"] or r["approved_amount"] or 0)
    return {
        "claims": n,
        "first_time_preauth_pct": pct(sum(1 for r in pre if not r["preauth_queried"]), len(pre)),
        "query_rate": round(sum(r["queries"] for r in rows) / n, 2) if n else None,
        "preauth_clock_breaches": sum(r["preauth_breaches"] for r in rows),
        "discharge_clock_breaches": sum(r["discharge_breaches"] for r in rows),
        "settled_vs_claimed_pct": pct(settled_amt, claimed),
        "deductions_after_discharge": sum(r["leak"]["codes"]["L4"] + r["leak"]["codes"]["L7"] for r in rows),
        "short_payments": sum(r["leak"]["codes"]["L5"] for r in rows),
        "rejections": sum(1 for r in rows if r["stage"] in ("rejected", "denied")),
        "days_submit_to_settle": avg([_days(r["submitted_at"], r["settled_at"]) for r in settled]),
        "days_submit_to_bank": avg([_days(r["submitted_at"], r["paid_at"]) for r in paid]),
        "outstanding": sum(ageing.values()), "ageing": ageing,
        "hospital_leakage": sum(r["leak"]["hospital"] for r in rows),
    }
