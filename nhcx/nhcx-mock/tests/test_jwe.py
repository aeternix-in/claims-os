"""JWE envelope mode (sandbox format: RSA-OAEP-256 / A256GCM, x-hcx-* in protected header)."""
import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest
from jwcrypto import jwe, jwk

from conftest import AUTH, make_envelope
from nhcx_mock.routes.receiver import INBOX

ROOT = Path(__file__).resolve().parents[1]
PROVIDER, PAYER = "aeternix-provider@hcx", "mock-payer@hcx"


@pytest.fixture
def keys(tmp_path):
    d = tmp_path / "keys"
    subprocess.run([sys.executable, str(ROOT / "scripts/gen_dev_keys.py"), str(d)], check=True,
                   capture_output=True)
    return d


@pytest.fixture
def jwe_client(client, keys, monkeypatch):
    from nhcx_mock.config import get_settings
    monkeypatch.setattr(get_settings(), "envelope_mode", "jwe")
    monkeypatch.setattr(get_settings(), "keys_dir", str(keys))
    return client


def key(keys, code, kind):
    return jwk.JWK.from_pem((keys / f"{code}.{kind}.pem").read_bytes())


def encrypt(keys, env, alg="RSA-OAEP-256", recipient=PAYER):
    p = env["payload"]
    prot = {"alg": alg, "enc": "A256GCM", **p["headers"]}
    t = jwe.JWE(json.dumps(p["fhir"]).encode(), recipient=key(keys, recipient, "public"),
                protected=json.dumps(prot))
    return {"payload": t.serialize(compact=True)}


def decrypt(keys, token, code=PROVIDER):
    t = jwe.JWE()
    t.deserialize(token, key=key(keys, code, "private"))
    return t.jose_header, json.loads(t.payload)


def post(c, body, **h):
    return c.post("/hcx/v1/preauth/submit", json=body, headers={**AUTH, **h})


def test_encrypted_preauth_round_trip(jwe_client, keys):
    env = make_envelope()
    r = post(jwe_client, encrypt(keys, env))
    assert r.status_code == 202, r.text

    cb = INBOX[0]["body"]
    assert cb["type"] == "JWEPayload" and isinstance(cb["payload"], str)
    assert len(cb["payload"].split(".")) == 5
    header, fhir = decrypt(keys, cb["payload"])
    assert header["alg"] == "RSA-OAEP-256" and header["enc"] == "A256GCM"
    assert header["x-hcx-status"] == "response.complete"
    assert header["x-hcx-correlation_id"] == env["payload"]["headers"]["x-hcx-correlation_id"]
    assert fhir["entry"][0]["resource"]["resourceType"] == "ClaimResponse"


def test_callback_header_readable_but_payload_secret(jwe_client, keys):
    post(jwe_client, encrypt(keys, make_envelope()))
    token = INBOX[0]["body"]["payload"]
    h = json.loads(base64.urlsafe_b64decode(token.split(".")[0] + "=="))
    assert h["x-hcx-recipient_code"] == PROVIDER          # routable without a key
    assert "ClaimResponse" not in token                    # FHIR is not visible
    with pytest.raises(Exception):
        decrypt(keys, token, code=PAYER)                   # wrong private key can't read it


def test_plain_payload_rejected_in_jwe_mode(jwe_client):
    r = post(jwe_client, make_envelope())
    assert r.json()["error"]["code"] == "ERR_INVALID_ENCRYPTION"


def test_wrong_algorithm_rejected(jwe_client, keys):
    r = post(jwe_client, encrypt(keys, make_envelope(), alg="RSA-OAEP"))
    assert r.json()["error"]["code"] == "ERR_INVALID_ENCRYPTION"
    assert "RSA-OAEP-256" in r.json()["error"]["message"]


def test_encrypted_to_wrong_key_rejected(jwe_client, keys):
    r = post(jwe_client, encrypt(keys, make_envelope(), recipient=PROVIDER))
    assert r.status_code == 400
    assert "decrypt" in r.json()["error"]["message"]


def test_tampered_token_rejected(jwe_client, keys):
    token = encrypt(keys, make_envelope())["payload"]
    parts = token.split(".")
    parts[3] = parts[3][:-4] + ("AAAA" if not parts[3].endswith("AAAA") else "BBBB")
    r = post(jwe_client, {"payload": ".".join(parts)})
    assert r.json()["error"]["code"] == "ERR_INVALID_ENCRYPTION"


def test_not_a_jwe(jwe_client):
    r = post(jwe_client, {"payload": "not.a.jwe"})
    assert r.json()["error"]["code"] == "ERR_INVALID_ENCRYPTION"


def test_unknown_recipient_key(jwe_client, keys):
    env = make_envelope(**{"x-hcx-recipient_code": "other-payer@hcx"})
    body = encrypt(keys, env)   # encrypted to mock-payer's key but addressed to a participant with no key
    r = post(jwe_client, body)
    assert r.json()["error"]["code"] == "ERR_INVALID_ENCRYPTION"


def test_header_rules_still_apply_in_jwe(jwe_client, keys):
    env = make_envelope()
    del env["payload"]["headers"]["x-hcx-correlation_id"]
    r = post(jwe_client, encrypt(keys, env))
    assert r.json()["error"]["code"] == "ERR_MANDATORY_HEADER_MISSING"
