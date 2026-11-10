# NHCX Local Mock

A local stand-in for the NHCX sandbox. Your app submits a pre-auth or claim, the mock validates it,
a mock payer decides, and the result comes back asynchronously on the matching `on_*` callback, like
the real exchange. Failures (bad headers, auth, duplicates, payer timeout, callback down) are
reproducible on demand.

## 1. Install (one time)

You need Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
# install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh          # macOS / Linux
# Windows (PowerShell): powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

unzip nhcx-mock.zip && cd nhcx-mock
uv sync                     # creates .venv and installs everything from pyproject.toml
cp .env.example .env        # optional; defaults work without it
```

Packages: fastapi, uvicorn[standard], pydantic, pydantic-settings, httpx, sqlalchemy, and pytest (dev).

Without uv: `python -m venv .venv && source .venv/bin/activate && pip install -e . pytest`

## 2. Run

```bash
uv run uvicorn nhcx_mock.main:app --reload --app-dir src
```

- Swagger UI: http://127.0.0.1:8000/docs
- Health: http://127.0.0.1:8000/health

In a second terminal, send a sample pre-auth and see the callback:

```bash
uv run python scripts/send_preauth.py            # approve
uv run python scripts/send_preauth.py reject     # or partial | query | timeout | callback_fail
```

## 3. Test

```bash
uv run pytest -v
```

## How to call it

`POST /hcx/v1/preauth/submit` (same path as the sandbox), header `bearer_auth: Bearer <anything>`, body:

```json
{
  "payload": {
    "headers": {
      "x-hcx-api_call_id": "<uuid>",
      "x-hcx-correlation_id": "<uuid>",
      "x-hcx-workflow_id": "<uuid>",
      "x-hcx-request_id": "<uuid>",
      "x-hcx-timestamp": "2026-10-03T13:22:06+0530",
      "x-hcx-sender_code": "aeternix-provider@hcx",
      "x-hcx-recipient_code": "mock-payer@hcx",
      "x-hcx-status": "request.initiated",
      "x-hcx-ben-abha-id": "91-0000-0000-0000"
    },
    "fhir": { "resourceType": "Bundle", "entry": ["... Claim with use=preauthorization ..."] }
  }
}
```

This is **plain mode**. The real sandbox puts a JWE string in `payload`, with these headers inside the
JWE protected header. The structure is the same, so moving to JWE later only changes that one field.

You get a `202` ack immediately. About 2 s later the mock POSTs
`{"type": "JWEPayload", "payload": {...ClaimResponse bundle...}}` to
`{CALLBACK_BASE_URL}/hcx/v1/preauth/on_submit`.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /hcx/v1/preauth/submit` | Pre-auth (Claim.use = preauthorization), callback to `/preauth/on_submit` |
| `POST /hcx/v1/claim/submit` | Claim (Claim.use = claim), callback to `/claim/on_submit` |
| `POST /mock/paymentnotice/{correlation_id}` | Payer sends a PaymentNotice for an approved claim to `/paymentnotice/request` |
| `POST /hcx/v1/paymentnotice/on_request` | Your app acknowledges the PaymentNotice |
| `GET /mock/transactions[?correlation_id=]` | Inspect persisted state |
| `GET /mock/transactions/{api_call_id}` | One transaction: status, callback attempts, payloads |
| `POST /mock/token` | Fake token |
| `GET` / `DELETE /receiver/inbox` | Callbacks received by the built-in receiver |

## Simulating outcomes and failures

Add the header `x-mock-scenario` to a submit:

| Scenario | Result |
|---|---|
| `approve` (default) | outcome `complete`, full amount, `preAuthRef` |
| `partial` | outcome `complete`, 50% of the amount |
| `reject` | outcome `error` |
| `query` | outcome `partial`, "additional information required" |
| `timeout` | no callback; status `payer_timeout` |
| `callback_fail` | callback goes to a dead URL; 3 retries, then `callback_status=failed` |

