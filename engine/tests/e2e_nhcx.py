"""Engine <-> nhcx-mock end to end on Postgres (plain or JWE). See README "NHCX".
Usage, from engine/:  python tests/e2e_nhcx.py "postgresql://postgres@localhost/ax" ./.storage plain ../dev-keys"""
"""Our engine <-> nhcx-mock, in-process, on Postgres. argv: db_url storage_dir mode(plain|jwe) keys_dir"""
import os, sys, time, io, json
DB, STORE, MODE, KEYS = sys.argv[1:5]
os.environ.update(SUPABASE_URL="http://x", DATABASE_URL=DB, SUPABASE_JWT_SECRET="test-secret", LOCAL_STORAGE_DIR=STORE,
                  NHCX_ENABLED="true", AETERNIX_ENVELOPE_MODE=MODE, NHCX_MOCK_ENVELOPE_MODE=MODE, AETERNIX_KEYS_DIR=KEYS,
                  NHCX_MOCK_KEYS_DIR=KEYS, AETERNIX_DEMO_MODE="true", AETERNIX_NHCX_BASE_URL="http://mock", AETERNIX_AUTH_MODE="static",
                  AETERNIX_KEY_SOURCE="files", NHCX_MOCK_CALLBACK_BASE_URL="http://engine", NHCX_MOCK_CALLBACK_DELAY_SECONDS="0",
                  NHCX_MOCK_DATABASE_URL=f"sqlite:///{STORE}/mock-{MODE}.db", NHCX_MOCK_DEFAULT_SCENARIO="approve")
sys.path.insert(0, ".")
import httpx, jwt, psycopg
from psycopg.rows import dict_row
from fastapi.testclient import TestClient
from nhcx_mock import callbacks as mock_cb
from nhcx_mock.main import app as mock_app
from aeternix_core.nhcx import client as core_client
from aeternix_core.config import get_settings as core_settings
from app.main import app
from tests.sample_docs import pdf, DOCS
from fpdf import FPDF

mock_cb.transport = httpx.ASGITransport(app=app)
core_client.transport = httpx.ASGITransport(app=mock_app)
raw = psycopg.connect(DB, row_factory=dict_row, autocommit=True)
uid = {r["email"]: str(r["id"]) for r in raw.execute("select id,email from auth.users").fetchall()}
H = lambda e: {"Authorization": "Bearer " + jwt.encode({"sub": uid[e], "aud": "authenticated", "exp": time.time()+900}, "test-secret")}
desk, doc, acc, owner = H("desk@demo.in"), H("doc@demo.in"), H("acc@demo.in"), H("desk@demo.in")
def ok(r, code=200):
    assert r.status_code == code, (r.status_code, r.text[:700]); return r.json()
def mkpdf(lines):
    p = FPDF(); p.add_page(); p.set_font("Helvetica", size=12)
    for l in lines: p.cell(0, 8, l, new_x="LMARGIN", new_y="NEXT")
    return bytes(p.output())
tag = f"{MODE}{int(time.time()) % 100000}"
EXTRA = {"usg": ["DEPARTMENT OF CARDIOLOGY", "CORONARY ANGIOGRAPHY REPORT", "Patient Name: Ravi Kumar", "Impression: 90% stenosis of LAD.",
                 "ECG: ST elevation, troponin raised"],
         "implant": ["IMPLANT INVOICE", "Patient Name: Ravi Kumar", "Drug-eluting stent, batch no ST-4471, sticker attached"]}

