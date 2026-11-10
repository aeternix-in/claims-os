"""Live demo: two real servers talking.

Terminal 1 (mock, sending callbacks to Aeternix on :8001):
    cd ~/Aeternix/nhcx-mock
    NHCX_MOCK_CALLBACK_BASE_URL=http://127.0.0.1:8001 uv run uvicorn nhcx_mock.main:app --app-dir src --port 8000
Terminal 2 (Aeternix):
    cd ~/Aeternix/aeternix-core
    uv run uvicorn aeternix_core.api.app:app --app-dir src --port 8001
Terminal 3:
    cd ~/Aeternix/aeternix-core && uv run python scripts/demo_flow.py
"""
import sys
import time
import uuid

import httpx

AETERNIX = "http://127.0.0.1:8001"
MOCK = "http://127.0.0.1:8000"

from aeternix_core.samples import sample_preauth  # noqa: E402


def wait_for(case_id: str, expected: set[str], timeout: float = 15) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        case = httpx.get(f"{AETERNIX}/cases/{case_id}").json()
        if case["status"] in expected:
            return case
        time.sleep(0.5)
    print(f"  timed out; case is still '{case['status']}'")
    sys.exit(1)


def step(msg: str):
    print(f"\n== {msg}")


case = sample_preauth(case_id=f"DEMO-{uuid.uuid4().hex[:6].upper()}")
cid = case.case_id

step(f"create case {cid}")
r = httpx.post(f"{AETERNIX}/cases", json=case.model_dump(mode="json"))
print(" ", r.status_code, r.json())

step("submit pre-auth")
r = httpx.post(f"{AETERNIX}/cases/{cid}/submit/preauth", timeout=30)
print("  exchange:", r.json()["exchange"]["status"], "| case:", r.json()["case"])
c = wait_for(cid, {"preauth_approved", "preauth_partially_approved", "preauth_rejected", "preauth_query"})
d = c["last_decision"]
print(f"  insurer says: {c['status']} | ref {d['preauth_ref']} | eligible {d['eligible']} of {d['submitted']}")
if c["status"] not in ("preauth_approved", "preauth_partially_approved"):
    sys.exit(0)

step("submit claim (after treatment)")
httpx.post(f"{AETERNIX}/cases/{cid}/submit/claim", timeout=30)
c = wait_for(cid, {"claim_approved", "claim_partially_approved", "claim_rejected", "claim_query"})
print("  insurer says:", c["status"])

step("insurer pays (trigger mock payment notice)")
corr = next(x["correlation_id"] for x in c["exchanges"] if x["workflow"] == "claim" and x["direction"] == "outbound")
httpx.post(f"{MOCK}/mock/paymentnotice/{corr}")
c = wait_for(cid, {"paid"})

step("final case history")
for h in c["history"]:
    print(f"  {h['from'] or '-':28} -> {h['to']:28} {h['note'] or ''}")
print(f"\n  {len(c['exchanges'])} messages exchanged with NHCX")
