"""Two real systems talking: aeternix-core <-> nhcx-mock, wired in-process.

aeternix -> mock : aeternix's NHCX client uses ASGITransport(mock_app)
mock -> aeternix : the mock's callback sender uses ASGITransport(aeternix_app)
"""
import pytest

pytest.importorskip("nhcx_mock")

from aeternix_core.samples import sample_preauth  # noqa: E402


def create(ax, case_id="AETX-CASE-0001"):
    case = sample_preauth(case_id)
    r = ax.post("/cases", json=case.model_dump(mode="json"))
    assert r.status_code == 201, r.text
    return case_id


def status(ax, case_id):
    return ax.get(f"/cases/{case_id}").json()["status"]


def test_full_lifecycle_draft_to_paid(system):
    ax, mock, _ = system
    cid = create(ax)
    assert status(ax, cid) == "draft"

    r = ax.post(f"/cases/{cid}/submit/preauth")
    assert r.status_code == 200, r.text
    assert r.json()["exchange"]["status"] == "acked"
    case = ax.get(f"/cases/{cid}").json()
    assert case["status"] == "preauth_approved"
    assert case["last_decision"]["preauth_ref"].startswith("PA-")
    assert case["last_decision"]["eligible"] == 70000

    assert ax.post(f"/cases/{cid}/submit/claim").status_code == 200
    assert status(ax, cid) == "claim_approved"

    claim_corr = next(x["correlation_id"] for x in ax.get(f"/cases/{cid}").json()["exchanges"]
                      if x["workflow"] == "claim" and x["direction"] == "outbound")
    assert mock.post(f"/mock/paymentnotice/{claim_corr}").status_code == 200

    case = ax.get(f"/cases/{cid}").json()
    assert case["status"] == "paid"
    assert [h["to"] for h in case["history"]] == [
        "draft", "preauth_submitted", "preauth_approved", "claim_submitted", "claim_approved", "paid"]
    # our acknowledgement reached the mock
    mock_txns = mock.get("/mock/transactions", params={"correlation_id": claim_corr}).json()
    assert {t["workflow"]: t["status"] for t in mock_txns}["paymentnotice"] == "acknowledged"
    ack_exchange = [x for x in case["exchanges"] if x["action"] == "on_request"][0]
    assert ack_exchange["status"] == "acked"


def test_preauth_rejected_blocks_claim(system):
    ax, _, mock_cfg = system
    mock_cfg.default_scenario = "reject"
    cid = create(ax)
    ax.post(f"/cases/{cid}/submit/preauth")
    assert status(ax, cid) == "preauth_rejected"
    r = ax.post(f"/cases/{cid}/submit/claim")
    assert r.status_code == 409


def test_partial_and_query(system):
    ax, _, mock_cfg = system
    mock_cfg.default_scenario = "partial"
    a = create(ax, "CASE-A")
    ax.post(f"/cases/{a}/submit/preauth")
    case = ax.get(f"/cases/{a}").json()
    assert case["status"] == "preauth_partially_approved"
    assert case["last_decision"]["eligible"] == 35000

    mock_cfg.default_scenario = "query"
    b = create(ax, "CASE-B")
    ax.post(f"/cases/{b}/submit/preauth")
    assert status(ax, b) == "preauth_query"
    mock_cfg.default_scenario = "approve"
    ax.post(f"/cases/{b}/submit/preauth")       # resubmit after query
    assert status(ax, b) == "preauth_approved"


def test_payer_timeout_leaves_case_waiting(system):
    ax, _, mock_cfg = system
    mock_cfg.default_scenario = "timeout"
    cid = create(ax)
    ax.post(f"/cases/{cid}/submit/preauth")
    assert status(ax, cid) == "preauth_submitted"


def test_gateway_rejection_marks_failed_then_retry(system, monkeypatch):
    ax, _, _ = system
    from aeternix_core.config import get_settings
    monkeypatch.setattr(get_settings(), "nhcx_token", "invalid")   # mock rejects this token
    cid = create(ax)
    r = ax.post(f"/cases/{cid}/submit/preauth")
    assert r.json()["exchange"]["status"] == "failed"
    assert "ERR_ACCESS_DENIED" in r.json()["exchange"]["error"]
    assert status(ax, cid) == "preauth_failed"

    monkeypatch.setattr(get_settings(), "nhcx_token", "aeternix-dev")
    ax.post(f"/cases/{cid}/submit/preauth")
    assert status(ax, cid) == "preauth_approved"


def test_network_failure_marks_failed(system, monkeypatch):
    ax, _, _ = system
    from aeternix_core.config import get_settings
    from aeternix_core.nhcx import client
    monkeypatch.setattr(client, "transport", None)
    monkeypatch.setattr(get_settings(), "nhcx_base_url", "http://127.0.0.1:9")  # nothing listens here
    cid = create(ax)
    r = ax.post(f"/cases/{cid}/submit/preauth")
    assert "network error" in r.json()["exchange"]["error"]
    assert status(ax, cid) == "preauth_failed"


def test_duplicate_callback_is_ignored(system):
    ax, _, _ = system
    cid = create(ax)
    ax.post(f"/cases/{cid}/submit/preauth")
    from aeternix_core import store
    with store.session() as s:
        callback = s.query(store.ExchangeRow).filter_by(case_id=cid, action="on_submit").one().body
    r = ax.post("/hcx/v1/preauth/on_submit", json=callback, headers={"bearer_auth": "Bearer x"})
    assert r.status_code == 202
    case = ax.get(f"/cases/{cid}").json()
    assert len([h for h in case["history"] if h["to"] == "preauth_approved"]) == 1


def test_callback_for_unknown_case_rejected(system):
    ax, _, _ = system
    body = {"type": "JWEPayload", "payload": {"headers": {
        "x-hcx-api_call_id": "a", "x-hcx-correlation_id": "nope", "x-hcx-workflow_id": "w",
        "x-hcx-request_id": "r", "x-hcx-timestamp": "t", "x-hcx-sender_code": "mock-payer@hcx",
        "x-hcx-recipient_code": "aeternix-provider@hcx", "x-hcx-status": "response.complete"},
        "fhir": {"resourceType": "Bundle", "entry": []}}}
    r = ax.post("/hcx/v1/preauth/on_submit", json=body, headers={"bearer_auth": "Bearer x"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "ERR_INVALID_CORRELATION_ID"


def test_callback_requires_bearer_auth(system):
    ax, _, _ = system
    r = ax.post("/hcx/v1/preauth/on_submit", json={"payload": {}})
    assert r.status_code == 401


def test_duplicate_case_and_unknown_case(system):
    ax, _, _ = system
    create(ax)
    assert ax.post("/cases", json=sample_preauth().model_dump(mode="json")).status_code == 409
    assert ax.get("/cases/NOPE").status_code == 404
