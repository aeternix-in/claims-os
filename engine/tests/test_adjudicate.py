from app.adjudicate import (BillLine, Hospital, PayerRules, Policy, adjudicate, expected_credit,
                            room_advice)

H = Hospital()


def lapchole_bill(room="single", days=2, rate=None):
    r = rate or H.rooms[room]
    return [
        BillLine("room", "Single room", days, r, r),
        BillLine("ot", "OT", 1, 22000), BillLine("surgeon", "Surgeon", 1, 32000),
        BillLine("anaesthesia", "Anaesthetist", 1, 9000), BillLine("investigations", "Labs", 1, 7000),
        BillLine("pharmacy", "Pharmacy", 1, 9000), BillLine("consumables", "Consumables", 1, 12000),
        BillLine("consult", "Visits", 4, 1000),
        BillLine("nonpayable", "Admission kit", 1, 800),
    ]


def test_no_cap_only_discount_and_nonpayable():
    rules = PayerRules(mou_discount_pct=10)
    pol = Policy(sum_insured=500000, balance=500000)
    a = adjudicate(lapchole_bill(), pol, rules, H)
    # discount on everything except pharmacy & implants (nonpayable category also discounted? no: not in by_cat)
    assert a["discount"] == round((13000 + 22000 + 32000 + 9000 + 7000 + 12000 + 4000) * 0.10)
    assert a["admissible"] == a["claimed"] - 800
    assert not a["pd_applies"]
    assert a["family"]["nonpayable"] == 800


def test_proportionate_cut_room_above_1pct_si():
    # 1% of 3L = ₹3,000/day eligible, single room ₹6,500 → ratio 0.4615
    pol = Policy(sum_insured=300000, balance=300000, room_rule={"kind": "pctSI", "roomPct": 1, "icuPct": 2})
    a = adjudicate(lapchole_bill(), pol, PayerRules(mou_discount_pct=0), H)
    ratio = 3000 / 6500
    assert a["pd_applies"] and abs(a["ratio"] - ratio) < 1e-9
    assert a["pd_room_excess"] == round(13000 * (1 - ratio))
    assert a["pd_by_category"]["surgeon"] == round(32000 * (1 - ratio))
    assert "pharmacy" not in a["pd_by_category"] and "investigations" not in a["pd_by_category"]
    assert a["admissible"] == a["claimed"] - 800 - a["pd"]


def test_no_cut_when_hospital_has_flat_billing_or_payer_waives():
    pol = Policy(sum_insured=300000, balance=300000, room_rule={"kind": "pctSI", "roomPct": 1, "icuPct": 2})
    assert not adjudicate(lapchole_bill(), pol, PayerRules(), Hospital(differential_billing=False))["pd_applies"]
    assert not adjudicate(lapchole_bill(), pol, PayerRules(applies_proportionate=False), H)["pd_applies"]


def test_copay_sublimit_balance_order():
    pol = Policy(sum_insured=100000, balance=50000, copay_pct=20, sub_limit=60000)
    a = adjudicate(lapchole_bill(), pol, PayerRules(), H)
    adm = a["admissible"]
    assert a["copay"] == round(adm * 0.2)
    after = adm - a["copay"]
    assert a["sub_limit_excess"] == max(0, after - 60000)
    assert a["expected_payable"] == 50000          # capped by balance
    assert a["policy_terms"] == a["copay"] + a["sub_limit_excess"] + a["si_excess"]


def test_package_rate():
    rules = PayerRules(basis="package", package_rates={"lapchole": 60000})
    pol = Policy(sum_insured=500000, balance=500000, room_rule={"kind": "pctSI", "roomPct": 1, "icuPct": 2})
    a = adjudicate(lapchole_bill(), pol, rules, H, procedure_id="lapchole")
    assert a["admissible"] == 60000 and not a["pd_applies"]


def test_above_mou_rate():
    bill = [BillLine("ot", "OT", 1, 25000, mou_rate=22000)]
    a = adjudicate(bill, Policy(500000, 500000), PayerRules(), H)
    assert a["above_mou"] == 3000 and a["admissible"] == 22000


def test_room_advice_and_tds():
    pol = Policy(500000, 500000, room_rule={"kind": "pctSI", "roomPct": 1, "icuPct": 2})
    assert room_advice(pol, H)["best"] == "twin"       # cap 5000 → twin 4200 fits, single 6500 doesn't
    assert expected_credit(86500) == {"settled": 86500, "tds": 8650, "expected_credit": 77850,
                                      "tds_note": "Section 194J; claimable as tax credit, not a loss"}
