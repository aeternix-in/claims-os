"""End-to-end run of step 5 against a local Postgres (see README).
Usage, from engine/:  python tests/e2e_local.py "postgresql://postgres@localhost/ax" ./.storage"""
import os, sys, time, jwt
os.environ.update(SUPABASE_URL="http://x", DATABASE_URL=sys.argv[1], SUPABASE_JWT_SECRET="test-secret", LOCAL_STORAGE_DIR=sys.argv[2])
sys.path.insert(0, ".")
import psycopg
from psycopg.rows import dict_row
from fastapi.testclient import TestClient
from app.main import app
from tests.sample_docs import pdf, png
raw = psycopg.connect(sys.argv[1], row_factory=dict_row, autocommit=True)
c = TestClient(app)
uid = {r["email"]: str(r["id"]) for r in raw.execute("select id,email from auth.users").fetchall()}
H = lambda e: {"Authorization": "Bearer " + jwt.encode({"sub": uid[e], "aud": "authenticated", "exp": time.time()+600}, "test-secret")}
desk, doc, other = H("desk@demo.in"), H("doc@demo.in"), H("other@x.in")
def ok(r, code=200):
    assert r.status_code == code, (r.status_code, r.text[:400]); return r.json()
payer = raw.execute("select id from payers where name='Insurer C'").fetchone()["id"]
cid = ok(c.post("/cases", headers=desk, json={"patient_name": "Ravi Kumar", "payer_id": str(payer), "policy_number": "IC-2026-778812",
     "sum_insured": 300000, "procedure_id": "lapchole", "provisional_dx": "Cholelithiasis"}), 201)["id"]
ok(c.post(f"/cases/{cid}/events", headers=desk, json={"event": "start_preauth"}))

r = c.post(f"/cases/{cid}/events", headers=desk, json={"event": "submit_preauth", "payload": {"estimate": 80000, "icd10": "K80.2"}})
d = r.json()["detail"]; print("submit with no documents ->", r.status_code, d["code"], "|", d["message"])

def up(name, data, mime):
    j = ok(c.post(f"/cases/{cid}/documents", headers=desk, files={"file": (name, data, mime)}), 201)
    return j
blobs = {k: pdf(k) for k in ["policy_card", "id_proof", "admission_note"]}
for k in blobs:
    j = up(f"{k}.pdf", blobs[k], "application/pdf"); print(f"  {k}.pdf -> {j['document']['doc_type']} ({j['read_method']}), fields: {[f['name'] for f in j['fields']]}")
j = up("usg_photo.png", png("usg_normal"), "image/png"); print(f"  usg_photo.png -> {j['document']['doc_type']} ({j['read_method']})")
dup = c.post(f"/cases/{cid}/documents", headers=desk, files={"file": ("again.pdf", blobs["id_proof"], "application/pdf")})
print("  same file again ->", dup.status_code, dup.json()["detail"]["code"])
print("  other hospital uploads to this case ->", c.post(f"/cases/{cid}/documents", headers=other, files={"file": ("x.pdf", pdf("bill"), "application/pdf")}).status_code)

s = ok(c.post(f"/cases/{cid}/checks", headers=desk))
print("check:", s["status"], {k: s[k] for k in ("red", "amber_open", "green")})
for x in s["results"]:
    if x["level"] != "green": print("   ", x["level"].upper(), x["rule_id"], "-", x["title"])

docs = ok(c.get(f"/cases/{cid}/documents", headers=desk))
for f in docs["fields"]:
    if f["name"] in ("patient_name", "admission_date", "diagnosis"):
        ok(c.post(f"/fields/{f['id']}/confirm", headers=desk, json={}))
amb = ok(c.post(f"/cases/{cid}/checks", headers=desk))
print("after confirming fields:", amb["status"], {k: amb[k] for k in ("red", "amber_open")})
r = c.post(f"/cases/{cid}/checks/clinical.usg_gallstones/clear", headers=desk, json={"stage": "preauth", "note": "desk says fine"})
print("desk tries to clear clinical amber ->", r.status_code, r.json()["detail"]["code"])
r = c.post(f"/cases/{cid}/events", headers=desk, json={"event": "submit_preauth", "payload": {"estimate": 80000, "icd10": "K80.2"}})
print("submit with amber open ->", r.status_code, r.json()["detail"]["code"])

j = up("usg_report.pdf", pdf("usg"), "application/pdf")
s = ok(c.post(f"/cases/{cid}/checks", headers=desk))
print("after uploading the real USG report:", s["status"], {k: s[k] for k in ("red", "amber_open", "green")})
for f in ok(c.get(f"/cases/{cid}/documents", headers=desk))["fields"]:
    if not f["confirmed"] and f["name"] in ("patient_name", "admission_date", "diagnosis"):
        ok(c.post(f"/fields/{f['id']}/confirm", headers=desk, json={}))
s = ok(c.post(f"/cases/{cid}/checks", headers=desk))
left = [x for x in s["results"] if x["level"] != "green" and not x["cleared"]]
for x in left: print("   ", x["level"].upper(), x["rule_id"], x["title"])
for x in left:
    who = doc if x["clinical"] else desk
    ok(c.post(f"/cases/{cid}/checks/{x['rule_id']}/clear", headers=who, json={"stage": "preauth", "note": "Reviewed and acceptable for this case"}))
r = ok(c.post(f"/cases/{cid}/events", headers=desk, json={"event": "submit_preauth", "payload": {"estimate": 80000, "icd10": "K80.2"}}))
print("submit ->", r["case"]["stage"])
f = ok(c.get(f"/documents/{j['document']['id']}/file", headers=desk)) if False else c.get(f"/documents/{j['document']['id']}/file", headers=desk)
print("view original ->", f.status_code, f.headers["content-type"], len(f.content), "bytes")
runs = raw.execute("select trigger, status from check_runs where case_id=%s order by id", (cid,)).fetchall()
print("check runs recorded:", [(x["trigger"], x["status"]) for x in runs])
ev = raw.execute("select event from case_events where case_id=%s order by id", (cid,)).fetchall()
print("timeline:", [e["event"] for e in ev])
