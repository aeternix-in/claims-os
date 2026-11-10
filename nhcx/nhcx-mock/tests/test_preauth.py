from conftest import AUTH, make_envelope
from nhcx_mock.routes.receiver import INBOX

ADJ = "http://terminology.hl7.org/CodeSystem/adjudication"


def totals(cr):
    return {t["category"]["coding"][0]["code"]: t["amount"]["value"] for t in cr.get("total", [])}


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_preauth_approve_end_to_end(client):
    env = make_envelope()
    req_h = env["payload"]["headers"]

    r = client.post("/hcx/v1/preauth/submit", json=env, headers=AUTH)
    assert r.status_code == 202
    assert r.json()["api_call_id"] == req_h["x-hcx-api_call_id"]

    assert len(INBOX) == 1
    cb = INBOX[0]
    assert cb["path"] == "/hcx/v1/preauth/on_submit"
    assert cb["body"]["type"] == "JWEPayload"
    h = cb["body"]["payload"]["headers"]
    assert h["x-hcx-status"] == "response.complete"
    assert h["x-hcx-api_call_id"] != req_h["x-hcx-api_call_id"]
    for k in ("x-hcx-correlation_id", "x-hcx-workflow_id", "x-hcx-request_id"):
        assert h[k] == req_h[k]
    assert h["x-hcx-sender_code"] == req_h["x-hcx-recipient_code"]

    txn = client.get(f"/mock/transactions/{req_h['x-hcx-api_call_id']}").json()
    assert txn["status"] == "approved"
    assert txn["callback_status"] == "delivered"


def test_response_matches_official_claimresponsebundle_shape(client):
    env = make_envelope()
    client.post("/hcx/v1/preauth/submit", json=env, headers=AUTH)
    bundle = INBOX[0]["body"]["payload"]["fhir"]

    assert bundle["type"] == "collection"
    assert "https://nrces.in/ndhm/fhir/r4/StructureDefinition/ClaimResponseBundle" in bundle["meta"]["profile"]
    assert bundle["meta"]["security"][0]["code"] == "V"
    assert bundle["identifier"] == env["payload"]["fhir"]["identifier"]

    types = [e["resource"]["resourceType"] for e in bundle["entry"]]
    assert types[0] == "ClaimResponse"
    assert {"Claim", "Patient", "Organization", "Practitioner", "Coverage"} <= set(types)

    cr = bundle["entry"][0]["resource"]
    claim_full_url = next(e["fullUrl"] for e in bundle["entry"] if e["resource"]["resourceType"] == "Claim")
    assert cr["request"] == {"reference": claim_full_url}
    assert cr["outcome"] == "complete"
    assert cr["preAuthRef"].startswith("PA-")
    assert "start" in cr["preAuthPeriod"] and "end" in cr["preAuthPeriod"]
    assert cr["payeeType"]["coding"][0]["code"] == "provider"
    assert totals(cr) == {"submitted": 70000, "eligpercent": 70000, "benefit": 70000}
    assert [i["itemSequence"] for i in cr["item"]] == [1, 2, 3]
    item3 = {a["category"]["coding"][0]["code"]: a for a in cr["item"][2]["adjudication"]}
    assert item3["submitted"]["amount"]["value"] == 20000
    assert item3["eligpercent"]["reason"]["coding"][0]["code"] == "covered"


def test_preauth_reject(client):
    client.post("/hcx/v1/preauth/submit", json=make_envelope(),
                headers={**AUTH, "x-mock-scenario": "reject"})
    cr = INBOX[0]["body"]["payload"]["fhir"]["entry"][0]["resource"]
    assert cr["outcome"] == "error"
    assert totals(cr) == {"submitted": 70000}
    assert "preAuthRef" not in cr


def test_preauth_partial_and_query(client):
    client.post("/hcx/v1/preauth/submit", json=make_envelope(), headers={**AUTH, "x-mock-scenario": "partial"})
    client.post("/hcx/v1/preauth/submit", json=make_envelope(), headers={**AUTH, "x-mock-scenario": "query"})
    partial, query, comm = (cb["body"]["payload"]["fhir"]["entry"][0]["resource"] for cb in INBOX)
    assert totals(partial) == {"submitted": 70000, "eligpercent": 35000, "benefit": 35000}
    assert query["outcome"] == "partial"
    assert "preAuthRef" not in query
    # NHA guide ch.8: the query itself follows as a CommunicationRequest carrying the hospital's CLN
    assert INBOX[2]["path"].endswith("/communication/request") if "path" in INBOX[2] else True
    assert comm["resourceType"] == "CommunicationRequest" and comm["payload"][0]["contentString"]
