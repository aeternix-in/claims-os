"""The full journey with JWE on both sides: every message on the wire is encrypted."""
import json

import pytest

pytest.importorskip("nhcx_mock")

from aeternix_core import store  # noqa: E402
from aeternix_core.samples import sample_preauth  # noqa: E402


def test_full_lifecycle_encrypted(system_jwe):
    ax, mock, _ = system_jwe
    cid = "JWE-CASE-1"
    assert ax.post("/cases", json=sample_preauth(cid).model_dump(mode="json")).status_code == 201

    r = ax.post(f"/cases/{cid}/submit/preauth")
    assert r.json()["exchange"]["status"] == "acked", r.text
    case = ax.get(f"/cases/{cid}").json()
    assert case["status"] == "preauth_approved"
    assert case["last_decision"]["eligible"] == 70000

    ax.post(f"/cases/{cid}/submit/claim")
    assert ax.get(f"/cases/{cid}").json()["status"] == "claim_approved"
    claim_corr = next(x["correlation_id"] for x in ax.get(f"/cases/{cid}").json()["exchanges"]
                      if x["workflow"] == "claim" and x["direction"] == "outbound")
    mock.post(f"/mock/paymentnotice/{claim_corr}")
    case = ax.get(f"/cases/{cid}").json()
    assert case["status"] == "paid"

    # Every stored message body (both directions) is an encrypted compact JWE; no FHIR in clear.
    with store.session() as s:
        bodies = [x.body for x in s.query(store.ExchangeRow).filter_by(case_id=cid)]
    assert len(bodies) == 6
    for b in bodies:
        assert isinstance(b["payload"], str) and len(b["payload"].split(".")) == 5
        assert "Adarsh" not in json.dumps(b) and "ClaimResponse" not in json.dumps(b)
    mock_txns = mock.get("/mock/transactions", params={"correlation_id": claim_corr}).json()
    assert {t["workflow"]: t["status"] for t in mock_txns}["paymentnotice"] == "acknowledged"


def test_missing_insurer_key_is_a_clear_error(system_jwe, tmp_path):
    ax, _, _ = system_jwe
    (tmp_path / "keys" / "mock-payer@hcx.public.pem").unlink()
    cid = "JWE-CASE-2"
    ax.post("/cases", json=sample_preauth(cid).model_dump(mode="json"))
    r = ax.post(f"/cases/{cid}/submit/preauth")
    assert r.status_code == 422
    assert "mock-payer@hcx" in r.json()["detail"]
    assert ax.get(f"/cases/{cid}").json()["status"] == "draft"   # nothing was sent


def test_plain_callback_rejected_in_jwe_mode(system_jwe):
    ax, _, _ = system_jwe
    body = {"type": "JWEPayload", "payload": {"headers": {}, "fhir": {}}}
    r = ax.post("/hcx/v1/preauth/on_submit", json=body, headers={"bearer_auth": "Bearer x"})
    assert r.json()["error"]["code"] == "ERR_INVALID_ENCRYPTION"


def test_rejections_and_timeouts_work_encrypted(system_jwe):
    ax, _, mock_cfg = system_jwe
    mock_cfg.default_scenario = "reject"
    ax.post("/cases", json=sample_preauth("JWE-R").model_dump(mode="json"))
    ax.post("/cases/JWE-R/submit/preauth")
    assert ax.get("/cases/JWE-R").json()["status"] == "preauth_rejected"


def test_query_flow_encrypted(system_jwe):
    ax, _, mock_cfg = system_jwe
    mock_cfg.default_scenario = "query"
    ax.post("/cases", json=sample_preauth("JWE-Q").model_dump(mode="json"))
    ax.post("/cases/JWE-Q/submit/preauth")
    assert ax.get("/cases/JWE-Q").json()["status"] == "preauth_query"
    r = ax.post("/cases/JWE-Q/query-reply", json={"message": "attached"})
    assert r.json()["exchange"]["status"] == "acked", r.text
    assert ax.get("/cases/JWE-Q").json()["status"] == "preauth_approved"
