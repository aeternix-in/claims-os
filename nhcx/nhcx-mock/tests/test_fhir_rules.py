"""Request-bundle rules derived from the official NHCX examples."""
from conftest import AUTH, make_envelope


def post(client, env):
    return client.post("/hcx/v1/preauth/submit", json=env, headers=AUTH)


def claim_of(env):
    return next(e["resource"] for e in env["payload"]["fhir"]["entry"]
                if e["resource"]["resourceType"] == "Claim")


def assert_domain_error(r, fragment):
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "ERR_INVALID_DOMAIN_PAYLOAD"
    assert fragment in r.json()["error"]["message"]


def test_official_example_is_accepted(client):
    assert post(client, make_envelope()).status_code == 202


def test_bundle_type_must_be_collection(client):
    env = make_envelope()
    env["payload"]["fhir"]["type"] = "document"
    assert_domain_error(post(client, env), "Bundle.type")


def test_missing_bundle_profile(client):
    env = make_envelope()
    del env["payload"]["fhir"]["meta"]
    assert_domain_error(post(client, env), "Bundle.meta.profile")


def test_missing_claim_profile(client):
    env = make_envelope()
    del claim_of(env)["meta"]
    assert_domain_error(post(client, env), "Claim.meta.profile")


def test_strict_profiles_can_be_disabled(client, monkeypatch):
    from nhcx_mock.config import get_settings
    monkeypatch.setattr(get_settings(), "strict_profiles", False)
    env = make_envelope()
    del env["payload"]["fhir"]["meta"]
    assert post(client, env).status_code == 202


def test_duplicate_full_url(client):
    env = make_envelope()
    entries = env["payload"]["fhir"]["entry"]
    entries[2]["fullUrl"] = entries[1]["fullUrl"]
    assert_domain_error(post(client, env), "unique")


def test_dangling_patient_reference(client):
    env = make_envelope()
    claim_of(env)["patient"]["reference"] = "urn:uuid:does-not-exist"
    assert_domain_error(post(client, env), "Claim.patient.reference")


def test_missing_coverage_entry(client):
    env = make_envelope()
    env["payload"]["fhir"]["entry"] = [e for e in env["payload"]["fhir"]["entry"]
                                       if e["resource"]["resourceType"] != "Coverage"]
    assert_domain_error(post(client, env), "coverage")


def test_total_must_match_item_nets(client):
    env = make_envelope()
    claim_of(env)["total"]["value"] = 99999
    assert_domain_error(post(client, env), "Claim.total")
