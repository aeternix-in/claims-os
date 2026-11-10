"""Case lifecycle: the states a hospital case moves through and the allowed moves.

    draft ──► preauth_submitted ──► preauth_approved / preauth_partially_approved ──► claim_submitted
                     │        └──► preauth_rejected (terminal)                            │
                     ├──► preauth_query ──► preauth_query_answered ──► (decision)         ▼
                     │                 └──► preauth_submitted (resubmission, workflow 121)
                     └──► preauth_failed ──► preauth_submitted (retry)     claim_approved / claim_partially_approved ──► paid
                                                                           claim_rejected (terminal), claim_query, claim_failed
A direct claim without pre-auth is also allowed (draft ──► claim_submitted).
"""
from enum import StrEnum


class CaseStatus(StrEnum):
    DRAFT = "draft"
    PREAUTH_SUBMITTED = "preauth_submitted"
    PREAUTH_APPROVED = "preauth_approved"
    PREAUTH_PARTIALLY_APPROVED = "preauth_partially_approved"
    PREAUTH_REJECTED = "preauth_rejected"
    PREAUTH_QUERY = "preauth_query"
    PREAUTH_QUERY_ANSWERED = "preauth_query_answered"
    PREAUTH_FAILED = "preauth_failed"
    CLAIM_SUBMITTED = "claim_submitted"
    CLAIM_APPROVED = "claim_approved"
    CLAIM_PARTIALLY_APPROVED = "claim_partially_approved"
    CLAIM_REJECTED = "claim_rejected"
    CLAIM_QUERY = "claim_query"
    CLAIM_QUERY_ANSWERED = "claim_query_answered"
    CLAIM_FAILED = "claim_failed"
    PAID = "paid"


S = CaseStatus
TRANSITIONS: dict[CaseStatus, set[CaseStatus]] = {
    S.DRAFT: {S.PREAUTH_SUBMITTED, S.CLAIM_SUBMITTED},
    S.PREAUTH_SUBMITTED: {S.PREAUTH_APPROVED, S.PREAUTH_PARTIALLY_APPROVED, S.PREAUTH_REJECTED,
                          S.PREAUTH_QUERY, S.PREAUTH_FAILED},
    S.PREAUTH_FAILED: {S.PREAUTH_SUBMITTED},
    # Query: answer via communication/on_request (19), or replace the pre-auth (resubmission 121)
    S.PREAUTH_QUERY: {S.PREAUTH_QUERY_ANSWERED, S.PREAUTH_SUBMITTED},
    S.PREAUTH_QUERY_ANSWERED: {S.PREAUTH_APPROVED, S.PREAUTH_PARTIALLY_APPROVED, S.PREAUTH_REJECTED,
                               S.PREAUTH_QUERY},
    S.PREAUTH_APPROVED: {S.CLAIM_SUBMITTED},
    S.PREAUTH_PARTIALLY_APPROVED: {S.CLAIM_SUBMITTED},
    S.PREAUTH_REJECTED: set(),
    S.CLAIM_SUBMITTED: {S.CLAIM_APPROVED, S.CLAIM_PARTIALLY_APPROVED, S.CLAIM_REJECTED,
                        S.CLAIM_QUERY, S.CLAIM_FAILED},
    S.CLAIM_FAILED: {S.CLAIM_SUBMITTED},
    S.CLAIM_QUERY: {S.CLAIM_QUERY_ANSWERED},          # claims only accept new (15) or query update (161)
    S.CLAIM_QUERY_ANSWERED: {S.CLAIM_APPROVED, S.CLAIM_PARTIALLY_APPROVED, S.CLAIM_REJECTED, S.CLAIM_QUERY},
    S.CLAIM_APPROVED: {S.PAID},
    S.CLAIM_PARTIALLY_APPROVED: {S.PAID},
    S.CLAIM_REJECTED: set(),
    S.PAID: set(),
}


class InvalidTransition(Exception):
    def __init__(self, current: str, target: str):
        super().__init__(f"cannot move case from '{current}' to '{target}'")
        self.current, self.target = current, target


def check(current: str, target: str) -> None:
    if CaseStatus(target) not in TRANSITIONS[CaseStatus(current)]:
        raise InvalidTransition(current, target)


def submitted(workflow: str) -> CaseStatus:
    return CaseStatus(f"{workflow}_submitted")


def failed(workflow: str) -> CaseStatus:
    return CaseStatus(f"{workflow}_failed")


def decided(workflow: str, decision: str) -> CaseStatus:
    """decision: approved | partially_approved | rejected | query"""
    return CaseStatus(f"{workflow}_{decision}")
