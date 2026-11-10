"""Claim state machine. Pure: no database. Every state move in the system goes
through `plan()`; the repository then persists exactly what it returns.

Stages mirror the CTO's demo UI (17 stages)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

STAGES = [
    "admitted", "preauth", "preauth_pending", "preauth_query", "denied",
    "in_treatment", "discharge", "discharge_pending", "discharge_query", "discharged",
    "claim", "submitted", "claim_query", "settled", "rejected", "paid", "closed",
]

DESK, DOCTOR, ACCOUNTS, OWNER, ADMIN = "desk", "doctor", "accounts", "owner", "admin"
SYSTEM = "system"   # payer decisions arriving over NHCX
ANY_STAFF = {DESK, DOCTOR, ACCOUNTS, OWNER, ADMIN}

# IRDAI Master Circular on Health Insurance, May 2024
CLOCK_SOURCE = "IRDAI Master Circular on Health Insurance Business, 29 May 2024"


class TransitionError(Exception):
    """Move not allowed. `code` is stable for the UI."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


Guard = Callable[[dict, dict], str | None]  # (case, payload) -> error message or None


@dataclass(frozen=True)
class Transition:
    event: str
    sources: frozenset[str]
    target: str
    roles: frozenset[str]
    start_clock: str | None = None   # 'preauth' | 'discharge'
    stop_clock: str | None = None
    guard: Guard | None = None
    set_fields: Callable[[dict, dict], dict] | None = None


def _need(*keys: str) -> Guard:
    def g(case: dict, payload: dict) -> str | None:
        missing = [k for k in keys if payload.get(k) in (None, "")]
        return f"missing: {', '.join(missing)}" if missing else None
    return g


def _positive_amount(key: str) -> Guard:
    def g(case: dict, payload: dict) -> str | None:
        v = payload.get(key)
        try:
            return None if v is not None and float(v) >= 0 else f"{key} must be a non-negative number"
        except (TypeError, ValueError):
            return f"{key} must be a number"
    return g


def _all(*guards: Guard) -> Guard:
    def g(case: dict, payload: dict) -> str | None:
        for x in guards:
            if (err := x(case, payload)):
                return err
        return None
    return g


def _copy(*keys: str):
    return lambda case, p: {k: p[k] for k in keys if k in p}


