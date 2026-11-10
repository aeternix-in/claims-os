"""Session token, NHCX certificate lookup and 401 retry — all against fake transports."""
import asyncio
import datetime as dt
import json

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from aeternix_core.config import get_settings
from aeternix_core.nhcx import certs, client, jose, session


@pytest.fixture
def sandbox(monkeypatch):
    monkeypatch.setenv("AETERNIX_AUTH_MODE", "session")
    monkeypatch.setenv("AETERNIX_SANDBOX_CLIENT_ID", "cid")
    monkeypatch.setenv("AETERNIX_SANDBOX_CLIENT_SECRET", "sec")
    get_settings.cache_clear()
    session.invalidate(); certs.clear_cache()
    yield
    session.transport = certs.transport = client.transport = None
    session.invalidate(); certs.clear_cache()
    get_settings.cache_clear()


def session_server(calls, expires=1200):
    def h(req):
        calls.append(json.loads(req.content))
        assert req.headers["X-CM-ID"] == "sbx"
        return httpx.Response(200, json={"accessToken": f"tok{len(calls)}", "expiresIn": expires})
    return httpx.MockTransport(h)


def make_cert(days_valid=365, malformed=True):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "payer@hcx")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(1)
            .not_valid_before(now - dt.timedelta(days=400))
            .not_valid_after(now + dt.timedelta(days=days_valid))
            .sign(key, hashes.SHA256()))
    der = bytearray(cert.public_bytes(serialization.Encoding.DER))
    if malformed:   # mimic the sandbox: CN tagged PrintableString but containing '@' (not allowed)
        i = der.find(b"payer@hcx")
        der[i - 2] = 0x13
    import base64
    pem = "-----BEGIN CERTIFICATE-----\n" + base64.b64encode(bytes(der)).decode() + "\n-----END CERTIFICATE-----\n"
    return key, pem


def test_token_cached_then_refreshed(sandbox, monkeypatch):
    calls = []
    session.transport = session_server(calls)
    assert session.token() == "tok1" and session.token() == "tok1"
    assert len(calls) == 1 and calls[0]["grantType"] == "client_credentials"
    session.invalidate()
    assert session.token() == "tok2"


def test_token_refreshed_within_margin(sandbox):
    calls = []
    session.transport = session_server(calls, expires=60)   # less than the 120 s margin
    session.token(); session.token()
    assert len(calls) == 2


def test_missing_credentials(sandbox, monkeypatch):
    monkeypatch.delenv("AETERNIX_SANDBOX_CLIENT_SECRET")
    get_settings.cache_clear()
    with pytest.raises(session.SessionError):
        session.token()


def test_cert_lookup_lenient_cached_and_encrypts(sandbox):
    key, pem = make_cert()
    with pytest.raises(Exception):            # strict parser refuses it, like the real sandbox cert
        x509.load_pem_x509_certificate(pem.encode())
    session.transport = session_server([])
    hits = []

    def h(req):
        hits.append(req)
        assert req.headers["Accept"] == "application/json" and req.headers["bearer_auth"] == "Bearer tok1"
        return httpx.Response(200, json={"encryption_cert": pem, "participant_id": "payer@hcx"})
    certs.transport = httpx.MockTransport(h)
    a = certs.public_key_pem("payer@hcx"); b = certs.public_key_pem("payer@hcx")
    assert a == b and len(hits) == 1
    expected = key.public_key().public_bytes(serialization.Encoding.PEM,
                                             serialization.PublicFormat.SubjectPublicKeyInfo)
    assert a == expected


def test_expired_cert_refused(sandbox):
    _, pem = make_cert(days_valid=-1)
    session.transport = session_server([])
    certs.transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"encryption_cert": pem}))
    with pytest.raises(certs.CertLookupError, match="expired"):
        certs.public_key_pem("payer@hcx")


def test_cert_401_refreshes_token(sandbox):
    _, pem = make_cert()
    calls = []
    session.transport = session_server(calls)
    seen = []

    def h(req):
        seen.append(req.headers["bearer_auth"])
        if len(seen) == 1:
            return httpx.Response(401)
        return httpx.Response(200, json={"encryption_cert": pem})
    certs.transport = httpx.MockTransport(h)
    certs.public_key_pem("payer@hcx")
    assert seen == ["Bearer tok1", "Bearer tok2"]


def test_client_401_retry_and_headers(sandbox):
    session.transport = session_server([])
    seen = []

    async def h(req):
        seen.append((req.headers["bearer_auth"], req.headers["Accept"]))
        return httpx.Response(401 if len(seen) == 1 else 202, json={})
    client.transport = httpx.MockTransport(h)
    r = asyncio.run(client.post("/x", {}))
    assert r.status_code == 202
    assert seen == [("Bearer tok1", "application/json"), ("Bearer tok2", "application/json")]


def test_jose_uses_nhcx_key(sandbox, monkeypatch):
    monkeypatch.setenv("AETERNIX_KEY_SOURCE", "nhcx")
    get_settings.cache_clear()
    key, pem = make_cert()
    session.transport = session_server([])
    certs.transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"encryption_cert": pem}))
    jwk = jose._key("payer@hcx", "public")
    assert jwk.export_to_pem() == key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
