from conftest import AUTH, make_envelope
from nhcx_mock.routes.receiver import INBOX


def post(client, env, **headers):
    return client.post("/hcx/v1/preauth/submit", json=env, headers=headers)


def test_missing_bearer_auth(client):
    assert post(client, make_envelope()).status_code == 401


def test_standard_authorization_header_is_not_enough(client):
    r = post(client, make_envelope(), Authorization="Bearer abc")
    assert r.status_code == 401


def test_invalid_token(client):
    assert post(client, make_envelope(), bearer_auth="Bearer invalid").status_code == 401


def test_missing_protocol_header(client):
    env = make_envelope()
    del env["payload"]["headers"]["x-hcx-correlation_id"]
    r = post(client, env, **AUTH)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "ERR_MANDATORY_HEADER_MISSING"


def test_bad_participant_code(client):
    r = post(client, make_envelope(**{"x-hcx-recipient_code": "payer"}), **AUTH)
    assert r.json()["error"]["code"] == "ERR_INVALID_SENDER_OR_RECIPIENT"


def test_wrong_status(client):
    r = post(client, make_envelope(**{"x-hcx-status": "response.complete"}), **AUTH)
    assert r.json()["error"]["code"] == "ERR_INVALID_STATUS"


def test_jwe_string_rejected_in_plain_mode(client):
    r = post(client, {"payload": "eyJhbGciOi..."}, **AUTH)
    assert r.json()["error"]["code"] == "ERR_INVALID_ENCRYPTION"


def test_not_json_object(client):
    r = client.post("/hcx/v1/preauth/submit", content=b"not json",
                    headers={**AUTH, "Content-Type": "application/json"})
    assert r.status_code == 400


def test_wrong_claim_use(client):
    r = post(client, make_envelope(use="claim"), **AUTH)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "ERR_INVALID_DOMAIN_PAYLOAD"


def test_missing_claim_resource(client):
    env = make_envelope()
    env["payload"]["fhir"]["entry"] = [e for e in env["payload"]["fhir"]["entry"]
                                       if e["resource"]["resourceType"] != "Claim"]
    assert post(client, env, **AUTH).status_code == 400


def test_duplicate_api_call_id(client):
    env = make_envelope()
    assert post(client, env, **AUTH).status_code == 202
    r = post(client, env, **AUTH)
    assert r.json()["error"]["code"] == "ERR_DUPLICATE_API_CALL_ID"


def test_payer_timeout(client):
    env = make_envelope()
    post(client, env, **{**AUTH, "x-mock-scenario": "timeout"})
    assert INBOX == []
    txn = client.get(f"/mock/transactions/{env['payload']['headers']['x-hcx-api_call_id']}").json()
    assert txn["status"] == "payer_timeout"


def test_callback_failure_retries(client):
    env = make_envelope()
    post(client, env, **{**AUTH, "x-mock-scenario": "callback_fail"})
    txn = client.get(f"/mock/transactions/{env['payload']['headers']['x-hcx-api_call_id']}").json()
    assert txn["callback_status"] == "failed"
    assert txn["callback_attempts"] == 3
    assert "404" in txn["last_error"]


def test_unknown_scenario(client):
    r = post(client, make_envelope(), **{**AUTH, "x-mock-scenario": "nope"})
    assert r.json()["error"]["code"] == "ERR_MOCK_SCENARIO"
