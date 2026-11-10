"""Send one sample pre-auth to a running mock and print the callback.

    uv run python scripts/send_preauth.py [scenario]
"""
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from conftest import make_envelope  # noqa: E402

BASE = "http://127.0.0.1:8000"
scenario = sys.argv[1] if len(sys.argv) > 1 else "approve"
env = make_envelope()

r = httpx.post(f"{BASE}/hcx/v1/preauth/submit", json=env,
               headers={"bearer_auth": "Bearer local-dev", "x-mock-scenario": scenario})
print("ACK", r.status_code, r.json())
time.sleep(3)
api_call_id = env["payload"]["headers"]["x-hcx-api_call_id"]
txn = httpx.get(f"{BASE}/mock/transactions/{api_call_id}").json()
print("TXN status:", txn["status"], "| callback:", txn["callback_status"])
print("INBOX last:", httpx.get(f"{BASE}/receiver/inbox").json()[-1:] or "(empty)")
