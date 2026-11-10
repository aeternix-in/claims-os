"""M1 (ABHA) flows against a fake ABDM that decrypts what we send, like the real one."""
import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient

from aeternix_core.abha import client as abha_client
from aeternix_core.abha import crypto, flows, share, validate
from aeternix_core.config import get_settings
from aeternix_core.nhcx import session

AADHAAR = "999941057058"      # passes Verhoeff (a commonly used test-format number)
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUB_B64 = base64.b64encode(KEY.public_key().public_bytes(
    serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
PROFILE = {"firstName": "Meera", "middleName": "", "lastName": "Iyer", "dob": "01-02-1990", "gender": "F",
           "photo": base64.b64encode(b"jpg").decode(), "phrAddress": ["91123456789012@sbx"],
           "address": "1 MG Road", "districtName": "PUNE", "stateName": "MAHARASHTRA", "pinCode": "411001",
           "ABHANumber": "91-1234-5678-9012", "abhaStatus": "ACTIVE"}


def dec(b64: str) -> str:
    return KEY.decrypt(base64.b64decode(b64), padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA1()), algorithm=hashes.SHA1(), label=None)).decode()


class FakeAbdm:
    def __init__(self, aadhaar_mobile="9876543210", accounts=None):
        self.calls, self.aadhaar_mobile = [], aadhaar_mobile
        self.accounts = accounts if accounts is not None else [
            {"ABHANumber": "91-1234-5678-9012", "preferredAbhaAddress": "meera@sbx", "name": "Meera Iyer",
             "status": "ACTIVE"}]
        self.on_share = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path, body = req.url.path, (json.loads(req.content) if req.content else {})
        self.calls.append((req.method, path, body, dict(req.headers)))
        if path.endswith("/gateway/v3/sessions"):
            return httpx.Response(200, json={"accessToken": "gw-token", "expiresIn": 1200})
        if path.endswith("/patient-share/v3/on-share"):
            self.on_share.append((body, dict(req.headers)))
            return httpx.Response(202)
        assert req.headers["Authorization"] == "Bearer gw-token"
        assert req.headers["REQUEST-ID"] and req.headers["TIMESTAMP"].endswith("Z")
        p = path.split("/abha/api", 1)[-1]
        if p == "/v3/profile/public/certificate":
            return httpx.Response(200, json={"publicKey": PUB_B64,
                                             "encryptionAlgorithm": "RSA/ECB/OAEPWithSHA-1AndMGF1Padding"})
        if p == "/v3/enrollment/request/otp":
            value = dec(body["loginId"])
            if body["loginHint"] == "aadhaar":
                assert value == AADHAAR and body["otpSystem"] == "aadhaar"
                return httpx.Response(200, json={"txnId": f"t-enrol-{len(self.calls)}",
                                                 "message": "OTP sent to Aadhaar registered mobile number ending with ******3210"})
            assert body["loginHint"] == "mobile" and body["scope"] == ["abha-enrol", "mobile-verify"]
            return httpx.Response(200, json={"txnId": "t-mob", "message": f"OTP sent to mobile ending with ******{value[-4:]}"})
        if p == "/v3/enrollment/enrol/byAadhaar":
            otp = body["authData"]["otp"]
            if dec(otp["otpValue"]) != "123456":
                return httpx.Response(400, json={"code": "ABDM-1204", "message": "Invalid OTP value"})
            assert body["consent"] == {"code": "abha-enrollment", "version": "1.4"}
            prof = dict(PROFILE, mobile="******3210" if otp["mobile"] == self.aadhaar_mobile else None)
            return httpx.Response(200, json={"message": "Account created successfully", "txnId": "t-acc",
                                             "tokens": {"token": "x-token-1", "expiresIn": 1800},
                                             "ABHAProfile": prof, "isNew": True})
        if p == "/v3/enrollment/auth/byAbdm":
            ok = dec(body["authData"]["otp"]["otpValue"]) == "654321"
            return httpx.Response(200, json={"txnId": "t-acc", "authResult": "success" if ok else "failed",
                                             "message": "OTP verified successfully" if ok else "Invalid OTP"})
        if p == "/v3/enrollment/enrol/suggestion":
            assert req.headers["Transaction_Id"] == "t-acc"
            return httpx.Response(200, json={"txnId": "t-acc", "abhaAddressList": ["meera.iyer", "meera1990"]})
        if p == "/v3/enrollment/enrol/abha-address":
            assert body == {"txnId": "t-acc", "abhaAddress": "meera.iyer", "preferred": 1}
            return httpx.Response(200, json={"txnId": "t-acc", "healthIdNumber": "91-1234-5678-9012",
                                             "preferredAbhaAddress": "meera.iyer@sbx"})
        if p == "/v3/profile/login/request/otp":
            dec(body["loginId"])
            return httpx.Response(200, json={"txnId": "t-login", "message": "OTP sent to mobile ending with ******3210"})
        if p == "/v3/profile/login/verify":
            if dec(body["authData"]["otp"]["otpValue"]) != "111111":
                return httpx.Response(400, json={"code": "ABDM-1100", "message": "Please enter a valid OTP. Entered OTP is either expired or incorrect."})
            return httpx.Response(200, json={"txnId": "t-login", "authResult": "success", "message": "OTP verified successfully",
                                             "token": "tok-login", "expiresIn": 300, "accounts": self.accounts})
        if p == "/v3/profile/login/verify/user":
            assert req.headers["T-token"] == "Bearer tok-login"
            return httpx.Response(200, json={"token": f"x-{body['ABHANumber']}", "expiresIn": 1800})
        if p == "/v3/phr/web/login/abha/search":
            return httpx.Response(200, json={"healthIdNumber": "91-1234-5678-9012", "abhaAddress": body["abhaAddress"],
                                             "authMethods": ["MOBILE_OTP"], "fullName": "Meera Iyer"})
        if p == "/v3/phr/web/login/abha/request/otp":
            assert dec(body["loginId"]) == "meera@sbx"
            return httpx.Response(200, json={"txnId": "t-phr", "message": "OTP sent"})
        if p == "/v3/phr/web/login/abha/verify":
            return httpx.Response(200, json={"message": "OTP verified successfully", "authResult": "success",
                                             "users": [{"abhaAddress": "meera@sbx", "fullName": "Meera Iyer",
                                                        "abhaNumber": "91-1234-5678-9012"}],
                                             "tokens": {"token": "phr-tok", "expiresIn": 1800}})
        if p in ("/v3/profile/account", "/v3/phr/web/login/profile/abha-profile"):
            assert req.headers["X-token"].startswith("Bearer ")
            return httpx.Response(200, json={"ABHANumber": "91-1234-5678-9012", "preferredAbhaAddress": "meera@sbx",
                                             "name": "Meera Iyer", "gender": "F", "yearOfBirth": "1990",
                                             "monthOfBirth": "2", "dayOfBirth": "1", "mobile": "******3210"})
        if p in ("/v3/profile/account/abha-card", "/v3/phr/web/login/profile/abha/phr-card"):
            return httpx.Response(200, content=b"\x89PNGcard", headers={"content-type": "image/png"})
        return httpx.Response(404, json={"message": f"fake ABDM: no route {p}"})


