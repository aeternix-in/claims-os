import json

import pytest

pytest.importorskip("nhcx_mock")

from aeternix_core.samples import sample_preauth  # noqa: E402


@pytest.fixture
def demo(monkeypatch):
    from aeternix_core.config import get_settings

    def enable(on=True):
        monkeypatch.setattr(get_settings(), "demo_mode", on)
    return enable


def test_demo_endpoints_hidden_when_demo_off(system):
    ax, _, _ = system
    assert ax.get("/ui/config").json()["demo_mode"] is False
    assert ax.post("/demo/case").status_code == 404
    assert ax.post("/demo/cases/X/pay").status_code == 404


def test_scenario_ignored_when_demo_off(system):
    ax, _, _ = system
    ax.post("/cases", json=sample_preauth("D-0").model_dump(mode="json"))
    ax.post("/cases/D-0/submit/preauth?scenario=reject")
    assert ax.get("/cases/D-0").json()["status"] == "preauth_approved"   # mock default, not "reject"


def test_demo_journey_with_chosen_outcomes(system, demo):
    ax, _, _ = system
    demo()
    cid = ax.post("/demo/case").json()["case_id"]
    assert cid.startswith("DEMO-")

    ax.post(f"/cases/{cid}/submit/preauth?scenario=query")
    assert ax.get(f"/cases/{cid}").json()["status"] == "preauth_query"
    ax.post(f"/cases/{cid}/submit/preauth?scenario=partial")
    assert ax.get(f"/cases/{cid}").json()["status"] == "preauth_partially_approved"
    ax.post(f"/cases/{cid}/submit/claim?scenario=approve")
    assert ax.post(f"/demo/cases/{cid}/pay").json()["http_status"] == 200
    assert ax.get(f"/cases/{cid}").json()["status"] == "paid"


def test_message_detail_plain(system):
    ax, _, _ = system
    ax.post("/cases", json=sample_preauth("D-1").model_dump(mode="json"))
    ax.post("/cases/D-1/submit/preauth")
    out, inc = ax.get("/cases/D-1").json()["exchanges"]

    m = ax.get(f"/cases/D-1/messages/{out['id']}").json()
    assert m["encrypted"] is False
    assert m["plain"]["fhir"]["entry"][0]["resource"]["resourceType"] == "Claim"
    assert m["plain"]["headers"]["x-hcx-status"] == "request.initiated"
    assert m["sync_response"]["correlation_id"] == m["correlation_id"]

    d = ax.get(f"/cases/D-1/messages/{inc['id']}").json()
    assert d["interpretation"]["decision"] == "approved"
    assert d["interpretation"]["eligible"] == 70000
    assert ax.get(f"/cases/OTHER/messages/{inc['id']}").status_code == 404


def test_message_detail_encrypted(system_jwe):
    ax, _, _ = system_jwe
    ax.post("/cases", json=sample_preauth("D-2").model_dump(mode="json"))
    ax.post("/cases/D-2/submit/preauth")
    out, inc = ax.get("/cases/D-2").json()["exchanges"]
    for x in (out, inc):
        m = ax.get(f"/cases/D-2/messages/{x['id']}").json()
        assert m["encrypted"] is True
        assert len(m["wire"]["payload"].split(".")) == 5
        assert "Adarsh" not in json.dumps(m["wire"])               # unreadable on the wire
        assert m["plain"]["fhir"]["resourceType"] == "Bundle"       # readable copy kept
    assert ax.get(f"/cases/D-2/messages/{inc['id']}").json()["interpretation"]["decision"] == "approved"