FHIR checks on every request, based on the official NHCX v6.5.0 examples:
- `Bundle.type` must be `collection`.
- Exactly one Claim, and `Claim.use` must match the endpoint.
- Every entry has a unique `fullUrl`.
- `Bundle.meta.profile` must include `.../StructureDefinition/ClaimBundle`, and `Claim.meta.profile` must include `.../StructureDefinition/Claim`. Turn this off with `NHCX_MOCK_STRICT_PROFILES=false`.
- `Claim.patient`, `insurer`, `provider` and `insurance.coverage` must point to entries inside the bundle.
- `Claim.total` must equal the sum of `item.net`.

Responses follow the official `ClaimResponseBundle-preauthorization-example-01`: a ClaimResponse first, followed by the echoed Claim, Patient, Organizations, Practitioner and Coverage. Each item gets `submitted` and `eligpercent` adjudication, with `submitted` and `eligpercent` totals. Approved pre-auths also get `preAuthRef` and `preAuthPeriod`.

Protocol error checks:
- Missing or invalid `bearer_auth` returns 401. The token `invalid` is always rejected.
- Missing protocol headers.
- Bad participant codes (must look like `xxx@hcx`).
- Wrong `x-hcx-status`.
- A JWE string sent in plain mode.
- Wrong `Claim.use`, or a missing Claim.
- Duplicate `api_call_id`.

## Connecting Aeternix

1. Have Aeternix send to `http://127.0.0.1:8000/hcx/v1/...` with the `bearer_auth` header.
2. Expose callback endpoints in Aeternix at `/hcx/v1/preauth/on_submit`, `/hcx/v1/claim/on_submit`
   and `/hcx/v1/paymentnotice/request`.
3. Set `NHCX_MOCK_CALLBACK_BASE_URL=http://<aeternix-host>:<port>` in `.env` and restart the mock.

## Layout

```
src/nhcx_mock/
  main.py          app, routers, error handlers
  config.py        settings (env prefix NHCX_MOCK_)
  envelope.py      payload unpack/pack, header rules, ack
  fhir_check.py    structural FHIR checks (not profile validation)
  workflows.py     preauth / claim / paymentnotice definitions
  payer.py         scenario -> ClaimResponse / PaymentNotice
  callbacks.py     async delivery with retries
  db.py            SQLite transactions table
  routes/          hcx.py, mock_admin.py, receiver.py, auth.py
contracts/         sandbox contract notes and assumptions
tests/             pytest suite and dummy FHIR fixture
scripts/           send_preauth.py
```

## Known limits / next steps

- FHIR checks are rule-based, not full profile validation. Next step: HL7 FHIR Validator with the ndhm.in#6.5.0 package.
- `tests/fixtures/nhcx_claimbundle_preauth_example01.json` is the official NRCeS example (narrative `text` blocks removed). Its patient details are the IG's sample data.
- No official rejection or query response example exists, so those outcomes and dispositions are mock choices.
- PaymentNotice is still a minimal R4 resource, not yet aligned with the official NHCX example.
- Use dummy data only. Never put real credentials, keys, or patient data here.

## Encryption (JWE mode)

Both projects support two envelope modes, set with `NHCX_MOCK_ENVELOPE_MODE` and `AETERNIX_ENVELOPE_MODE`. Both sides must use the same mode.

| Mode | `payload` contains | Use |
|---|---|---|
| `plain` (default) | JSON `{"headers": {...}, "fhir": {...}}` | Building and debugging, since you can read everything |
| `jwe` | Compact JWE string, `RSA-OAEP-256` / `A256GCM`, with the `x-hcx-*` headers in the protected header | Sandbox format |

In JWE mode the sender encrypts to the **recipient's public key**. The x-hcx headers stay readable, so the gateway can route without a key, but only the recipient's private key can read the FHIR payload.

Generate **dummy** dev keys once (shared by both projects):

```bash
cd ~/Aeternix/nhcx-mock
uv run python scripts/gen_dev_keys.py ../dev-keys
# creates ~/Aeternix/dev-keys/{aeternix-provider@hcx,mock-payer@hcx}.{private,public}.pem
```

Keys are files named `<participant_code>.private.pem` and `.public.pem` in the keys folder (default `../dev-keys`). They are in `.gitignore` and must never be committed. On the real sandbox, Aeternix's keypair comes from NHCX onboarding and insurer public keys come from the Fetch Certs API.
