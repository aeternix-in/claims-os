import pytest

pytest.importorskip("nhcx_mock")

from aeternix_core.samples import sample_preauth  # noqa: E402


def test_ui_served(system):
    ax, _, _ = system
    r = ax.get("/ui")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "New case" in r.text
    assert ax.get("/", follow_redirects=False).status_code in (302, 307)


def test_sample_endpoint_is_a_valid_new_case(system):
    ax, _, _ = system
    sample = ax.get("/ui/sample").json()
    assert sample["case_id"].startswith("AETX-")
    assert ax.post("/cases", json=sample).status_code == 201


def test_case_list_and_actions(system):
    ax, _, mock_cfg = system
    assert ax.get("/cases").json() == []
    ax.post("/cases", json=sample_preauth("UI-1").model_dump(mode="json"))
    [row] = ax.get("/cases").json()
    assert row["patient"] == "Adarsh Agrawal" and row["total"] == 70000 and row["status"] == "draft"

    assert ax.get("/cases/UI-1").json()["actions"] == ["preauth", "claim"]
    ax.post("/cases/UI-1/submit/preauth")
    assert ax.get("/cases/UI-1").json()["actions"] == ["claim"]          # approved -> claim next
    ax.post("/cases/UI-1/submit/claim")
    assert ax.get("/cases/UI-1").json()["actions"] == []                 # waiting for payment

    mock_cfg.default_scenario = "query"
    ax.post("/cases", json=sample_preauth("UI-2").model_dump(mode="json"))
    ax.post("/cases/UI-2/submit/preauth")
    assert ax.get("/cases/UI-2").json()["actions"] == ["preauth"]        # resend after query