@pytest.fixture
def abdm(tmp_path, monkeypatch):
    monkeypatch.setenv("AETERNIX_DATABASE_URL", f"sqlite:///{tmp_path}/ax.db")
    monkeypatch.setenv("AETERNIX_SANDBOX_CLIENT_ID", "cid")
    monkeypatch.setenv("AETERNIX_SANDBOX_CLIENT_SECRET", "sec")
    monkeypatch.setenv("AETERNIX_HIP_ID", "IN2710000123")
    get_settings.cache_clear()
    fake = FakeAbdm()
    t = httpx.MockTransport(fake)
    abha_client.transport = session.transport = share.transport = t
    session.invalidate(); crypto.forget()
    flows._pending.clear(); flows._sessions.clear()
    from aeternix_core.api.app import app
    with TestClient(app) as c:
        yield c, fake
    abha_client.transport = session.transport = share.transport = None
    session.invalidate(); crypto.forget()
    get_settings.cache_clear()


# ---- validation --------------------------------------------------------------------------------

def test_aadhaar_checks():
    assert validate.aadhaar("9999 4105 7058") == AADHAAR
    for bad, msg in (("12345", "12 digits"), ("999941057059", "not valid"), ("099941057058", "not valid")):
        with pytest.raises(validate.InvalidInput, match=msg):
            validate.aadhaar(bad)


