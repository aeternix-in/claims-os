"""Send the sample pre-auth to a running nhcx-mock and show the result.

    uv run python scripts/submit_sample.py [scenario]      # mock must be running on :8000
"""
import json
import sys
import time

import httpx

from aeternix_core.nhcx.envelope import ENDPOINT, build_request
from aeternix_core.nhcx.fhir_claim import to_claim_bundle
from aeternix_core.samples import sample_preauth

BASE = "http://127.0.0.1:8000"
scenario = sys.argv[1] if len(sys.argv) > 1 else "approve"

case = sample_preauth()
body, conv = build_request(case, to_claim_bundle(case))
r = httpx.post(BASE + ENDPOINT[case.claim_type.value], json=body,
               headers={"bearer_auth": "Bearer aeternix-dev", "x-mock-scenario": scenario})
print("ACK", r.status_code, json.dumps(r.json(), indent=2))
if r.status_code != 202:
    sys.exit(1)

time.sleep(3)
txns = httpx.get(f"{BASE}/mock/transactions", params={"correlation_id": conv.correlation_id}).json()
print("STATUS:", txns[0]["status"], "| callback:", txns[0]["callback_status"])
cr = txns[0]["response_fhir"]["entry"][0]["resource"] if txns[0]["response_fhir"] else None
if cr:
    print("OUTCOME:", cr["outcome"], "-", cr["disposition"])
    for t in cr.get("total", []):
        print(f"  {t['category']['coding'][0]['display']}: {t['amount']['value']} {t['amount']['currency']}")
