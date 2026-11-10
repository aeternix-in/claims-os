"""Aeternix against the NHA Implementation Guide rules enforced by nhcx-mock."""
import pytest

pytest.importorskip("nhcx_mock")

from aeternix_core import store  # noqa: E402
from aeternix_core.samples import sample_preauth  # noqa: E402


def create(ax, cid):
    assert ax.post("/cases", json=sample_preauth(cid).model_dump(mode="json")).status_code == 201


def sent_headers(cid, workflow, action="submit"):
    with store.session() as s:
        rows = s.query(store.ExchangeRow).filter_by(case_id=cid, workflow=workflow, action=action,
                                                    direction="outbound").order_by(store.ExchangeRow.id).all()
        return [r.plain["headers"] for r in rows]


def test_workflow_codes_on_submit(system):
    ax, _, _ = system
    create(ax, "W-1")
    ax.post("/cases/W-1/submit/preauth")
    ax.post("/cases/W-1/submit/claim")
    assert sent_headers("W-1", "preauth")[0]["x-hcx-workflow_id"] == "12"
    assert sent_headers("W-1", "claim")[0]["x-hcx-workflow_id"] == "15"
    assert ax.get("/cases/W-1").json()["status"] == "claim_approved"


def test_query_reply_flow(system):
    """Queried decision + communication/request -> staff reply (19) -> final approval."""
    ax, _, mock_cfg = system
    mock_cfg.default_scenario = "query"
    create(ax, "Q-1")
    ax.post("/cases/Q-1/submit/preauth")
    case = ax.get("/cases/Q-1").json()
    assert case["status"] == "preauth_query"
    assert "ECG" in case["open_query"]["remarks"]

    r = ax.post("/cases/Q-1/query-reply", json={
        "message": "ECG report and cardiologist notes attached.",
        "attachments": [{"title": "ecg.pdf", "content_type": "application/pdf", "data": "JVBERi0xLjQK"}]})
    assert r.status_code == 200, r.text
    assert r.json()["exchange"]["status"] == "acked"
    reply = sent_headers("Q-1", "communication", "on_request")[0]
    assert reply["x-hcx-workflow_id"] == "19" and reply["x-hcx-status"] == "response.complete"

    case = ax.get("/cases/Q-1").json()
    assert case["status"] == "preauth_approved"        # mock sent the final decision after the reply
    assert case["open_query"] is None
    assert [h["to"] for h in case["history"]] == [
        "draft", "preauth_submitted", "preauth_query", "preauth_query_answered", "preauth_approved"]


def test_query_reply_without_open_query(system):
    ax, _, _ = system
    create(ax, "Q-2")
    assert ax.post("/cases/Q-2/query-reply", json={"message": "x"}).status_code == 409


def test_resubmission_after_query_uses_121(system):
    ax, _, mock_cfg = system
    mock_cfg.default_scenario = "query"
    create(ax, "Q-3")
    ax.post("/cases/Q-3/submit/preauth")
    mock_cfg.default_scenario = "approve"
    ax.post("/cases/Q-3/submit/preauth")              # revised pre-auth instead of a reply
    assert [h["x-hcx-workflow_id"] for h in sent_headers("Q-3", "preauth")] == ["12", "121"]
    assert ax.get("/cases/Q-3").json()["status"] == "preauth_approved"


def test_payment_ack_is_a_task(system):
    ax, mock, _ = system
    create(ax, "P-1")
    ax.post("/cases/P-1/submit/preauth")
    ax.post("/cases/P-1/submit/claim")
    corr = sent_headers("P-1", "claim")[0]["x-hcx-correlation_id"]
    mock.post(f"/mock/paymentnotice/{corr}")
    case = ax.get("/cases/P-1").json()
    assert case["status"] == "paid"
    assert "UTR" in case["history"][-1]["note"]
    with store.session() as s:
        ack = s.query(store.ExchangeRow).filter_by(case_id="P-1", action="on_request").one()
        types = [e["resource"]["resourceType"] for e in ack.plain["fhir"]["entry"]]
        output = ack.plain["fhir"]["entry"][0]["resource"]["output"][0]
    assert types == ["Task"] and output["valueString"] == "7612345"
    txns = mock.get("/mock/transactions", params={"correlation_id": corr}).json()
    assert {t["workflow"]: t["status"] for t in txns}["paymentnotice"] == "acknowledged"


def test_tpa_routing(system):
    """Messages go to processingid (here: the mock payer's code set explicitly)."""
    ax, _, _ = system
    case = sample_preauth("T-1")
    case.insurer.processing_code = "mock-payer@hcx"
    ax.post("/cases", json=case.model_dump(mode="json"))
    ax.post("/cases/T-1/submit/preauth")
    assert sent_headers("T-1", "preauth")[0]["x-hcx-recipient_code"] == "mock-payer@hcx"


def test_failed_reply_reopens_query(system, monkeypatch):
    ax, _, mock_cfg = system
    mock_cfg.default_scenario = "query"
    create(ax, "Q-4")
    ax.post("/cases/Q-4/submit/preauth")
    from aeternix_core.config import get_settings
    monkeypatch.setattr(get_settings(), "nhcx_token", "invalid")        # gateway rejects the reply
    r = ax.post("/cases/Q-4/query-reply", json={"message": "docs"})
    assert r.json()["exchange"]["status"] == "failed"
    case = ax.get("/cases/Q-4").json()
    assert case["status"] == "preauth_query" and case["open_query"] is not None   # staff can retry