def test_other_checks():
    assert validate.mobile("+91 98765 43210") == "9876543210"
    with pytest.raises(validate.InvalidInput, match="valid mobile"):
        validate.mobile("12345")
    assert validate.abha_number("91123456789012") == "91-1234-5678-9012"
    assert validate.abha_address("Meera.Iyer") == "meera.iyer@sbx"
    with pytest.raises(validate.InvalidInput):
        validate.abha_address("_x")
    assert validate.mask("9876543210") == "******3210"


# ---- creation ----------------------------------------------------------------------------------

def test_create_abha_same_mobile(abdm):
    c, fake = abdm
    r = c.post("/abha/enrol/otp", json={"aadhaar": AADHAAR})
    assert r.status_code == 200 and "******3210" in r.json()["message"]
    txn = r.json()["txnId"]
    # consent is mandatory
    r = c.post("/abha/enrol/verify", json={"txnId": txn, "otp": "123456", "mobile": "9876543210", "consent": False})
    assert r.status_code == 400 and "Consent" in r.json()["detail"]
    # wrong OTP -> ABDM's message shown
    r = c.post("/abha/enrol/verify", json={"txnId": txn, "otp": "000000", "mobile": "9876543210", "consent": True})
    assert r.status_code == 400 and r.json()["detail"] == "Invalid OTP value"
    r = c.post("/abha/enrol/verify", json={"txnId": txn, "otp": "123456", "mobile": "9876543210", "consent": True})
    d = r.json()
    assert r.status_code == 200 and d["isNew"] and d["mobileVerified"]
    assert d["profile"]["abhaNumber"] == "91-1234-5678-9012" and d["profile"]["name"] == "Meera Iyer"
    assert d["patient"]["existing"] is False and d["sid"]
    # suggestions + address
    s = c.get("/abha/enrol/suggestions", params={"txnId": "t-acc"}).json()
    assert s["suggestions"] == ["meera.iyer", "meera1990"]
    a = c.post("/abha/enrol/address", json={"txnId": "t-acc", "abhaAddress": "meera.iyer"}).json()
    assert a["abhaAddress"] == "meera.iyer@sbx"
    # card via the user token
    card = c.get("/abha/card", params={"sid": d["sid"], "download": True})
    assert card.status_code == 200 and card.content.startswith(b"\x89PNG")
    assert "attachment" in card.headers["content-disposition"]
    # nothing in clear text went to ABDM
    sent = json.dumps([b for _, _, b, _ in fake.calls])
    assert AADHAAR not in sent and "123456" not in sent


def test_create_abha_other_mobile_needs_mobile_otp(abdm):
    c, _ = abdm
    txn = c.post("/abha/enrol/otp", json={"aadhaar": AADHAAR}).json()["txnId"]
    d = c.post("/abha/enrol/verify", json={"txnId": txn, "otp": "123456", "mobile": "9123456780", "consent": True}).json()
    assert d["mobileVerified"] is False
    m = c.post("/abha/enrol/mobile/otp", json={"txnId": d["txnId"], "mobile": "9123456780"}).json()
    assert "6780" in m["message"]
    bad = c.post("/abha/enrol/mobile/verify", json={"txnId": m["txnId"], "otp": "000000"})
    assert bad.status_code == 400
    ok = c.post("/abha/enrol/mobile/verify", json={"txnId": m["txnId"], "otp": "654321"})
    assert ok.status_code == 200 and ok.json()["message"] == "OTP verified successfully"


def test_invalid_aadhaar_never_reaches_abdm(abdm):
    c, fake = abdm
    r = c.post("/abha/enrol/otp", json={"aadhaar": "123412341234"})
    assert r.status_code == 400 and "not valid" in r.json()["detail"]
    assert not any("enrollment" in p for _, p, _, _ in fake.calls)


