"""Step 8 end-to-end (leakage, scorecards, owner pack, CSV, claim export) against a local Postgres.
Usage, from engine/:  python tests/e2e_reports.py "postgresql://postgres@localhost/ax" ./.storage"""
import os, sys, time, jwt, json
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
acc, doc, other = H("acc@demo.in"), H("doc@demo.in"), H("other@x.in")
def ok(r, code=200):
    assert r.status_code == code, (r.status_code, r.text[:500]); return r.json()
print("doctor opens reports ->", c.get("/reports/leakage", headers=doc).status_code)
L = ok(c.get("/reports/leakage", headers=acc))
print("hospital leakage", round(L["hospital_leakage"]), "| family share", round(L["family_share"]), "| contestable", round(L["contestable"]))
for x in L["by_code"]:
    if x["amount"]: print(f"  {x['code']:<4} {x['name']:<40} ₹{x['amount']:>9,.0f}  {x['claims']} claim(s)")
print("where it went:", {k: round(v) for k, v in L["where_it_went"].items()})
P = ok(c.get("/reports/payers", headers=acc))["scorecards"]
for s in P:
    print(f"  {s['payer']:<10} claims {s['claims']:>2} | outstanding ₹{s['outstanding']:>8,.0f} | settled/claimed {s['settled_vs_claimed_pct']}% | submit→bank {s['days_submit_to_bank']} d | queries/claim {s['query_rate']} | leakage ₹{s['hospital_leakage']:,.0f}")
O = ok(c.get("/reports/owner", headers=acc))
print("owner pack keys:", list(O), "| top leakage:", [x["code"] for x in O["top_leakage"]])
print("owner pack has no patient names:", "Ravi" not in json.dumps(O, default=str))
csvt = c.get("/reports/claims.csv", headers=acc); print("claims.csv:", csvt.status_code, csvt.headers["content-type"], "|", csvt.text.splitlines()[0][:110])
print("payers.csv rows:", len(c.get("/reports/payers.csv", headers=acc).text.splitlines()))
cid = raw.execute("select id from cases where case_number='AX-2611-0001'").fetchone()["id"]
E = ok(c.get(f"/cases/{cid}/export", headers=acc))
print("claim export:", E["schema"], "| docs", len(E["documents"]), "| confirmed fields", len(E["confirmed_fields"]), "| timeline", len(E["timeline"]))
print("other hospital export ->", c.get(f"/cases/{cid}/export", headers=other).status_code, "| other hospital leakage total:", ok(c.get("/reports/leakage", headers=other))["hospital_leakage"])
f = ok(c.get("/reports/leakage?frm=2030-01-01", headers=acc)); print("future period:", f["hospital_leakage"], len(f["claims"]))
