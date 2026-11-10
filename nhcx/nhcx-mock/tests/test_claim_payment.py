import uuid

from conftest import AUTH, make_envelope
from nhcx_mock.routes.receiver import INBOX


def test_claim_then_payment_notice_then_ack(client):
    env = make_envelope(use="claim")
    corr = env["payload"]["headers"]["x-hcx-correlation_id"]
    assert client.post("/hcx/v1/claim/submit", json=env, headers=AUTH).status_code == 202
    assert INBOX[0]["path"] == "/hcx/v1/claim/on_submit"

    # Mock payer pushes a PaymentNotice to the provider
    r = client.post(f"/mock/paymentnotice/{corr}")
    assert r.status_code == 200
    pn_cb = INBOX[1]
    assert pn_cb["path"] == "/hcx/v1/paymentnotice/request"
    assert "type" not in pn_cb["body"]
    pn_headers = pn_cb["body"]["payload"]["headers"]
    notice = pn_cb["body"]["payload"]["fhir"]["entry"][0]["resource"]
    assert notice["resourceType"] == "PaymentNotice"
    assert notice["amount"]["value"] == 70000

    # Provider acknowledges via on_request
    ack_headers = {**pn_headers,
                   "x-hcx-api_call_id": str(uuid.uuid4()),
                   "x-hcx-status": "response.complete",
                   "x-hcx-sender_code": pn_headers["x-hcx-recipient_code"],
                   "x-hcx-recipient_code": pn_headers["x-hcx-sender_code"]}
    ack_body = {"type": "JWEPayload",
                "payload": {"headers": ack_headers, "fhir": {"resourceType": "Bundle", "entry": []}}}
    r = client.post("/hcx/v1/paymentnotice/on_request", json=ack_body, headers=AUTH)
    assert r.status_code == 202

    txns = client.get("/mock/transactions", params={"correlation_id": corr}).json()
    assert {t["workflow"]: t["status"] for t in txns} == {"claim": "approved", "paymentnotice": "acknowledged"}


def test_payment_notice_requires_approved_claim(client):
    env = make_envelope(use="claim")
    client.post("/hcx/v1/claim/submit", json=env, headers={**AUTH, "x-mock-scenario": "reject"})
    r = client.post(f"/mock/paymentnotice/{env['payload']['headers']['x-hcx-correlation_id']}")
    assert r.status_code == 400
