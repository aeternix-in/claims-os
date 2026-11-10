import pytest

from app import state_machine as sm

HAPPY_PATH = [
    ("start_preauth", "desk", {}),
    ("submit_preauth", "desk", {"estimate": 85000}),
    ("preauth_query", "desk", {"query_text": "Send USG report"}),
    ("answer_preauth_query", "doctor", {"reply_text": "Attached"}),
    ("preauth_approved", "desk", {"approved_amount": 70000}),
    ("enhancement_approved", "desk", {"approved_amount": 90000}),
    ("advise_discharge", "doctor", {}),
    ("request_final_auth", "desk", {"billed_amount": 96000}),
    ("final_auth_approved", "desk", {"approved_amount": 88000}),
    ("prepare_claim", "desk", {}),
    ("submit_claim", "desk", {}),
    ("settlement_letter", "accounts", {"settled_amount": 86500}),
    ("bank_credit_matched", "accounts", {"utr": "UTR123", "received_amount": 77850}),
    ("close", "owner", {}),
]


def test_walks_admission_to_closed():
    case = {"stage": "admitted"}
    for event, role, payload in HAPPY_PATH:
        p = sm.plan(case, event, payload, role)
        case = {**case, **p.field_updates, "stage": p.to_stage}
    assert case["stage"] == "closed"
    assert case["approved_amount"] == 88000


def test_every_stage_reachable():
    reachable, frontier = {"admitted"}, ["admitted"]
    while frontier:
        s = frontier.pop()
        for t in sm.TRANSITIONS:
            if s in t.sources and t.target not in reachable:
                reachable.add(t.target); frontier.append(t.target)
    assert reachable == set(sm.STAGES)


def test_clocks():
    p = sm.plan({"stage": "preauth"}, "submit_preauth", {"estimate": 1}, "desk", {"preauthSlaMin": 60})
    assert p.clock_start[0] == "preauth"
    p = sm.plan({"stage": "preauth_pending"}, "preauth_approved", {"approved_amount": 1}, "desk")
    assert p.clock_stop == "preauth"


@pytest.mark.parametrize("case,event,payload,role,code", [
    ({"stage": "admitted"}, "submit_claim", {}, "desk", "bad_stage"),
    ({"stage": "settled"}, "bank_credit_matched", {"utr": "x", "received_amount": 1}, "desk", "forbidden_role"),
    ({"stage": "settled"}, "bank_credit_matched", {"received_amount": 1}, "accounts", "guard_failed"),
    ({"stage": "preauth"}, "submit_preauth", {"estimate": -5}, "desk", "guard_failed"),
    ({"stage": "admitted"}, "teleport", {}, "desk", "unknown_event"),
])
def test_refusals(case, event, payload, role, code):
    with pytest.raises(sm.TransitionError) as e:
        sm.plan(case, event, payload, role)
    assert e.value.code == code


def test_only_bank_credit_reaches_paid():
    assert [t.event for t in sm.TRANSITIONS if t.target == "paid"] == ["bank_credit_matched"]
