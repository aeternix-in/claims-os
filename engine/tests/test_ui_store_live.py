"""Live UI storage against a local Postgres. Skips unless TEST_DATABASE_URL is set."""
import os, time
import pytest

DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB, reason="needs TEST_DATABASE_URL")


def test_versions_and_append_only_history():
    os.environ.update(SUPABASE_URL="http://x", DATABASE_URL=DB, SUPABASE_JWT_SECRET="test-secret")
    import jwt, psycopg
    from psycopg.rows import dict_row
    from fastapi.testclient import TestClient
    from app.main import app
    raw = psycopg.connect(DB, row_factory=dict_row, autocommit=True)
    uid = str(raw.execute("select id from auth.users where email='desk@demo.in'").fetchone()["id"])
    H = {"Authorization": "Bearer " + jwt.encode({"sub": uid, "aud": "authenticated", "exp": time.time() + 600}, "test-secret")}
    c = TestClient(app)
    cid = f"T-{int(time.time() * 1000)}"
    ev = lambda n: [{"id": f"{cid}-e{i}", "at": i, "type": "x", "text": f"t{i}"} for i in range(1, n + 1)]
    r = c.put(f"/ui/claims/{cid}", headers=H, json={"doc": {"id": cid, "stage": "admitted", "events": ev(1)}, "base_version": 0})
    assert r.json()["version"] == 1
    # stale save refused
    assert c.put(f"/ui/claims/{cid}", headers=H, json={"doc": {"id": cid, "events": ev(2)}, "base_version": 0}).status_code == 409
    # appending works
    assert c.put(f"/ui/claims/{cid}", headers=H, json={"doc": {"id": cid, "events": ev(2)}, "base_version": 1}).json()["version"] == 2
    # dropping or rewriting history refused
    r = c.put(f"/ui/claims/{cid}", headers=H, json={"doc": {"id": cid, "events": ev(1)}, "base_version": 2})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "history_changed"
    bad = ev(2); bad[0]["text"] = "rewritten"
    assert c.put(f"/ui/claims/{cid}", headers=H, json={"doc": {"id": cid, "events": bad}, "base_version": 2}).status_code == 422
    log = raw.execute("select count(*) as n from ui_claim_log where claim_id=%s", (cid,)).fetchone()["n"]
    assert log == 2
    # other hospital cannot see it
    other = str(raw.execute("select id from auth.users where email='other@x.in'").fetchone()["id"])
    HO = {"Authorization": "Bearer " + jwt.encode({"sub": other, "aud": "authenticated", "exp": time.time() + 600}, "test-secret")}
    assert all(x["id"] != cid for x in c.get("/ui/state", headers=HO).json()["claims"])
    raw.execute("delete from ui_claims where id=%s", (cid,))  # leave the dev hospital's desk clean


def test_doctor_cannot_change_hospital_lists():
    os.environ.update(SUPABASE_URL="http://x", DATABASE_URL=DB, SUPABASE_JWT_SECRET="test-secret")
    import jwt, psycopg
    from psycopg.rows import dict_row
    from fastapi.testclient import TestClient
    from app.main import app
    raw = psycopg.connect(DB, row_factory=dict_row, autocommit=True)
    tok = lambda email: {"Authorization": "Bearer " + jwt.encode({"sub": str(raw.execute("select id from auth.users where email=%s", (email,)).fetchone()["id"]),
                                                                  "aud": "authenticated", "exp": time.time() + 600}, "test-secret")}
    c = TestClient(app)
    s = c.get("/ui/state", headers=tok("desk@demo.in")).json()
    if not s["state"]:
        pytest.skip("hospital has no saved UI state yet")
    doc, v = s["state"], s["version"]
    changed = {**doc, "doctors": (doc.get("doctors") or []) + [{"id": "d-x", "name": "Dr X", "dept": "Cardiology"}]}
    r = c.put("/ui/state", headers=tok("doc@demo.in"), json={"doc": changed, "base_version": v})
    assert r.status_code == 403 and "doctors" in r.json()["detail"]
    # the same document unchanged in its lists is accepted from a doctor (and bumps the version)
    r = c.put("/ui/state", headers=tok("doc@demo.in"), json={"doc": doc, "base_version": v})
    assert r.status_code == 200 and r.json()["version"] == v + 1
