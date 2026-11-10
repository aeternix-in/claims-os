"""Step 7 end-to-end (bank import + matching) against a local Postgres. Run after the other e2e scripts.
Usage, from engine/:  python tests/e2e_bank.py "postgresql://postgres@localhost/ax" ./.storage"""
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
desk, acc, other = H("desk@demo.in"), H("acc@demo.in"), H("other@x.in")
def ok(r, code=200):
    assert r.status_code == code, (r.status_code, r.text[:500]); return r.json()

# clean slate for bank tables; close earlier test claims so they don't join the pool
raw.execute("update cases set stage='closed' where stage='settled'")
h = raw.execute("select hospital_id, patient_id, policy_id from cases limit 1").fetchone()
payer = {r["name"]: r["id"] for r in raw.execute("select id, name from payers").fetchall()}
tag = int(time.time()) % 100000
def settled(n, payer_name, amount, ref):
    cid = raw.execute("""insert into cases (hospital_id, case_number, patient_id, policy_id, payer_id, stage, approved_amount, settled_amount, payer_ref, discharged_at)
                         values (%s,%s,%s,%s,%s,'settled',%s,%s,%s, now() - interval '20 days') returning id""",
                      (h["hospital_id"], f"AX-B{tag}-{n}", h["patient_id"], h["policy_id"], payer[payer_name], amount, amount, ref)).fetchone()["id"]
    return str(cid)
ids = [settled(1, "Insurer C", 86500, f"ICL/CLM/{tag}1"), settled(2, "Insurer A", 50000, None), settled(3, "Insurer B", 62000, f"IB-{tag}-3"),
       settled(4, "Insurer C", 40000, None), settled(5, "CGHS", 48000, None), settled(6, "Insurer C", 35000, None), settled(7, "Insurer C", 27000, None)]

print("desk opens bank ->", c.get("/bank", headers=desk).status_code, "(GET allowed)", "| desk imports ->", c.post("/bank/sample-statement", headers=desk).status_code)
s = ok(c.post("/bank/sample-statement", headers=acc))
print("sample statement:", {k: s[k] for k in ("rows", "credits_new", "duplicates", "skipped")})
print(s["csv"])
again = ok(c.post("/bank/import", headers=acc, files={"file": ("stmt.csv", s["csv"].encode(), "text/csv")}))
print("same statement re-imported:", {k: again[k] for k in ("credits_new", "duplicates")})

b = ok(c.get("/bank", headers=acc))
print("stats:", b["stats"])
for cr in b["credits"]:
    p = cr["proposal"]
    print(f"  {cr['amount']:>10} {cr['narration'][:48]:<48} -> " + (f"{p['confidence']:<6} {[l['case_number'] for l in p['lines']]} | {p['why']}" if p else "no proposal"))

r = ok(c.post("/bank/confirm-proposals", headers=acc, json={"levels": ["exact"]}))
print("Match certain (exact only): confirmed", r["confirmed"], "| shortfalls:", [(x["case_number"], x["shortfall"]) for x in r["shortfalls"]])
r = ok(c.post("/bank/confirm-proposals", headers=acc, json={"levels": ["likely", "split"]}))
print("then likely + split: confirmed", r["confirmed"])

b = ok(c.get("/bank", headers=acc))
left = [cr for cr in b["credits"] if cr["status"] == "unmatched"]
print("left unmatched:", [(cr["amount"], cr["narration"][:30]) for cr in left], "| claims still waiting:", [x["case_number"] for x in b["open_cases"]])
dep = next(cr for cr in left if "WALK-IN" in cr["narration"])
bad = c.post("/bank/confirm", headers=acc, json={"credit_id": dep["id"], "lines": [{"case_id": b["open_cases"][0]["id"], "amount": 1000}]})
print("manual match with wrong amount ->", bad.status_code, bad.json()["detail"]["code"])
ok(c.post(f"/bank/credits/{dep['id']}/ignore", headers=acc, json={"note": "Patient deposit, not a claim payment"}))
print("walk-in deposit ignored with a note")
x = c.post("/bank/confirm", headers=other, json={"credit_id": dep["id"], "lines": [{"case_id": ids[0], "amount": 1}]})
print("other hospital touches this credit ->", x.status_code)

st = raw.execute("select case_number, stage, received_amount from cases where id = any(%s::uuid[]) order by case_number", (ids,)).fetchall()
print("claims now:", [(r["case_number"].split('-')[-1], r["stage"], float(r["received_amount"] or 0)) for r in st])
sw = ok(c.post("/letters/sweep", headers=acc))
print("sweep:", [(d["kind"], d["subject"]) for d in sw["drafted"]])
ev = raw.execute("select event, note from case_events where case_id=%s order by id desc limit 1", (ids[0],)).fetchone()
print("timeline on claim 1:", ev["event"], "|", ev["note"])
