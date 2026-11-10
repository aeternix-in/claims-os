"""End-to-end: internal case -> transformer -> envelope -> nhcx-mock -> callback.

Runs the mock in-process; requires ../nhcx-mock (installed as a dev dependency).
"""
import httpx
import pytest

nhcx_mock = pytest.importorskip("nhcx_mock")

from fastapi.testclient import TestClient  # noqa: E402

from aeternix_core.domain.models import ClaimType  # noqa: E402
from aeternix_core.nhcx.envelope import ENDPOINT, build_request  # noqa: E402
from aeternix_core.nhcx.fhir_claim import to_claim_bundle  # noqa: E402
from aeternix_core.samples import sample_preauth  # noqa: E402

AUTH = {"bearer_auth": "Bearer aeternix-dev"}


@pytest.fixture
def mock(tmp_path, monkeypatch):
    monkeypatch.setenv("NHCX_MOCK_DATABASE_URL", f"sqlite:///{tmp_path}/mock.db")
    monkeypatch.setenv("NHCX_MOCK_CALLBACK_DELAY_SECONDS", "0")
    monkeypatch.setenv("NHCX_MOCK_CALLBACK_BASE_URL", "http://mock/receiver")
    monkeypatch.setenv("NHCX_MOCK_DEFAULT_SCENARIO", "approve")
    monkeypatch.setenv("NHCX_MOCK_STRICT_PROFILES", "true")
    from nhcx_mock import callbacks
    from nhcx_mock.config import get_settings
    from nhcx_mock.main import app
    from nhcx_mock.routes import receiver
    get_settings.cache_clear()
    callbacks.transport = httpx.ASGITransport(app=app)
    receiver.INBOX.clear()
    with TestClient(app) as client:
        yield client, receiver.INBOX
    callbacks.transport = None
    get_settings.cache_clear()


def submit(client, case, scenario="approve"):
    body, conv = build_request(case, to_claim_bundle(case))
    r = client.post(ENDPOINT[case.claim_type.value], json=body,
                    headers={**AUTH, "x-mock-scenario": scenario})
    return r, conv


def test_preauth_accepted_and_approved(mock):
    client, inbox = mock
    r, conv = submit(client, sample_preauth())
    assert r.status_code == 202, r.text
    assert r.json()["correlation_id"] == conv.correlation_id

    assert len(inbox) == 1
    cb = inbox[0]["body"]["payload"]
    assert cb["headers"]["x-hcx-correlation_id"] == conv.correlation_id
    assert cb["headers"]["x-hcx-recipient_code"] == "aeternix-provider@hcx"
    cr = cb["fhir"]["entry"][0]["resource"]
    assert cr["outcome"] == "complete"
    eligible = {t["category"]["coding"][0]["code"]: t["amount"]["value"] for t in cr["total"]}
    assert eligible == {"submitted": 70000, "eligpercent": 70000, "benefit": 70000}


def test_claim_flow_uses_claim_endpoint(mock):
    client, inbox = mock
    case = sample_preauth(case_id="AETX-CASE-0002")
    case.claim_type = ClaimType.CLAIM
    r, _ = submit(client, case)
    assert r.status_code == 202, r.text
    assert inbox[0]["path"] == "/hcx/v1/claim/on_submit"


def test_rejection_is_received(mock):
    client, inbox = mock
    r, _ = submit(client, sample_preauth(), scenario="reject")
    assert r.status_code == 202
    assert inbox[0]["body"]["payload"]["fhir"]["entry"][0]["resource"]["outcome"] == "error"
