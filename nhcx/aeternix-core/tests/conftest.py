import subprocess
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _make_system(tmp_path, monkeypatch, mode: str):
    pytest.importorskip("nhcx_mock")
    from fastapi.testclient import TestClient

    keys = tmp_path / "keys"
    if mode == "jwe":
        subprocess.run([sys.executable, str(ROOT / "scripts/gen_dev_keys.py"), str(keys)],
                       check=True, capture_output=True)
    for prefix in ("NHCX_MOCK", "AETERNIX"):
        monkeypatch.setenv(f"{prefix}_ENVELOPE_MODE", mode)
        monkeypatch.setenv(f"{prefix}_KEYS_DIR", str(keys))
    monkeypatch.setenv("NHCX_MOCK_DATABASE_URL", f"sqlite:///{tmp_path}/mock.db")
    monkeypatch.setenv("NHCX_MOCK_CALLBACK_DELAY_SECONDS", "0")
    monkeypatch.setenv("NHCX_MOCK_CALLBACK_BASE_URL", "http://aeternix")
    monkeypatch.setenv("NHCX_MOCK_DEFAULT_SCENARIO", "approve")
    monkeypatch.setenv("AETERNIX_DATABASE_URL", f"sqlite:///{tmp_path}/aeternix.db")
    monkeypatch.setenv("AETERNIX_NHCX_BASE_URL", "http://mock")
    # Tests always talk to the mock, whatever your real .env says (sandbox mode would log in to ABDM).
    monkeypatch.setenv("AETERNIX_AUTH_MODE", "static")
    monkeypatch.setenv("AETERNIX_KEY_SOURCE", "files")

    from nhcx_mock import callbacks as mock_callbacks
    from nhcx_mock.config import get_settings as mock_settings
    from nhcx_mock.main import app as mock_app

    from aeternix_core.api.app import app as ax_app
    from aeternix_core.config import get_settings as ax_settings
    from aeternix_core.nhcx import client as ax_client

    mock_settings.cache_clear()
    ax_settings.cache_clear()
    mock_callbacks.transport = httpx.ASGITransport(app=ax_app)
    ax_client.transport = httpx.ASGITransport(app=mock_app)
    try:
        with TestClient(mock_app) as mock, TestClient(ax_app) as ax:
            yield ax, mock, mock_settings()
    finally:
        mock_callbacks.transport = None
        ax_client.transport = None
        mock_settings.cache_clear()
        ax_settings.cache_clear()


@pytest.fixture
def system(tmp_path, monkeypatch):
    """aeternix-core <-> nhcx-mock wired in-process, plain envelopes."""
    yield from _make_system(tmp_path, monkeypatch, "plain")


@pytest.fixture
def system_jwe(tmp_path, monkeypatch):
    """Same, with JWE envelopes on both sides (sandbox format)."""
    yield from _make_system(tmp_path, monkeypatch, "jwe")
