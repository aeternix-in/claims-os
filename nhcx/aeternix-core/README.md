# aeternix-core

Aeternix's provider-side core: the internal claim model and its transformation into
NHCX FHIR bundles. Tested against the local `nhcx-mock`.

```
HIS / form ─► intake (later) ─► POST /cases ─► ClaimCase ─► transformer ─► envelope ─► NHCX client ──► NHCX
                                                   ▲                                                 │
                                       case state machine ◄── /hcx/v1/*/on_submit, paymentnotice ◄───┘
```

The service is both a **client** (it sends pre-auths and claims to NHCX) and a **server**
(NHCX calls it back with the insurer's decisions and payment notices).

## Setup

This project expects `nhcx-mock` to sit next to it:

```
~/Aeternix/
  nhcx-mock/
  aeternix-core/
```

```bash
cd ~/Aeternix
unzip ~/Downloads/aeternix-core.zip
cd aeternix-core
uv sync              # installs pydantic, plus pytest, httpx and ../nhcx-mock for tests
uv run pytest -v
```

## Try it against the running mock

```bash
# terminal 1
cd ~/Aeternix/nhcx-mock && uv run uvicorn nhcx_mock.main:app --reload --app-dir src

# terminal 2
cd ~/Aeternix/aeternix-core && uv run python scripts/submit_sample.py          # or: reject / partial / query
```

## Staff UI

With both servers running (see below), open **http://127.0.0.1:8001/ui**.

- **Case queue** on the left, with filters: *Needs action* (send pre-auth, send claim, answer a query, resend after a failure), *With insurer*, and *Closed*.
- **Case page**: a journey track (Admitted → Pre-auth → Claim → Paid) showing where the case is and whether it's waiting, approved, needs info or rejected. It also shows the insurer's latest decision, the bill, the clinical details and the history. The action buttons only offer moves the case allows. The page refreshes itself every 3 seconds.
- **New case** form in plain hospital terms. Validation errors are listed in plain language ("Diagnosis #1 ICD-10 code: not a valid ICD-10 code"). *Fill with sample data* loads the official example case.

It's a single file (`api/static/index.html`) with no build step and no extra dependencies, and it works offline (the web font is optional).

## Demo mode (for showing the system)

Start Aeternix with `AETERNIX_DEMO_MODE=true` to get:

- **Run a demo case**: one click creates a realistic case, with no form to fill.
- **Insurer will…** selector next to the send buttons: choose the mock insurer's decision (approve, approve part, reject, ask for more info, not respond).
- **Simulate insurer payment** once a claim is approved.

With demo mode off, these controls and the `/demo/*` endpoints don't exist, and the `scenario` parameter is ignored. Never enable demo mode against a real gateway.

The **Under the hood** panel on every case page is available in both modes. It lists every message exchanged with NHCX. Opening one shows the FHIR resources Aeternix built (or received), the protocol headers, the message as it travelled (the encrypted token in JWE mode, colour-coded), the exchange's reply, how Aeternix interpreted an insurer decision, and the full FHIR document.

Best demo setup (encrypted, so the panel shows the sandbox's real format):

```bash
# Terminal 1
cd nhcx-mock
NHCX_MOCK_ENVELOPE_MODE=jwe NHCX_MOCK_CALLBACK_BASE_URL=http://127.0.0.1:8001 \
  uv run uvicorn nhcx_mock.main:app --app-dir src --port 8000
# Terminal 2
cd aeternix-core
AETERNIX_DEMO_MODE=true AETERNIX_ENVELOPE_MODE=jwe \
  uv run uvicorn aeternix_core.api.app:app --app-dir src --port 8001
# Browser: http://127.0.0.1:8001/ui
```

## Run the whole loop live (two servers)

```bash
# Terminal 1: mock, sending callbacks to Aeternix on port 8001
cd ~/Aeternix/nhcx-mock
NHCX_MOCK_CALLBACK_BASE_URL=http://127.0.0.1:8001 uv run uvicorn nhcx_mock.main:app --app-dir src --port 8000

# Terminal 2: Aeternix
cd ~/Aeternix/aeternix-core
uv run uvicorn aeternix_core.api.app:app --app-dir src --port 8001

# Terminal 3: create a case, then pre-auth, claim and payment
cd ~/Aeternix/aeternix-core
uv run python scripts/demo_flow.py
```

Aeternix's API docs are at http://127.0.0.1:8001/docs.

**Encrypted (sandbox format):** generate dev keys once, then add the mode to both servers:

```bash
cd ~/Aeternix/aeternix-core && uv run python scripts/gen_dev_keys.py ../dev-keys

# Terminal 1
NHCX_MOCK_ENVELOPE_MODE=jwe NHCX_MOCK_CALLBACK_BASE_URL=http://127.0.0.1:8001 \
  uv run uvicorn nhcx_mock.main:app --app-dir src --port 8000
# Terminal 2
AETERNIX_ENVELOPE_MODE=jwe uv run uvicorn aeternix_core.api.app:app --app-dir src --port 8001
```

## ABHA desk (ABDM Milestone 1)

Create and verify patients' ABHA, and register them by Scan & Share: **http://127.0.0.1:8001/ui/abha**.
Needs the sandbox client credentials in `.env` (same as NHCX). Check the setup safely first:

```bash
uv run python scripts/abha_check.py
```

Code in `src/aeternix_core/abha/`; test-case mapping and demo steps in `docs/M1_CHECKLIST.md`.

## NHCX contract (NHA Implementation Guide 2026-27)

Aeternix builds messages to NHA's payer guide, not only to the NRCeS FHIR example:

- **Identifiers:** Claim **CLN** (case number = hospital case no.), hospital **NPI** (HFR ID) + name, insurer **NIIP** (participant code) + name, Coverage **NH** (product code from Get Policy), doctor **HPIN** ("NA" refused at intake), patient **PI** (UHID) and **ADN** (Aadhaar).
- **workflow_id codes:** 12 new pre-auth, 121 revised pre-auth after a query, 15 new claim, 19 / 161 query reply.
- **Recipient:** the processing participant (`insurer.processing_code`, i.e. the TPA when one handles the case), falling back to the insurer.
- **Insurer answers:** complete = approved (partly approved when the eligible amount is below the requested amount), error = rejected, partial = queried, queued = still submitted (no change).
- **Queries:** the insurer sends `communication/request`; staff reply from the case page (text plus PDF/JPG/PNG attachments) via `communication/on_request`; the insurer then sends its final decision.
- **Payment:** reads the PaymentNotice and the bank UTR, and acknowledges with a Task carrying the case number.

## Case lifecycle

```
draft → preauth_submitted → preauth_approved / preauth_partially_approved → claim_submitted → claim_approved → paid
                          ↘ preauth_rejected (terminal)                                     ↘ claim_rejected (terminal)
                          ↘ preauth_query → preauth_query_answered → decision               ↘ claim_query → claim_query_answered → decision
                                         ↘ preauth_submitted (revised pre-auth, workflow 121)
                          ↘ preauth_failed → retry                                          ↘ claim_failed → retry
```
Every change is saved in `case_events`, and every message to or from NHCX in `exchanges`.

## API

| Endpoint | Who calls it | Purpose |
|---|---|---|
| `POST /cases` | Aeternix (UI or intake) | Create a case from a `ClaimCase` JSON |
| `GET /cases` | Aeternix (UI) | Case list: patient, insurer, total, status |
| `GET /cases/{id}/messages/{n}` | Aeternix (UI) | One NHCX message: wire form, readable form, interpretation |
| `POST /demo/case`, `POST /demo/cases/{id}/pay` | Demo mode only | Demo case, simulated payment |
| `GET /cases/{id}` | Aeternix | Status, last insurer decision, history, messages |
| `POST /cases/{id}/submit/{workflow}` | Aeternix | Send to NHCX (`preauth` or `claim`) |
| `POST /hcx/v1/preauth/on_submit` | NHCX | Insurer's pre-auth decision |
| `POST /hcx/v1/claim/on_submit` | NHCX | Insurer's claim decision |
| `POST /cases/{id}/query-reply` | Aeternix (UI) | Answer the insurer's document query (text + attachments) |
| `POST /hcx/v1/communication/request` | NHCX | Insurer asks for more information |
| `POST /hcx/v1/paymentnotice/request` | NHCX | Payment notice; we acknowledge with a Task via `paymentnotice/on_request` |

Reliability rules built in:
- A case is marked as submitted *before* sending, because the callback can arrive while the send is still in flight.
- The same applies to query replies: the case moves to "reply sent" before sending, and the query reopens if the reply isn't delivered.
- Duplicate callbacks (same `api_call_id`) are acknowledged and ignored.
- Callbacks for unknown conversations, or addressed to another hospital, are rejected.
- Gateway errors or network failures mark the case `*_failed`, and it can be retried.
- Illegal moves (for example, claiming after a rejected pre-auth) are refused with 409.

## What's here

| File | Purpose |
|---|---|
| `domain/models.py` | Internal model: `ClaimCase` with Patient, Hospital, Insurer, Doctor, Policy, Diagnosis, Procedure, LineItem. Plain business terms, validated at intake (ICD-10 format, dates, policy expiry, money with 2 decimals, no unknown fields). |
| `nhcx/codes.py` | Every FHIR code system and code we emit, each taken from the official NRCeS example. |
| `nhcx/fhir_claim.py` | `to_claim_bundle(case)`: ClaimCase → NHCX ClaimBundle. Deterministic resource ids (uuid5 of case_id + role). |
| `nhcx/envelope.py` | `build_request(case, bundle)`: HCX headers + plain-mode body. |
| `nhcx/jose.py` | JWE encrypt/decrypt (RSA-OAEP-256 / A256GCM) for JWE mode. |
| `nhcx/client.py` | HTTP client for NHCX (sends the `bearer_auth` header). |
| `nhcx/inbound.py` | Parses and validates messages NHCX sends to us. |
| `nhcx/responses.py` | Turns a ClaimResponseBundle into a decision: approved, partially approved, rejected or query. |
| `domain/lifecycle.py` | Case states and allowed transitions. |
| `services.py` | Business operations: create, submit, handle callbacks and payment notices. |
| `store.py` | SQLite tables: `cases`, `exchanges`, `case_events`. |
| `api/app.py` | FastAPI app (UI, Aeternix-facing and NHCX-facing endpoints). |
| `api/static/index.html` | Staff UI (case queue, case page with query reply, new case form). |
| `samples.py` | `sample_preauth()`: the official example's clinical content (angioplasty case, ₹70,000) in our internal model. |

## Tests

- `test_models.py`: intake validation rules.
- `test_fhir_claim.py`: **our output equals the official NRCeS ClaimBundle example**, field by field. There are two documented, intentional differences: we use +05:30 (IST) where the example has +05:00, and we add `item.quantity`.
- `test_against_mock.py`: transformer and envelope output accepted by nhcx-mock.
- `test_lifecycle.py`: state machine rules and response interpretation.
- `test_nhcx_contract.py`: guide rules end to end: workflow codes, query reply flow, revised pre-auth (121), payment acknowledgement Task, TPA routing, failed reply reopening the query.
- `test_demo.py`: demo endpoints hidden when off, chosen outcomes, message detail (plain and encrypted).
- `test_ui.py`: UI is served, sample case is valid, case list and next-action rules.
- `test_loop_jwe.py`: the full journey with **both sides encrypting**. It checks that no stored message contains readable patient data.
- `test_loop.py`: **both real apps wired together**. Covers draft to paid; rejection blocking a claim; partial approval; query then resubmit; payer timeout; gateway rejection then retry; network failure; duplicate callback; unknown conversation; missing auth.

## Known gaps (marked `TODO` in code)

- ABHA travels in the `x-hcx-ben-abha-id` header; no Patient identifier type for it is sourced.
- Where NHA's guide gives an identifier code but not its code system (CLN, HPIN, the payment-ack output type), the system is marked `ASSUMPTION` in `nhcx/codes.py`.
- Not built yet: Get Policy (to fill product code and processing code automatically), coverage eligibility, insurance plan, task cancel/reprocess, search, status, enhancement (13).
- Only the "final" diagnosis type is supported, because it's the only one sourced from the example.
- Only inpatient care is supported.
- There are no intake adapters yet; each hospital's HIS needs its own mapping (see "intake" below), and clinical code mappings need medical sign-off.
- Each message's readable copy (`exchanges.plain`) is patient data stored unencrypted. Before production, decide on database encryption and retention.
- Decision mapping (`responses.py`) and the payment-ack payload are assumptions until verified on the sandbox.
- Insurer public keys are read from files; on the sandbox they should come from the Fetch Certs API (with caching).
- There's no automatic follow-up for payer timeouts yet (for example, a `/hcx/v1/status` check after N hours).

## Sandbox mode (real NHCX)
Set in `.env`: `AETERNIX_AUTH_MODE=session`, `AETERNIX_KEY_SOURCE=nhcx`, plus the sandbox client ID/secret.
- `nhcx/session.py`: ABDM v3 session token, cached, refreshed 2 min before expiry, refreshed on 401.
- `nhcx/certs.py`: insurer public key via Fetch Certs (lenient reader for sandbox certs), cached ≤24 h, expired certs refused.
- Sending real pre-auths still needs participant registration with a public HTTPS callback URL.
