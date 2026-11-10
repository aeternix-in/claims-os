import pytest

from aeternix_core.domain import lifecycle
from aeternix_core.domain.lifecycle import CaseStatus as S
from aeternix_core.nhcx.responses import UnreadableResponse, interpret


def test_happy_path_transitions_allowed():
    path = [S.DRAFT, S.PREAUTH_SUBMITTED, S.PREAUTH_APPROVED, S.CLAIM_SUBMITTED, S.CLAIM_APPROVED, S.PAID]
    for a, b in zip(path, path[1:]):
        lifecycle.check(a, b)


@pytest.mark.parametrize("a,b", [(S.DRAFT, S.PAID), (S.PREAUTH_REJECTED, S.CLAIM_SUBMITTED),
                                 (S.PAID, S.CLAIM_SUBMITTED), (S.PREAUTH_SUBMITTED, S.PREAUTH_SUBMITTED)])
def test_illegal_transitions(a, b):
    with pytest.raises(lifecycle.InvalidTransition):
        lifecycle.check(a, b)


def test_retry_after_failure_and_query():
    lifecycle.check(S.PREAUTH_FAILED, S.PREAUTH_SUBMITTED)
    lifecycle.check(S.PREAUTH_QUERY, S.PREAUTH_SUBMITTED)          # resubmission (121)
    lifecycle.check(S.PREAUTH_QUERY, S.PREAUTH_QUERY_ANSWERED)     # communication reply (19)
    lifecycle.check(S.CLAIM_QUERY, S.CLAIM_QUERY_ANSWERED)
    with pytest.raises(lifecycle.InvalidTransition):
        lifecycle.check(S.CLAIM_QUERY, S.CLAIM_SUBMITTED)          # claims accept only 15 / 161


def cr_bundle(outcome, submitted=None, eligible=None):
    total = []
    if submitted is not None:
        total.append({"category": {"coding": [{"code": "submitted"}]}, "amount": {"value": submitted}})
    if eligible is not None:
        total.append({"category": {"coding": [{"code": "eligpercent"}]}, "amount": {"value": eligible}})
    return {"entry": [{"resource": {"resourceType": "ClaimResponse", "outcome": outcome, "total": total}}]}


@pytest.mark.parametrize("bundle,expected", [
    (cr_bundle("complete", 100, 100), "approved"),
    (cr_bundle("complete", 100, 40), "partially_approved"),
    (cr_bundle("error", 100), "rejected"),
    (cr_bundle("partial"), "query"),
    (cr_bundle("queued"), "submitted"),
])
def test_interpret(bundle, expected):
    assert interpret(bundle).decision == expected


def test_interpret_rejects_garbage():
    with pytest.raises(UnreadableResponse):
        interpret({"entry": []})
    with pytest.raises(UnreadableResponse):
        interpret(cr_bundle("weird"))
