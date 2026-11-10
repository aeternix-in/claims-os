"""Bill audit: what the payer will pay and what falls on the family.
Ported line-for-line from the CTO demo (function `gt`) so numbers match.

Sources
- Proportionate deduction: IRDAI Guidelines on Standardisation of Exclusions / room-rent
  proportionate deduction (June 2020). Not applied to ICU, diagnostics, pharmacy, implants,
  nor when the hospital does not bill by room category.
- Non-payable items: IRDAI list of items excluded (Annexure, Master Circular 2024).
- TDS: section 194J, CBDT Circular 8/2009 (10%).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

Category = Literal["room", "icu", "ot", "surgeon", "anaesthesia", "consult", "procedure",
                   "investigations", "pharmacy", "consumables", "implants", "misc", "nonpayable"]
CATEGORIES: list[str] = ["room", "icu", "ot", "surgeon", "anaesthesia", "consult", "procedure",
                         "investigations", "pharmacy", "consumables", "implants", "misc", "nonpayable"]
CATEGORY_LABEL = {
    "room": "Room and nursing", "icu": "ICU charges", "ot": "OT charges", "surgeon": "Surgeon fees",
    "anaesthesia": "Anaesthetist fees", "consult": "Consultant visits", "investigations": "Diagnostics",
    "pharmacy": "Pharmacy", "consumables": "Consumables", "implants": "Implants and devices",
    "procedure": "Procedure charges", "nonpayable": "Non-medical items", "misc": "Other charges",
}
# room-linked: cut in proportion when the room is above eligibility
PROPORTIONATE = ["ot", "surgeon", "anaesthesia", "consult", "procedure", "consumables", "misc"]
# never cut in proportion
PROPORTIONATE_EXEMPT = ["icu", "investigations", "pharmacy", "implants"]

ROOM_LABEL = {"general": "General ward", "twin": "Twin sharing", "single": "Single room",
              "deluxe": "Deluxe room", "icu": "ICU"}
DEFAULT_ROOMS = {"general": 2500, "twin": 4200, "single": 6500, "deluxe": 9000, "icu": 12500}


def _r(x: float) -> int:
    """JS Math.round (half up), so results match the demo exactly."""
    import math
    return int(math.floor(x + 0.5))


@dataclass
class BillLine:
    category: str
    description: str
    qty: float
    rate: float
    mou_rate: float | None = None
    to_payer: bool = True     # False = family pays directly (e.g. a room upgrade they chose)

    @property
    def amount(self) -> float:
        return _r(self.qty * self.rate)   # rounded to the rupee, per line


@dataclass
class Policy:
    sum_insured: float
    balance: float
    copay_pct: float = 0
    sub_limit: float | None = None
    # {"kind":"pctSI","roomPct":1,"icuPct":2} | {"kind":"category","max":"twin"} | {"kind":"none"}
    room_rule: dict = field(default_factory=lambda: {"kind": "none"})


@dataclass
class PayerRules:
    basis: Literal["itemised", "package"] = "itemised"
    mou_discount_pct: float = 0
    discount_excludes: list[str] = field(default_factory=lambda: ["pharmacy", "implants"])
    applies_proportionate: bool = True
    package_rates: dict[str, float] = field(default_factory=dict)
    tds_pct: float = 10


@dataclass
class Hospital:
    rooms: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_ROOMS))
    differential_billing: bool = True


def room_eligibility(policy: Policy, hospital: Hospital) -> dict:
    rr = policy.room_rule or {"kind": "none"}
    if rr.get("kind") == "pctSI":
        return {"room": _r(policy.sum_insured * rr["roomPct"] / 100),
                "icu": _r(policy.sum_insured * rr["icuPct"] / 100),
                "label": f"{rr['roomPct']}% of sum insured a day for the room, {rr['icuPct']}% for ICU"}
    if rr.get("kind") == "category":
        return {"room": hospital.rooms[rr["max"]], "icu": None,
                "label": f"Up to {ROOM_LABEL[rr['max']].lower()}"}
    return {"room": None, "icu": None, "label": "No room limit"}


def adjudicate(bill: list[BillLine], policy: Policy, rules: PayerRules, hospital: Hospital,
               procedure_id: str | None = None, room_category: str | None = None,
               authorised: float = 0) -> dict:
    use_package = rules.basis == "package" or (procedure_id in rules.package_rates)
    package_rate = rules.package_rates.get(procedure_id) if procedure_id else None

    gross = family_direct = nonpayable_total = nonpayable_to_payer = above_mou = claimed_gross = 0.0
    above_mou_lines: list[dict] = []
    by_cat: dict[str, float] = {}
    for i, ln in enumerate(bill):
        amt = ln.amount
        gross += amt
        if ln.category == "nonpayable":
            nonpayable_total += amt
        if not ln.to_payer:
            family_direct += amt
            continue
        claimed_gross += amt
        if ln.category == "nonpayable":
            nonpayable_to_payer += amt
            continue
        net = amt
        if ln.mou_rate is not None and ln.rate > ln.mou_rate:
            excess = _r(ln.qty * (ln.rate - ln.mou_rate))
            above_mou += excess
            above_mou_lines.append({"index": i, "excess": excess})
            net = amt - excess
        by_cat[ln.category] = by_cat.get(ln.category, 0) + net

    elig = room_eligibility(policy, hospital)
    room_line = next((l for l in bill if l.category == "room"), None)
    if room_line:
        room_rate = min(room_line.rate, room_line.mou_rate if room_line.mou_rate is not None else room_line.rate)
    else:
        room_rate = hospital.rooms.get(room_category or "general", 0)
    room_days = room_line.qty if room_line else 0

    discount = pd_total = pd_room_excess = 0
    pd_by_cat: dict[str, int] = {}
    ratio, pd_applies, note, exempt_base = 1.0, False, "", 0

    if use_package and package_rate is not None:
        base = claimed_gross - nonpayable_to_payer - above_mou
        discount = max(0, base - package_rate)
        claimed = min(base, package_rate) + nonpayable_to_payer + above_mou
        admissible = min(base, package_rate)
        note = "Package rate: no proportionate deduction applies."
    else:
        d = rules.mou_discount_pct / 100
        for cat in CATEGORIES:
            v = by_cat.get(cat)
            if v and cat not in rules.discount_excludes:
                discount += v * d
        discount = _r(discount)
        claimed = claimed_gross - discount

        def after_disc(cat: str) -> float:
            return by_cat.get(cat, 0) * (1 if cat in rules.discount_excludes else 1 - d)

        cap = elig["room"]
        if cap is not None and room_rate > cap:
            ratio = cap / room_rate
            if not rules.applies_proportionate:
                note = "This payer does not apply proportionate deduction."
            elif not hospital.differential_billing:
                note = "The hospital does not bill by room category, so proportionate deduction cannot apply."
            else:
                pd_applies = True
                pd_room_excess = _r(after_disc("room") * (1 - ratio))
                for cat in PROPORTIONATE:
                    v = after_disc(cat)
                    if v > 0:
                        pd_by_cat[cat] = _r(v * (1 - ratio))
                pd_total = pd_room_excess + sum(pd_by_cat.values())
                for cat in PROPORTIONATE_EXEMPT:
                    exempt_base += _r(after_disc(cat))
                note = (f"Room at ₹{room_rate:,.0f} a day against an eligible ₹{cap:,.0f}: "
                        f"the payer pays {ratio * 100:.0f}% of room-linked charges.")
        elif cap is None:
            note = "The policy has no room limit."
        else:
            note = f"Room within the eligible ₹{cap:,.0f} a day."
        admissible = claimed - nonpayable_to_payer - above_mou - pd_total

    admissible = max(0, _r(admissible))
    copay = _r(admissible * policy.copay_pct / 100)
    after = admissible - copay
    sub_limit_excess = max(0, after - policy.sub_limit) if policy.sub_limit is not None else 0
    after -= sub_limit_excess
    si_excess = max(0, after - policy.balance)
    expected_payable = after - si_excess
    policy_terms = copay + sub_limit_excess + si_excess
    unauthorised_excess = max(0, expected_payable - authorised) if authorised > 0 else 0

    return {
        "gross": gross, "family_direct": family_direct, "claimed_gross": claimed_gross,
        "nonpayable_total": nonpayable_total, "nonpayable_to_payer": nonpayable_to_payer,
        "above_mou": above_mou, "above_mou_lines": above_mou_lines,
        "discount": discount, "package_rate": package_rate, "claimed": claimed,
        "room_cap": elig["room"], "room_rule_label": elig["label"], "room_rate": room_rate,
        "room_days": room_days, "ratio": ratio, "pd_applies": pd_applies, "pd_note": note,
        "pd": pd_total, "pd_room_excess": pd_room_excess, "pd_by_category": pd_by_cat,
        "pd_exempt_base": exempt_base, "admissible": admissible, "copay": copay,
        "sub_limit_excess": sub_limit_excess, "si_excess": si_excess, "policy_terms": policy_terms,
        "expected_payable": expected_payable, "authorised": authorised,
        "unauthorised_excess": unauthorised_excess,
        # what the family should expect to pay (L3a + L3b + L3d)
        "family": {"nonpayable": nonpayable_total, "room_proportion": pd_total, "copay": policy_terms,
                   "total": nonpayable_total + pd_total + policy_terms},
    }


def room_advice(policy: Policy, hospital: Hospital) -> dict:
    """At admission: the best room the policy fully covers, so there is no proportionate cut."""
    cap = room_eligibility(policy, hospital)["room"]
    if cap is None:
        return {"cap": None, "advice": "No room limit: any room is covered.", "best": None}
    fits = [(k, v) for k, v in hospital.rooms.items() if k != "icu" and v <= cap]
    best = max(fits, key=lambda kv: kv[1])[0] if fits else None
    msg = (f"Eligible up to ₹{cap:,.0f} a day. Advise {ROOM_LABEL[best].lower()} or below to avoid a proportionate cut."
           if best else f"Eligible up to ₹{cap:,.0f} a day: below every room category, so expect a proportionate cut.")
    return {"cap": cap, "advice": msg, "best": best}


def expected_credit(settled: float, tds_pct: float = 10) -> dict:
    tds = _r(settled * tds_pct / 100)
    return {"settled": settled, "tds": tds, "expected_credit": settled - tds,
            "tds_note": "Section 194J; claimable as tax credit, not a loss"}


def to_dict(x) -> dict:
    return asdict(x)
