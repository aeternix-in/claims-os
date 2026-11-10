import copy
import json
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parent / "fixtures"
AUTH = {"bearer_auth": "Bearer test-token"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("NHCX_MOCK_DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("NHCX_MOCK_CALLBACK_DELAY_SECONDS", "0")
    monkeypatch.setenv("NHCX_MOCK_CALLBACK_BASE_URL", "http://mock/receiver")
    monkeypatch.setenv("NHCX_MOCK_DEFAULT_SCENARIO", "approve")

    from nhcx_mock.config import get_settings
    get_settings.cache_clear()
    from nhcx_mock import callbacks
    from nhcx_mock.main import app
    from nhcx_mock.routes import receiver

    callbacks.transport = httpx.ASGITransport(app=app)  # callbacks hit the in-process app
    receiver.INBOX.clear()
    with TestClient(app) as c:
        yield c
    callbacks.transport = None
    get_settings.cache_clear()


def make_envelope(use: str = "preauthorization", **header_overrides) -> dict:
    bundle = copy.deepcopy(json.loads((FIXTURES / "nhcx_claimbundle_preauth_example01.json").read_text()))
    for e in bundle["entry"]:
        if e["resource"]["resourceType"] == "Claim":
            e["resource"]["use"] = use
    headers = {
        "x-hcx-api_call_id": str(uuid.uuid4()),
        "x-hcx-correlation_id": str(uuid.uuid4()),
        "x-hcx-workflow_id": str(uuid.uuid4()),
        "x-hcx-request_id": str(uuid.uuid4()),
        "x-hcx-timestamp": "2026-10-03T13:22:06+0530",
        "x-hcx-sender_code": "aeternix-provider@hcx",
        "x-hcx-recipient_code": "mock-payer@hcx",
        "x-hcx-status": "request.initiated",
        "x-hcx-ben-abha-id": "91-0000-0000-0000",
    }
    headers.update(header_overrides)
    return {"payload": {"headers": headers, "fhir": bundle}}