with TestClient(mock_app) as mock, TestClient(app) as c:
    # identifiers NHCX needs
    raw.execute("update hospitals set hfr_id='45675454', nhcx_participant_code='aeternix-provider@hcx' where name='Demo Hospital'")
    payer = raw.execute("select id from payers where name='Insurer C'").fetchone()["id"]
    raw.execute("update payers set nhcx_participant_code='mock-payer@hcx' where id=%s", (payer,))
    st = ok(c.get("/nhcx/status", headers=desk)); print("status:", {k: st[k] for k in ("enabled", "demo_mode", "envelope_mode", "hospital_ready")})

    def new_case(name="Ravi Kumar"):
        cid = ok(c.post("/cases", headers=desk, json={"patient_name": name, "payer_id": str(payer), "policy_number": "235037120",
            "sum_insured": 500000, "procedure_id": "ptca", "provisional_dx": "Coronary artery disease", "treating_doctor": "Dr. A. Mehta",
            "room_category": "general"}), 201)["id"]
        ok(c.put(f"/cases/{cid}/bill", headers=desk, json={"lines": [
            {"category": "procedure", "description": "Angiography", "qty": 1, "rate": 10000, "snomed_code": "77343006"},
            {"category": "procedure", "description": "Angioplasty", "qty": 1, "rate": 40000, "snomed_code": "418285008"},
            {"category": "icu", "description": "ICU", "qty": 10, "rate": 2000}]}))
        return cid

    def pass_gate(cid, stage, kinds):
        for k in kinds:
            lines = EXTRA[k] if k in EXTRA else [l.format(adm="08/11/2026", dis="10/11/2026") for l in DOCS[k]]
            data = mkpdf(lines + [f"Ref {cid[:8]}"])                # unique per case (the duplicate check is hospital-wide)
            r = c.post(f"/cases/{cid}/documents", headers=desk, files={"file": (f"{k}-{tag}.pdf", data, "application/pdf")})
            assert r.status_code in (201, 409), r.text
        for f in ok(c.get(f"/cases/{cid}/documents", headers=desk))["fields"]:
            if not f["confirmed"] and f["name"] in ("patient_name", "admission_date", "diagnosis"):
                ok(c.post(f"/fields/{f['id']}/confirm", headers=desk, json={}))
        s = ok(c.post(f"/cases/{cid}/checks?stage={stage}", headers=desk))
        for x in s["results"]:
            if x["level"] == "amber" and not x["cleared"]:
                ok(c.post(f"/cases/{cid}/checks/{x['rule_id']}/clear", headers=doc if x["clinical"] else desk, json={"stage": stage, "note": "Reviewed for the NHCX test"}))
        s = ok(c.post(f"/cases/{cid}/checks?stage={stage}", headers=desk))
        assert s["status"] == "green", [x for x in s["results"] if x["level"] != "green"]

    # ---------- 1. readiness: problems in plain words ----------
    cid = new_case()
    v = ok(c.get(f"/cases/{cid}/nhcx", headers=desk))
    print("not ready yet:", v["ready"]["preauth"]["problems"])
    ok(c.post(f"/cases/{cid}/events", headers=desk, json={"event": "start_preauth"}))
    r = c.post(f"/cases/{cid}/nhcx/send/preauth", headers=desk, json={}); print("send while incomplete ->", r.status_code, r.json()["detail"]["code"])
    bad = c.patch(f"/cases/{cid}/details", headers=desk, json={"doctor_hpr_id": "NA"}); print("doctor ID 'NA' ->", bad.status_code)
    ok(c.patch(f"/cases/{cid}/details", headers=desk, json={"patient_dob": "1981-01-12", "patient_sex": "M", "doctor_hpr_id": "21-1521-3828-3227",
        "icd10": "I25.1", "product_code": "XYZHLIP21001V012021", "member_id": "ABC123456BI007", "patient_abha": "91-0000-0000-0001"}))
    v = ok(c.get(f"/cases/{cid}/nhcx", headers=desk)); print("ready:", v["ready"]["preauth"]["ok"], "total", v["ready"]["preauth"]["total"], "| actions:", v["actions"])

    # ---------- 2. pre-auth over NHCX, insurer approves ----------
    pass_gate(cid, "preauth", ["policy_card", "id_proof", "admission_note", "usg"])
    r = ok(c.post(f"/cases/{cid}/nhcx/send/preauth", headers=desk, json={"scenario": "approve"}))
    print("pre-auth sent:", r["status"], "workflow", r["workflow_id"])
    cs = raw.execute("select stage, approved_amount, nhcx_last_decision from cases where id=%s", (cid,)).fetchone()
    print("after insurer callback:", cs["stage"], float(cs["approved_amount"]), cs["nhcx_last_decision"]["decision"], cs["nhcx_last_decision"]["preauth_ref"])

    # ---------- 3. discharge (manual), claim over NHCX, settlement, payment notice, bank credit ----------
    ok(c.post(f"/cases/{cid}/events", headers=doc, json={"event": "advise_discharge"}))
    pass_gate(cid, "discharge", ["discharge_summary", "bill"])
    ok(c.post(f"/cases/{cid}/events", headers=desk, json={"event": "request_final_auth", "payload": {"billed_amount": 70000}}))
    ok(c.post(f"/cases/{cid}/events", headers=desk, json={"event": "final_auth_approved", "payload": {"approved_amount": 70000}}))
    ok(c.post(f"/cases/{cid}/events", headers=desk, json={"event": "prepare_claim"}))
    pass_gate(cid, "claim", ["ot_notes", "implant"])
    r = ok(c.post(f"/cases/{cid}/nhcx/send/claim", headers=desk, json={"scenario": "partial"}))
    cs = raw.execute("select stage, settled_amount, payer_ref from cases where id=%s", (cid,)).fetchone()
    print("claim sent:", r["workflow_id"], "| insurer:", cs["stage"], float(cs["settled_amount"]), cs["payer_ref"])
    ok(c.post(f"/cases/{cid}/nhcx/demo-pay", headers=desk))
    cs = raw.execute("select stage, nhcx_utr, nhcx_paid_amount, case_number from cases where id=%s", (cid,)).fetchone()
    print("payment notice:", cs["stage"], "(not paid until the bank credit) UTR", cs["nhcx_utr"], "amount", float(cs["nhcx_paid_amount"]))
    ackx = raw.execute("select status, plain from nhcx_exchanges where case_id=%s and workflow='paymentnotice' and direction='outbound'", (cid,)).fetchone()
    print("payment ack Task:", ackx["status"], ackx["plain"]["fhir"]["entry"][0]["resource"]["output"][0]["valueString"] == cs["case_number"])
    exp = round(float(cs["nhcx_paid_amount"]) * 0.9)
    csv = f"Date,Narration,Ref No,Debit,Credit,Balance\n{time.strftime('%d/%m/%Y')},NEFT CR INSURER C {cs['nhcx_utr']},{cs['nhcx_utr']},,{exp:.2f},1000000.00\n"
    ok(c.post("/bank/import", headers=acc, files={"file": ("s.csv", csv.encode(), "text/csv")}))
    prop = next(cr["proposal"] for cr in ok(c.get("/bank", headers=acc))["credits"] if cr["utr"] == cs["nhcx_utr"])
    print("bank proposal:", prop["confidence"], "|", prop["why"])
    ok(c.post("/bank/confirm-proposals", headers=acc, json={"levels": ["exact"]}))
    print("after bank match:", raw.execute("select stage from cases where id=%s", (cid,)).fetchone()["stage"])

    # ---------- 4. insurer query over NHCX, answered from the query desk with an attachment ----------
    q_cid = new_case()
    ok(c.patch(f"/cases/{q_cid}/details", headers=desk, json={"patient_dob": "1981-01-12", "patient_sex": "M", "doctor_hpr_id": "21-1521-3828-3227",
        "icd10": "I25.1", "product_code": "XYZHLIP21001V012021", "member_id": "ABC123456BI007"}))
    ok(c.post(f"/cases/{q_cid}/events", headers=desk, json={"event": "start_preauth"}))
    pass_gate(q_cid, "preauth", ["policy_card", "id_proof", "admission_note", "usg"])
    ok(c.post(f"/cases/{q_cid}/nhcx/send/preauth", headers=desk, json={"scenario": "query"}))
    q = ok(c.get(f"/cases/{q_cid}/queries", headers=desk))["queries"][0]
    print("insurer query:", raw.execute("select stage from cases where id=%s", (q_cid,)).fetchone()["stage"], "|", q["source"], q["code"], "|", q["query_text"])
    q = ok(c.post(f"/queries/{q['id']}/draft", headers=desk))
    draft = "ECG and the angiography report are attached. Significant LAD stenosis with ST elevation; admission needed for angioplasty."
    usg_doc = next(d["id"] for d in ok(c.get(f"/cases/{q_cid}/documents", headers=desk))["documents"] if d["filename"].startswith("usg"))
    q = ok(c.put(f"/queries/{q['id']}", headers=doc, json={"draft": draft, "attachments": [usg_doc]}))
    if q["requires_signoff"]:
        q = ok(c.post(f"/queries/{q['id']}/signoff", headers=doc))
    r = ok(c.post(f"/queries/{q['id']}/send", headers=desk))
    reply = raw.execute("select workflow_id, status, plain from nhcx_exchanges where case_id=%s and workflow='communication' and direction='outbound'", (q_cid,)).fetchone()
    payload = next(e for e in reply["plain"]["fhir"]["entry"] if e["resource"]["resourceType"] == "Communication")["resource"]["payload"]
    print("reply over NHCX: workflow", reply["workflow_id"], reply["status"], "| attachments:", [p["contentAttachment"]["title"] for p in payload if "contentAttachment" in p])
    print("after insurer's final decision:", raw.execute("select stage from cases where id=%s", (q_cid,)).fetchone()["stage"])
    hist = [e["event"] for e in raw.execute("select event from case_events where case_id=%s order by id", (q_cid,)).fetchall() if not e["event"].startswith(("document", "field", "check", "bill", "details"))]
    print("timeline:", hist)

    # ---------- 5. gateway refuses -> failed, resend ----------
    f_cid = new_case()
    ok(c.patch(f"/cases/{f_cid}/details", headers=desk, json={"patient_dob": "1981-01-12", "patient_sex": "M", "doctor_hpr_id": "21-1521-3828-3227",
        "icd10": "I25.1", "product_code": "XYZHLIP21001V012021", "member_id": "ABC123456BI007"}))
    ok(c.post(f"/cases/{f_cid}/events", headers=desk, json={"event": "start_preauth"}))
    pass_gate(f_cid, "preauth", ["policy_card", "id_proof", "admission_note", "usg"])
    core_settings().nhcx_token = "invalid"
    r = ok(c.post(f"/cases/{f_cid}/nhcx/send/preauth", headers=desk, json={}))
    v = ok(c.get(f"/cases/{f_cid}/nhcx", headers=desk))
    print("gateway refused:", r["status"], r["error"][:40], "| stage", v["stage"], "| actions", v["actions"])
    core_settings().nhcx_token = "aeternix-dev"
    r = ok(c.post(f"/cases/{f_cid}/nhcx/send/preauth", headers=desk, json={}))
    print("resend:", r["status"], "| stage", raw.execute("select stage from cases where id=%s", (f_cid,)).fetchone()["stage"])

    # ---------- 6. protocol safety ----------
    m = ok(c.get(f"/cases/{cid}/nhcx", headers=desk))["messages"]
    print("messages on case 1:", [(x["workflow"], x["action"], x["direction"], x["workflow_id"], x["status"]) for x in m])
    detail = ok(c.get(f"/cases/{cid}/nhcx/messages/{m[1]['id']}", headers=desk))
    print("under the hood:", "encrypted" if detail["encrypted"] else "plain", "| resources", detail["resources"][:3], "| interpretation", (detail["interpretation"] or {}).get("decision"))
    dup = c.post("/hcx/v1/preauth/on_submit", json=detail["wire"], headers={"bearer_auth": "Bearer x"}); print("duplicate callback ->", dup.status_code)
    noauth = c.post("/hcx/v1/preauth/on_submit", json=detail["wire"]); print("callback without auth ->", noauth.status_code, noauth.json()["error"]["code"])
    print("other hospital sees messages ->", c.get(f"/cases/{cid}/nhcx/messages/{m[1]['id']}", headers=H("other@x.in")).status_code)
    if MODE == "jwe":
        stored = json.dumps([r["wire"] for r in raw.execute("select wire from nhcx_exchanges where case_id=%s", (cid,)).fetchall()])
        print("wire messages readable patient data:", "Ravi Kumar" in stored)