def test_resend_rules(abdm, monkeypatch):
    c, _ = abdm
    t1 = c.post("/abha/enrol/otp", json={"aadhaar": AADHAAR}).json()["txnId"]
    r = c.post("/abha/enrol/otp", json={"aadhaar": AADHAAR, "previous_txn": t1})
    assert r.status_code == 400 and "wait" in r.json()["detail"]
    clock = [flows.time.time()]
    monkeypatch.setattr(flows.time, "time", lambda: clock[0])
    txn = t1
    for left in (1, 0):
        clock[0] += 61
        r = c.post("/abha/enrol/otp", json={"aadhaar": AADHAAR, "previous_txn": txn})
        assert r.status_code == 200 and r.json()["resendsLeft"] == left
        txn = r.json()["txnId"]
    clock[0] += 61
    r = c.post("/abha/enrol/otp", json={"aadhaar": AADHAAR, "previous_txn": txn})
    assert r.status_code == 400 and "only 2 times" in r.json()["detail"]


# ---- verification ------------------------------------------------------------------------------

def test_verify_by_abha_number(abdm):
    c, fake = abdm
    r = c.post("/abha/login/otp", json={"method": "abha-number-aadhaar-otp", "value": "91-1234-5678-9012"})
    assert r.status_code == 200
    body = [b for _, p, b, _ in fake.calls if p.endswith("/v3/profile/login/request/otp")][-1]
    assert body["scope"] == ["abha-login", "aadhaar-verify"] and body["otpSystem"] == "aadhaar"
    assert dec(body["loginId"]) == "91-1234-5678-9012"
    bad = c.post("/abha/login/verify", json={"txnId": "t-login", "otp": "999999"})
    assert bad.status_code == 400 and "valid OTP" in bad.json()["detail"]
    v = c.post("/abha/login/verify", json={"txnId": "t-login", "otp": "111111"}).json()
    assert v["sid"] and not v["needsSelection"]
    p = c.get("/abha/profile", params={"sid": v["sid"]}).json()
    assert p["profile"]["dob"] == "01-02-1990" and p["patient"]["patient_id"].startswith("AXP-")


def test_verify_by_mobile_multiple_accounts(abdm):
    c, fake = abdm
    fake.accounts = [{"ABHANumber": "91-1111-1111-1111", "name": "A", "status": "ACTIVE"},
                     {"ABHANumber": "91-2222-2222-2222", "name": "B", "status": "ACTIVE"}]
    c.post("/abha/login/otp", json={"method": "mobile-otp", "value": "9876543210"})
    v = c.post("/abha/login/verify", json={"txnId": "t-login", "otp": "111111"}).json()
    assert v["needsSelection"] and len(v["accounts"]) == 2
    # T-token cannot read the profile directly
    assert c.get("/abha/profile", params={"sid": v["sid"]}).status_code == 400
    s = c.post("/abha/login/select", json={"sid": v["sid"], "abhaNumber": "91-2222-2222-2222"}).json()
    c.get("/abha/profile", params={"sid": s["sid"]})
    hdrs = [h for _, p, _, h in fake.calls if p.endswith("/v3/profile/account")]
    assert hdrs[-1]["x-token"] == "Bearer x-91-2222-2222-2222"


def test_verify_mobile_without_abha_offers_creation(abdm):
    c, fake = abdm
    fake.accounts = []
    c.post("/abha/login/otp", json={"method": "aadhaar-otp", "value": AADHAAR})
    v = c.post("/abha/login/verify", json={"txnId": "t-login", "otp": "111111"}).json()
    assert v["noAbha"] and "No ABHA is registered" in v["message"]


