from datetime import datetime, timedelta, timezone

from app import leakage as lk
from app.adjudicate import BillLine, Hospital, PayerRules, Policy, adjudicate

NOW = datetime.now(timezone.utc)
BILL = [BillLine("room", "Single", 2, 6500, 6500), BillLine("ot", "OT", 1, 25000, 22000), BillLine("surgeon", "S", 1, 32000),
        BillLine("pharmacy", "P", 1, 9000), BillLine("nonpayable", "Kit", 1, 800)]
POL = Policy(300000, 300000, copay_pct=10, room_rule={"kind": "pctSI", "roomPct": 1, "icuPct": 2})
AUDIT = adjudicate(BILL, POL, PayerRules(mou_discount_pct=5), Hospital())


def leak(case, ev, letters=frozenset()):
    return lk.case_leakage(case, AUDIT, {"enhancements": 0, **ev}, set(letters), 10)


def test_family_codes_from_audit():
    L = leak({"stage": "discharged"}, {})
    c = L["codes"]
    assert c["L3a"] == 800 and c["L3b"] == AUDIT["pd"] > 0 and c["L3d"] == AUDIT["policy_terms"] > 0 and c["L3c"] == 3000
    assert L["family"] == c["L3a"] + c["L3b"] + c["L3d"]


def test_under_approval_L1_vs_L2():
    pay = AUDIT["expected_payable"]
    assert leak({"stage": "discharged"}, {"final_approved": pay - 5000})["codes"]["L1"] == 5000
    L = leak({"stage": "discharged"}, {"final_approved": pay - 5000, "enhancements": 1})["codes"]
    assert L["L2"] == 5000 and L["L1"] == 0


def test_settlement_cut_and_short_payment():
    pay = AUDIT["expected_payable"]
    c = leak({"stage": "paid", "settled_amount": pay - 2000, "received_amount": round((pay - 2000) * 0.9) - 500},
             {"final_approved": pay, "settled_at": NOW, "paid_at": NOW})["codes"]
    assert c["L4"] == 2000 and c["L5"] == 500


def test_rejection_and_silent_write_off():
    old = NOW - timedelta(days=90)
    assert leak({"stage": "rejected", "approved_amount": 40000}, {"final_approved": 40000, "rejected_at": NOW})["codes"]["L6"] == 40000
    c = leak({"stage": "rejected", "approved_amount": 40000}, {"final_approved": 40000, "rejected_at": old})["codes"]
    assert c["L7"] == 40000 and c["L6"] == 0
    c = leak({"stage": "rejected", "approved_amount": 40000}, {"final_approved": 40000, "rejected_at": old}, {"appeal"})["codes"]
    assert c["L6"] == 40000 and c["L7"] == 0          # chased: stays contestable


def test_where_it_went_adds_back_to_gross():
    pay = AUDIT["expected_payable"]
    case = {"stage": "paid", "settled_amount": pay - 2000, "received_amount": round((pay - 2000) * 0.9)}
    L = leak(case, {"final_approved": pay, "settled_at": NOW, "paid_at": NOW})
    w = lk.where_it_went(AUDIT, case, 10, L)
    parts = w["discount"] + w["family"] + w["leakage"] + w["tds"] + w["received"] + w["outstanding"]
    assert abs(parts - w["gross"]) <= 2, w


def test_scorecard():
    base = {"submitted_at": NOW - timedelta(days=40), "settled_at": NOW - timedelta(days=10), "paid_at": NOW - timedelta(days=5),
            "preauth_decided": True, "preauth_queried": False, "queries": 1, "preauth_breaches": 0, "discharge_breaches": 1,
            "claimed": 50000, "settled_amount": 45000, "approved_amount": 50000, "stage": "paid",
            "leak": {"codes": {k: 0 for k in lk.ORDER} | {"L4": 5000}, "hospital": 5000}}
    s = lk.scorecard([base, dict(base, stage="settled", paid_at=None, preauth_queried=True)])
    assert s["claims"] == 2 and s["first_time_preauth_pct"] == 50.0 and s["settled_vs_claimed_pct"] == 90.0
    assert s["days_submit_to_settle"] == 30.0 and s["ageing"]["31-60"] == 45000 and s["deductions_after_discharge"] == 10000
