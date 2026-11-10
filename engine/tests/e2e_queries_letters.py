"""Step 6 end-to-end (query desk + letters) against a local Postgres. Run after e2e_local.py.
Usage, from engine/:  python tests/e2e_queries_letters.py "postgresql://postgres@localhost/ax" ./.storage"""
import os, sys, time, jwt
os.environ.update(SUPABASE_URL="http://x", DATABASE_URL=sys.argv[1], SUPABASE_JWT_SECRET="test-secret", LOCAL_STORAGE_DIR=sys.argv[2])
sys.path.insert(0, ".")
import psycopg
from psycopg.rows import dict_row
from fastapi.testclient import TestClient
from app.main import app
raw = psycopg.connect(sys.argv[1], row_factory=dict_row, autocommit=True)
c = TestClient(app)
uid = {r["email"]: str(r["id"]) for r in raw.execute("select id,email from auth.users").fetchall()}
H = lambda e: {"Authorization": "Bearer " + jwt.encode({"sub": uid[e], "aud": "authenticated", "exp": time.time()+600}, "test-secret")}
desk, doc, acc = H("desk@demo.in"), H("doc@demo.in"), H("acc@demo.in")
def ok(r, code=200):
    assert r.status_code == code, (r.status_code, r.text[:500]); return r.json()

cid = raw.execute("select id from cases where stage='preauth_pending' limit 1").fetchone()["id"]
print("case at:", raw.execute("select case_number from cases where id=%s", (cid,)).fetchone()["case_number"], "preauth_pending")
ok(c.post(f"/cases/{cid}/events", headers=desk, json={"event": "preauth_query", "payload": {"query_text": "Please justify the need for admission; laparoscopic surgery can be planned as day care"}}))
q = ok(c.get(f"/cases/{cid}/queries", headers=desk))["queries"][0]
print("query opened:", q["code"], q["code_name"], "| needs doctor:", q["requires_signoff"])
r = c.post(f"/cases/{cid}/events", headers=desk, json={"event": "answer_preauth_query", "payload": {"reply_text": "ok"}})
print("answer via raw event ->", r.status_code, r.json()["detail"]["code"])

q = ok(c.post(f"/queries/{q['id']}/draft", headers=desk))
print("---- draft ----"); print(q["draft"]); print("---------------")
print("placeholders:", len(q["placeholders"]), "| can send:", q["can_send"])
r = c.post(f"/queries/{q['id']}/send", headers=desk); print("send now ->", r.status_code, r.json()["detail"]["code"])
r = c.post(f"/queries/{q['id']}/signoff", headers=desk); print("desk signs ->", r.status_code)
r = c.post(f"/queries/{q['id']}/signoff", headers=doc); print("doctor signs with brackets left ->", r.status_code, r.json()["detail"]["code"])
filled = q["draft"].replace(q["placeholders"][0], "Recurrent biliary colic with two episodes this month and multiple calculi on USG; "
          "surgery under general anaesthesia needs overnight observation for post-operative pain and vomiting.")
q = ok(c.put(f"/queries/{q['id']}", headers=doc, json={"draft": filled}))
q = ok(c.post(f"/queries/{q['id']}/signoff", headers=doc)); print("doctor signs ->", q["status"], "by", q["signed_name"])
q2 = ok(c.put(f"/queries/{q['id']}", headers=desk, json={"draft": filled + "\nPS"})); print("desk edits after signing -> status", q2["status"], "(signature cleared)")
ok(c.put(f"/queries/{q['id']}", headers=doc, json={"draft": filled})); ok(c.post(f"/queries/{q['id']}/signoff", headers=doc))
res = ok(c.post(f"/queries/{q['id']}/send", headers=desk)); print("send ->", res["case"]["stage"])
print("clocks:", [(k["kind"], k["stopped_at"] is not None) for k in raw.execute("select kind, stopped_at from clocks where case_id=%s order by started_at", (cid,)).fetchall()])

# ---- letters: set up cases in later stages directly ----
h = raw.execute("select hospital_id, patient_id, payer_id, policy_id from cases where id=%s", (cid,)).fetchone()
def mk(num, stage, **kw):
    cols = dict(hospital_id=h["hospital_id"], case_number=num, patient_id=h["patient_id"], payer_id=h["payer_id"], policy_id=h["policy_id"], stage=stage, **kw)
    return raw.execute(f"insert into cases ({','.join(cols)}) values ({','.join(['%s']*len(cols))}) returning id", list(cols.values())).fetchone()["id"]
a = mk(f"AX-T{int(time.time())%100000}-1", "submitted", approved_amount=56000, discharged_at="2026-09-20")
raw.execute("insert into case_events (hospital_id, case_id, event, to_stage, created_at) values (%s,%s,'submit_claim','submitted', now() - interval '50 days')", (h["hospital_id"], a))
b = mk(f"AX-T{int(time.time())%100000}-2", "settled", approved_amount=60000, settled_amount=50000, discharged_at="2026-10-20")
for l in [("room", "Twin", 2, 4200), ("ot", "OT", 1, 22000), ("surgeon", "Surgeon", 1, 32000)]:
    raw.execute("insert into bill_lines (hospital_id, case_id, category, description, qty, rate) values (%s,%s,%s,%s,%s,%s)", (h["hospital_id"], b, *l))
d_ = mk(f"AX-T{int(time.time())%100000}-3", "rejected", approved_amount=40000)
raw.execute("insert into case_events (hospital_id, case_id, event, to_stage, payload) values (%s,%s,'claim_rejected','rejected', '{\"reason\":\"Pre-existing disease within waiting period\"}')", (h["hospital_id"], d_))
e = mk(f"AX-T{int(time.time())%100000}-4", "discharged", approved_amount=30000, discharged_at="2026-10-01")

s = ok(c.post("/letters/sweep", headers=acc)); print("sweep drafted:", sorted((x["kind"]) for x in s["drafted"]))
s2 = ok(c.post("/letters/sweep", headers=acc)); print("second sweep drafted:", len(s2["drafted"]), "(no duplicates)")
L = {l["kind"]: l for l in ok(c.get("/letters", headers=acc))["letters"]}
print("---- dispute letter ----"); print(L["dispute"]["subject"]); print(L["dispute"]["body"]); print("------------------------")
r = c.post(f"/letters/{L['dispute']['id']}/approve", headers=acc); print("approve dispute with brackets ->", r.status_code, r.json()["detail"]["code"])
r = c.post(f"/letters/{L['reminder']['id']}/sent", headers=acc); print("mark reminder sent before approval ->", r.status_code)
ok(c.post(f"/letters/{L['reminder']['id']}/approve", headers=acc)); x = ok(c.post(f"/letters/{L['reminder']['id']}/sent", headers=acc)); print("reminder ->", x["status"])
p = c.get(f"/letters/{L['reminder']['id']}/pdf", headers=acc); print("PDF ->", p.status_code, p.headers["content-type"], len(p.content), "bytes")
open(os.path.join(sys.argv[2], "reminder.pdf"), "wb").write(p.content)
s3 = ok(c.post("/letters/sweep", headers=acc)); print("sweep again (50 days with payer, reminder sent):", [x["kind"] for x in s3["drafted"]])