def test_verify_by_abha_address(abdm):
    c, _ = abdm
    r = c.post("/abha/login/otp", json={"method": "abha-address-aadhaar-otp", "value": "meera"})
    assert r.status_code == 400 and "does not allow" in r.json()["detail"]
    r = c.post("/abha/login/otp", json={"method": "abha-address-mobile-otp", "value": "meera"})
    assert r.status_code == 200 and r.json()["name"] == "Meera Iyer"
    v = c.post("/abha/login/verify", json={"txnId": "t-phr", "otp": "111111"}).json()
    p = c.get("/abha/profile", params={"sid": v["sid"]}).json()
    assert p["profile"]["abhaAddress"] == "meera@sbx"
    assert c.get("/abha/card", params={"sid": v["sid"]}).status_code == 200


def test_one_abha_one_patient(abdm):
    c, _ = abdm
    ids = set()
    for _ in range(2):
        c.post("/abha/login/otp", json={"method": "abha-number-mobile-otp", "value": "91123456789012"})
        v = c.post("/abha/login/verify", json={"txnId": "t-login", "otp": "111111"}).json()
        ids.add(c.get("/abha/profile", params={"sid": v["sid"]}).json()["patient"]["patient_id"])
    assert len(ids) == 1
    assert len([p for p in c.get("/abha/patients").json() if p["abha_number"] == "91-1234-5678-9012"]) == 1


def test_expired_session(abdm):
    c, _ = abdm
    r = c.get("/abha/profile", params={"sid": "nope"})
    assert r.status_code == 401 and "expired" in r.json()["detail"]


# ---- Scan & Share ------------------------------------------------------------------------------

PUSH = {"intent": "PROFILE_SHARE",
        "metaData": {"hipId": "IN2710000123", "context": "REG-DESK-1"},
        "profile": {"patient": {"abhaNumber": "91-1234-5678-9012", "abhaAddress": "meera@sbx", "name": "Meera Iyer",
                                "gender": "F", "dayOfBirth": "1", "monthOfBirth": "2", "yearOfBirth": "1990",
                                "address": {"line": "1 MG Road", "district": "PUNE", "state": "MAHARASHTRA",
                                            "pincode": "411001"}, "phoneNumber": "9876543210"}}}


def test_scan_and_share(abdm):
    c, fake = abdm
    cfg = c.get("/abha/config").json()
    assert cfg["qr_url"] == "https://phrsbx.abdm.gov.in/share-profile?hip-id=IN2710000123&counter-id=REG-DESK-1"
    qr = c.get("/abha/qr.svg")
    assert qr.status_code == 200 and qr.text.lstrip().startswith(("<?xml", "<svg"))
    r = c.post("/api/v3/hip/patient/share", json=PUSH, headers={"REQUEST-ID": "req-1", "TIMESTAMP": "x"})
    assert r.status_code == 202
    body, hdrs = fake.on_share[-1]
    assert body["response"]["requestId"] == "req-1"
    ack = body["acknowledgement"]
    assert ack["abhaAddress"] == "meera@sbx" and ack["status"] == "SUCCESS"
    assert ack["profile"]["tokenNumber"] == "1" and ack["profile"]["context"] == "REG-DESK-1"
    assert hdrs["x-cm-id"] == "sbx" and hdrs["authorization"] == "Bearer gw-token"
    # same push again: same token, no second callback
    c.post("/api/v3/hip/patient/share", json=PUSH, headers={"REQUEST-ID": "req-1"})
    assert len(fake.on_share) == 1
    rows = c.get("/abha/shares").json()
    assert rows[0]["status"] == "acknowledged" and rows[0]["patient_id"].startswith("AXP-")
    # a new push from another patient gets the next token
    other = json.loads(json.dumps(PUSH))
    other["profile"]["patient"].update(abhaNumber="91-9999-8888-7777", abhaAddress="ravi@sbx", name="Ravi")
    c.post("/api/v3/hip/patient/share", json=other, headers={"REQUEST-ID": "req-2"})
    assert fake.on_share[-1][0]["acknowledgement"]["profile"]["tokenNumber"] == "2"


def test_share_without_request_id(abdm):
    c, _ = abdm
    assert c.post("/api/v3/hip/patient/share", json=PUSH).status_code == 400


def test_ui_page(abdm):
    c, _ = abdm
    r = c.get("/ui/abha")
    assert r.status_code == 200 and "Create ABHA" in r.text and "Scan &amp; Share" in r.text