T = Transition
F = frozenset
TRANSITIONS: list[Transition] = [
    # admission -> pre-auth
    T("start_preauth", F({"admitted"}), "preauth", F({DESK})),
    T("submit_preauth", F({"preauth"}), "preauth_pending", F({DESK}),
      start_clock="preauth", guard=_all(_need("estimate"), _positive_amount("estimate")),
      set_fields=_copy("estimate", "provisional_dx", "icd10")),
    T("preauth_query", F({"preauth_pending"}), "preauth_query", F({DESK, SYSTEM}),
      stop_clock="preauth", guard=_need("query_text")),
    T("answer_preauth_query", F({"preauth_query"}), "preauth_pending", F({DESK, DOCTOR}),
      start_clock="preauth", guard=_need("reply_text")),
    T("preauth_approved", F({"preauth_pending"}), "in_treatment", F({DESK, SYSTEM}),
      stop_clock="preauth", guard=_positive_amount("approved_amount"),
      set_fields=lambda c, p: {"approved_amount": p["approved_amount"]}),
    T("preauth_denied", F({"preauth_pending", "preauth_query"}), "denied", F({DESK, SYSTEM}),
      stop_clock="preauth", guard=_need("reason")),
    # enhancement keeps the stage, raises approval
    T("enhancement_approved", F({"in_treatment"}), "in_treatment", F({DESK}),
      guard=_positive_amount("approved_amount"),
      set_fields=lambda c, p: {"approved_amount": p["approved_amount"]}),
    # discharge
    T("advise_discharge", F({"in_treatment"}), "discharge", F({DESK, DOCTOR})),
    T("request_final_auth", F({"discharge"}), "discharge_pending", F({DESK}),
      start_clock="discharge", guard=_all(_need("billed_amount"), _positive_amount("billed_amount")),
      set_fields=_copy("billed_amount")),
    T("discharge_query", F({"discharge_pending"}), "discharge_query", F({DESK}),
      stop_clock="discharge", guard=_need("query_text")),
    T("answer_discharge_query", F({"discharge_query"}), "discharge_pending", F({DESK, DOCTOR}),
      start_clock="discharge", guard=_need("reply_text")),
    T("final_auth_approved", F({"discharge_pending"}), "discharged", F({DESK}),
      stop_clock="discharge", guard=_positive_amount("approved_amount"),
      set_fields=lambda c, p: {"approved_amount": p["approved_amount"], "discharged_at": "now"}),
    # claim file and settlement
    T("prepare_claim", F({"discharged"}), "claim", F({DESK, ACCOUNTS})),
    T("submit_claim", F({"claim"}), "submitted", F({DESK, ACCOUNTS})),
    T("claim_query", F({"submitted"}), "claim_query", F({DESK, ACCOUNTS, SYSTEM}), guard=_need("query_text")),
    T("answer_claim_query", F({"claim_query"}), "submitted", F({DESK, ACCOUNTS, DOCTOR}),
      guard=_need("reply_text")),
    T("settlement_letter", F({"submitted"}), "settled", F({DESK, ACCOUNTS, SYSTEM}),
      guard=_positive_amount("settled_amount"), set_fields=_copy("settled_amount", "payer_ref")),
    T("claim_rejected", F({"submitted", "claim_query"}), "rejected", F({DESK, ACCOUNTS, SYSTEM}),
      guard=_need("reason")),
    # only a confirmed bank credit closes a claim
    T("bank_credit_matched", F({"settled"}), "paid", F({ACCOUNTS, OWNER}),
      guard=_all(_need("utr"), _positive_amount("received_amount")),
      set_fields=_copy("received_amount")),
    T("close", F({"paid", "denied", "rejected"}), "closed", F({ACCOUNTS, OWNER})),
    T("reopen_for_appeal", F({"rejected", "denied"}), "claim", F({DESK, ACCOUNTS}), guard=_need("reason")),
]
BY_EVENT = {t.event: t for t in TRANSITIONS}


@dataclass
class Plan:
    transition: Transition
    from_stage: str
    to_stage: str
    field_updates: dict = field(default_factory=dict)
    clock_start: tuple[str, datetime] | None = None   # (kind, due_at)
    clock_stop: str | None = None


def allowed_events(stage: str, role: str) -> list[str]:
    return [t.event for t in TRANSITIONS if stage in t.sources and role in t.roles]


def plan(case: dict, event: str, payload: dict, role: str, settings: dict | None = None,
         now: datetime | None = None) -> Plan:
    t = BY_EVENT.get(event)
    if t is None:
        raise TransitionError("unknown_event", f"Unknown event '{event}'")
    stage = case["stage"]
    if stage not in t.sources:
        raise TransitionError("bad_stage", f"'{event}' is not allowed from '{stage}'")
    if role not in t.roles:
        raise TransitionError("forbidden_role", f"Role '{role}' cannot do '{event}'")
    if t.guard and (err := t.guard(case, payload)):
        raise TransitionError("guard_failed", err)

    settings = settings or {}
    now = now or datetime.now(timezone.utc)
    p = Plan(t, stage, t.target)
    if t.set_fields:
        p.field_updates = t.set_fields(case, payload)
    if t.start_clock:
        mins = settings.get("preauthSlaMin", 60) if t.start_clock == "preauth" else settings.get("dischargeSlaMin", 180)
        p.clock_start = (t.start_clock, now + timedelta(minutes=mins))
    p.clock_stop = t.stop_clock
    return p
