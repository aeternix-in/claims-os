# Aeternix Claims OS

Cashless insurance claims for Indian hospitals, from admission to the money in the bank: policy check at admission,
bill audit, document intake with a pre-submission check, payer queries, follow-up letters, bank matching, leakage
reports, and NHCX.

> **Status:** working end to end on local machines against a mock insurer. **Demo data only:** the current Supabase
> project is in Singapore and known security gaps are open. Read [Security and data protection](#security-and-data-protection)
> and [Before real patients](#before-real-patients) first.

---

## Contents
- [How it fits together](#how-it-fits-together)
- [Run it locally](#run-it-locally)
- [Users and roles](#users-and-roles)
- [Test it: a walkthrough](#test-it-a-walkthrough)
- [NHCX](#nhcx)
- [Automated tests](#automated-tests)
- [Deploy](#deploy)
- [Repository layout](#repository-layout)
- [Rules and sources](#rules-and-sources)
- [Troubleshooting](#troubleshooting)
- [What is built, what is open](#what-is-built-what-is-open)
- [Security and data protection](#security-and-data-protection)
- [Before real patients](#before-real-patients)

---

## How it fits together

```
 Browser (web/, React)  ─── reads (RLS, own hospital only) ───►  Supabase Postgres
        │                                                         ▲
        └── every change ──►  Engine (engine/, FastAPI)  ─────────┘
                                 │  state machine, bill audit, OCR, checks,
                                 │  queries, letters, bank match, reports
                                 ▼
                       NHCX layer (nhcx/aeternix-core)  ◄──►  NHCX gateway  (nhcx/nhcx-mock locally)
```

- **The browser only reads.** Row-level security limits each user to their own hospital. It has no write access.
- **Every change goes through the engine**, and every claim move goes through one state machine
  (`engine/app/state_machine.py`, 17 stages). Each move adds a row to `case_events`, which cannot be edited or deleted.
- **Nothing reaches a payer without a person.** People confirm key fields, clear check items with a named note, a
  doctor signs clinical replies, and someone approves every letter.
- **Only a matched bank credit closes a claim.** A payer's "paid" message is not enough.

---

## Run it locally

Tested on Ubuntu with Python 3.12 (via uv) and Node 22.

### 1. One-time system packages
```bash
sudo apt install -y tesseract-ocr poppler-utils nodejs npm     # OCR, PDF tools, Node
curl -LsSf https://astral.sh/uv/install.sh | sh                # uv (Python manager)
source ~/.bashrc
node -v                                                         # must be 18 or higher
```
If Node is older: `curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs`.
You do **not** need Python 3.12 from apt: uv downloads it.

### 2. Python environment
```bash
cd aeternix                       # the folder with engine/, web/, nhcx/, supabase/
uv venv .venv --python 3.12
source .venv/bin/activate
uv pip install -r engine/requirements.txt
uv pip install -e nhcx/aeternix-core -e nhcx/nhcx-mock
```

### 3. Supabase (once per project)
1. Create a project. **Region: Mumbai (ap-south-1)** for anything beyond demo data (see
   [Before real patients](#before-real-patients)). Keep RLS and the Data API **on**.
2. **SQL editor:** run each file in `supabase/migrations/` **once, in order**: `0001` → `0008`.
3. **Authentication → Users → Add user** (tick *Auto confirm*). Create at least `owner` and `desk` users.
4. Run `supabase/seed.sql` once, with `CHANGE-ME@example.com` replaced by one user's email. This creates the hospital,
   7 payers and that user's profile.
5. Link the other users. See [Users and roles](#users-and-roles).

### 4. Settings
```bash
cp engine/.env.example engine/.env
cp web/.env.example web/.env
python nhcx/aeternix-core/scripts/gen_dev_keys.py dev-keys     # dummy NHCX keys for the mock; never commit
```

`engine/.env`
```ini
SUPABASE_URL=https://<project-ref>.supabase.co
DATABASE_URL=postgresql://postgres.<project-ref>:<password>@aws-?-<region>.pooler.supabase.com:5432/postgres
SUPABASE_JWT_SECRET=                      # leave empty on new projects
SUPABASE_SERVICE_ROLE_KEY=<secret key>     # server only, never in web/
CORS_ORIGINS=http://localhost:5173

NHCX_ENABLED=true
AETERNIX_NHCX_BASE_URL=http://127.0.0.1:8000
AETERNIX_ENVELOPE_MODE=jwe                 # or plain (then no keys needed)
AETERNIX_KEYS_DIR=../dev-keys
AETERNIX_DEMO_MODE=true                    # mock only, never against a real gateway
```
`web/.env`
```ini
VITE_SUPABASE_URL=https://<project-ref>.supabase.co
VITE_SUPABASE_ANON_KEY=<anon / publishable key>
VITE_ENGINE_URL=http://localhost:8080
```

Where to find them:
| Value | Supabase dashboard |
|---|---|
| Project URL | Project Settings → Data API (or the `<project-ref>` from the pooler string) |
| `DATABASE_URL` | **Connect → Direct → Session pooler**, type URI (direct connection is IPv6-only) |
| anon / publishable key | Project Settings → API Keys |
| service role / secret key | Project Settings → API Keys |

If the database password has `@ # / : ? %`, encode it in the URL:
`python3 -c "import urllib.parse,getpass; print(urllib.parse.quote(getpass.getpass(), safe=''))"`

Check the connection: `cd engine && python -c "from app.db import pool; print(pool().connection().__enter__().execute('select count(*) from hospitals').fetchone())"`
should print `{'count': 1}`.

### 5. Start it (three terminals, each from `aeternix/`)
```bash
# 1. NHCX mock insurer
source .venv/bin/activate && cd nhcx/nhcx-mock
NHCX_MOCK_CALLBACK_BASE_URL=http://127.0.0.1:8080 NHCX_MOCK_ENVELOPE_MODE=jwe NHCX_MOCK_KEYS_DIR=../../dev-keys \
  uvicorn nhcx_mock.main:app --app-dir src --port 8000

# 2. Engine
source .venv/bin/activate && cd engine
uvicorn app.main:app --port 8080 --reload

# 3. Web app
cd web && npm install && npm run dev        # later runs: just npm run dev
```
Open **http://localhost:5173** (the product), **http://localhost:5173/demo/** (investor demo, sample data). API docs: engine at http://localhost:8080/docs, mock at http://localhost:8000/docs.

**Both NHCX terminals must use the same envelope mode** (`jwe` in both, or `plain` in both).

---

## Users and roles

| Role | Does |
|---|---|
| `desk` | Admissions, bill, documents, pre-auth, discharge, payer queries, NHCX sends |
| `doctor` | Clears clinical check items, signs clinical query replies, advises discharge |
| `accounts` | Claim file and settlement, bank match, letters, payer terms |
| `owner` | Setup, payers, bank match, reports; read-only on claim work |
| `admin` | Everything (use sparingly) |

Link users after creating them in Authentication → Users:
```sql
insert into profiles (user_id, hospital_id, full_name, role)
select u.id, (select id from hospitals order by created_at limit 1), x.name, x.role::user_role
from auth.users u
join (values ('contact@aeternix.in', 'Aeternix Admin',  'owner'),
             ('desk@aeternix.in',    'Insurance Desk',  'desk'),
             ('doctor@aeternix.in',  'Dr Test',         'doctor'),
             ('accounts@aeternix.in','Accounts',        'accounts')) as x(email, name, role)
  on lower(u.email) = x.email
on conflict (user_id) do update set role = excluded.role, full_name = excluded.full_name;
```
Check: `select u.email, p.role from profiles p join auth.users u on u.id = p.user_id;`
After changing a user's role or link, **refresh the page**.

Testing several roles at once: use a normal window, a private window and another browser.

---

## Test it: a walkthrough

About 20 minutes. Note anything odd with a screenshot (see [Reporting a problem](#reporting-a-problem)).

**Owner**
1. **Setup:** HFR ID `45675454`, NHCX code `aeternix-provider@hcx`, check the room tariff. Save.
2. **Payers → Insurer C:** NHCX code `mock-payer@hcx`. Save payer. Look at the rulebook (discount, TDS, package rates).

**Desk**
3. **Claims desk → New admission:** payer Insurer C, sum insured e.g. ₹3,00,000, room limit "1% of sum insured",
   pick a **single room**. Check the policy check on the right warns about a proportionate cut. Fill DOB, sex,
   ICD-10 (`I25.1`), doctor's HPR ID, member ID and product code if you'll test NHCX. Create.
4. **Bill and audit:** add lines (room, OT, surgeon, pharmacy, a non-medical item), **Save and check**. Check the payer
   and family amounts. For NHCX, give each line a SNOMED code (`77343006` angiography, `418285008` angioplasty; ICU fills itself).
5. **Documents:** upload a policy card, photo ID, admission note and a report (PDF or phone photo). Each should be
   sorted by type; **confirm** the key fields it reads. The pre-flight card on the right should lose its red items.
6. **Next step → Start pre-auth.** Clear any amber item with a note. Clinical ambers need the doctor.

**Doctor**
7. Open the same claim and clear the clinical amber items with a note.

**Desk, NHCX path**
8. **NHCX tab:** fill anything listed as missing (**Details for NHCX**). Choose *Insurer will… → ask for more info*, send.
   The journey shows the query; **Under the hood** shows each message.
9. **Queries tab:** **Draft a reply from the record**, fill bracketed items, attach a document, have the doctor
   **Approve and sign** if asked, then **Mark as sent to payer**. The insurer's final decision should arrive by itself.

**Desk, without NHCX** (any payer without an NHCX code): use **Next step** to record the payer's answers by hand.

10. Continue: **Advise discharge** (doctor) → **Request final approval** (needs discharge summary + bill) →
    **Record final approval** → **Prepare claim file** → send the claim (NHCX tab) or **Submit claim file**.

**Accounts / owner**
11. NHCX path: after the claim is settled, **Simulate insurer payment** on the NHCX tab, then **Bank match → Use a
    sample statement** (or import a CSV). The UTR match should be **Certain**. **Match certain** → claim becomes Paid.
12. **Claim to cash → Run the sweep:** letters appear for overdue, short-paid or rejected claims. Open, approve, PDF.
13. **Leakage** and **Reports:** check the numbers make sense against what you entered. Download the CSVs.

---

## The CTO's UI: product and investor demo

One build serves:

| Address | What it is |
|---|---|
| `/` | **The product.** The CTO's UI on the hospital's real data: sign in, every change saved to Supabase through the engine, shared between users (others' changes appear within 5 s). Real time only, real role from the profile. |
| `/demo/` | **Investor demo.** The CTO's approved demo, exactly (pixel-identical, checked). Sample hospital, simulated payers, fast-forward clock. Runs only in the visitor's browser; it never talks to the engine or Supabase, so it cannot touch real data. |
| `/classic/` | The earlier engine-first screens (kept for reference). |
| `/live/` | Old address; forwards to `/`. |

The UI source is `web/src/demo-ui/app.js` (de-minified from the approved build). Screens are unchanged; every
change is marked `Aeternix:` and switched on only in the product (`LIVE`).

**What the product does that the demo simulates**

- **Payer replies are recorded, not simulated.** Wherever the demo says "Skip to the payer's reply", the product
  says **Record the payer's reply** and opens a form for what actually came in: pre-auth approved / partly / query /
  denied; enhancement; final approval (the payer's figure; standard deductions are worked out from the bill and the
  policy and anything else is recorded as an extra cut, flagged unexplained if no reason was given); settlement
  letter / query / rejection; query reply accepted; money credited (amount, UTR, date); dispute outcome. The claim
  then moves exactly as the demo's own payer logic moves it. "Expected by" times are only reminders.
- **The hospital's own lists.** Setup → Payers, TPAs, Doctors, Procedures. Every dropdown and screen uses them.
  A new payer starts with the rules of a similar payer (edit them on Payers); a new procedure starts from the
  first procedure's charges (edit on Tariffs). Anything used on a claim cannot be removed.
- **Documents.** Upload on a claim's Documents tab stores the file privately (Supabase Storage, or
  `engine/.storage` locally when no service key is set), reads the text (PDF text, OCR for scans) and shows the
  key fields found. View opens the stored file. Remove keeps the file and marks it removed.
- **Bank statement import** (Bank match → Import a statement, CSV) saves the credits for matching.
- **Roles are enforced by the engine:** a doctor cannot change hospital details, payers, rules, tariff,
  doctors, procedures or bank credits. Theme is per device.
- **No data loss on close:** a change waiting to be sent is sent when the tab is hidden; closing with an unsent
  change asks first.
- **PDFs:** the UI's Print buttons (letters, realisation report, summaries) print or save as PDF from the browser.

Storage: migrations `0007_ui_store.sql` (claims, hospital record, append-only audit log) and `0008_ui_files.sql`
(uploaded files); engine `app/ui_store.py` and `app/ui_files.py`.

**Not yet in the product:** sending pre-auths and claims over NHCX from these screens (the NHCX module works on
the engine's own case model, used by `/classic/`; bridging it to the product's claims is the next step), and
adding users from the UI (use `seed` / the Supabase dashboard as in "Users and roles").

## NHCX

The protocol is the team's **aeternix-core** (`nhcx/aeternix-core`): NHA-guide FHIR ClaimBundle (checked against the
official NRCeS example), envelope, JWE, ABDM sandbox session, Fetch Certs, inbound parsing, decision reading.
**nhcx-mock** (`nhcx/nhcx-mock`) plays the exchange and insurer. `engine/app/nhcx.py` connects them to our claims:

| Event | What happens |
|---|---|
| Send pre-auth / claim | Missing identifiers listed in plain words → pre-flight gate → case moved and message saved → sent (workflow 12 / 15; 121 revised pre-auth) |
| Insurer decision | approved / part → pre-auth approved or settled (eligible amount); rejected; queried |
| Insurer query | Opens on the Queries desk; reply goes back over NHCX with attachments (workflow 19 / 161); undelivered → reopens |
| Payment notice | Stores the UTR and amount, acknowledges with a Task; the claim closes only when Bank match finds that UTR |
| Failed send | Case stays put, **Resend** offered |

Every message is stored in `nhcx_exchanges` and shown on the NHCX tab (**Under the hood**).

**Keys:** `dev-keys/` holds dummy keys for the mock only. The real sandbox needs NHCX onboarding (participant code,
keypair, sandbox client ID/secret), then `AETERNIX_AUTH_MODE=session`, `AETERNIX_KEY_SOURCE=nhcx`,
`AETERNIX_DEMO_MODE=false`, and a public HTTPS callback URL. No code changes.

**Off switch:** `NHCX_ENABLED=false` and everything else works; claims then go by the payer's portal as today.

**Changes made to nhcx-mock** to match aeternix-core's NHA-guide contract: "benefit" adjudication on decisions,
the insurer query as `communication/request` (workflow 24 / 27) answered on `communication/on_request`, and a
PaymentReconciliation with the UTR on payment notices.

Docker alternative: `docker compose -f docker-compose.nhcx.yml up` (engine + mock, JWE, demo mode).

---

## Automated tests
```bash
source .venv/bin/activate
(cd engine && pytest -q)                  # 55: state machine, bill audit, intake/OCR, checks, drafting, matching, leakage
(cd nhcx/aeternix-core && pytest -q)      # 85: ClaimBundle vs NHA example, envelope/JWE, contract with the mock
(cd nhcx/nhcx-mock && pytest -q)          # 39
```
End-to-end scripts against a local Postgres (no Supabase): `engine/tests/e2e_*.py`; each file's docstring says how.
The bill audit was checked against the CTO demo's own calculation on 500 random bills (0 differences).

---

## Deploy
- **Web → Netlify:** site from this repo; `web/netlify.toml` sets the build. Add the three `VITE_*` variables.
- **Engine → Fly.io (`bom`) or Render:** `docker build -f engine/Dockerfile .` **from the repo root** (it includes
  aeternix-core, Tesseract and Poppler). Set the variables from `engine/.env.example`; add the Netlify URL to `CORS_ORIGINS`.
- **Supabase:** migrations in order, then seed. Same project for both.
- The service role key goes only on the engine. Never in `web/`, never in git.

---

## Repository layout
```
engine/
  app/            main, auth, db, state_machine, cases, billing (bill audit), adjudicate, intake (OCR), documents,
                  scrubber + checks (pre-flight), drafting + querydesk, letters, matching + bank, leakage + reports,
                  nhcx, setup, storage
  rules/          packs/*.yaml (required documents per payer type), clinical.yaml, nhcx_codes.yaml
  tests/          unit tests + e2e_*.py
web/src/
  screens/        Overview, Desk worklists, Admit, CaseView, QueryDesk, BankMatch, Leakage, Reports, Payers, Setup, Login
  components/     Shell, Worklist, BillPanel, DocumentsPanel, ChecksCard, QueryEditor, LettersPanel, NhcxPanel
supabase/
  migrations/     0001_core … 0006_nhcx  (run once each, in order)
  seed.sql        first hospital, 7 payers, first user
nhcx/
  aeternix-core/  NHCX protocol layer (+ its own README, ABHA desk)
  nhcx-mock/      local NHCX gateway + insurer
docker-compose.nhcx.yml
```

---

## Rules and sources
- `engine/rules/packs/insurer.yaml`, `scheme.yaml`: documents each payer type needs, with the circular each rule comes
  from. Rules marked **UNVERIFIED** (most scheme rules) must be checked against current NHA / CGHS / ECHS circulars.
- `engine/rules/clinical.yaml`: evidence each procedure needs (e.g. gallstone ultrasound for lap chole). **Draft by
  engineering; clinical team must review and sign off (`reviewed_by`).**
- `engine/rules/nhcx_codes.yaml`: SNOMED codes for NHCX. Only codes from the NRCeS example are filled; the clinical
  team adds the rest.
- Bill audit sources (IRDAI proportionate deduction 2020, non-payables list, TDS 194J) are cited in `engine/app/adjudicate.py`.

---

## Troubleshooting

| You see | Cause and fix |
|---|---|
| "Your account isn't linked to a hospital yet" | No `profiles` row for that user. Run the link SQL above, then refresh. If the row exists: `grant select on all tables in schema public to authenticated;` |
| A button or form is missing | Your role can't do that. The screen says who can; bottom-left shows your role |
| "Only the insurance desk creates admissions" | Sign in as a desk user |
| `{'count': 0}` in the connection check | Seed not run (or failed). Run `seed.sql` |
| `column ... already exists` in the SQL editor | That migration already ran. Run each file once; move to the next |
| Engine can't reach the database | Use the **Session pooler** string, not Direct (IPv6). Encode special characters in the password |
| NHCX tab says "not switched on" | `NHCX_ENABLED=true` in `engine/.env`, restart the engine |
| NHCX send fails with a decryption / format error | The engine and mock use different envelope modes. Make both `jwe` or both `plain` |
| NHCX tab lists missing details | Fill them via **Details for NHCX**, Setup (hospital) and Payers (insurer code) |
| Duplicate upload refused | The same file is already on a case in this hospital (files are checked by fingerprint) |
| `npm audit` warnings | Dev-server tooling, not shipped code. Don't run `npm audit fix --force` |

### Reporting a problem
Send: the screen (screenshot), the user/role you were signed in as, the case number, what you clicked, and the last
lines of the **engine** terminal. Never send `.env` contents or keys.

---

## What is built, what is open

**Built:** admission with policy check and room advice · bill audit (discount, MoU rate, proportionate deduction,
package, co-pay, sub-limit, sum insured, TDS) · document upload with OCR, sorting and confirmed fields · pre-flight
check (red blocks, amber needs a named note, clinical items need a doctor) · IRDAI 1-hour and 3-hour clocks ·
query desk (Q1–Q8, replies drafted from the record, doctor sign-off) · follow-up letters with PDF · bank statement
import and matching · leakage L1–L7, payer scorecards, owner's pack, CSV exports · NHCX via aeternix-core ·
setup, payers, roles, live updates.

**Open:**
- Product (`/`): NHCX sending from the claim screens; adding users from the UI.
- Letter sweep and owner digest run by button; to be scheduled.
- NHCX: real sandbox not yet connected; Get Policy, enhancement (13), timeout follow-up not built.
- ABHA desk (ABDM M1) stays in aeternix-core.
- `npm audit` warnings to tidy before production.

## Security and data protection

### Where data is today
| Thing | Where | OK for |
|---|---|---|
| Database, logins, documents (current Supabase project) | **Singapore (ap-southeast-1)**, free plan | **Synthetic / demo data only** |
| Engine, web app | Developers' laptops (`localhost`) | Testing |
| NHCX | Local mock with dummy keys | Testing |

**No real patient data goes into the current project.** It is outside India, and the team's own design rule (D7) and
the DPDP Act point to keeping health data in India. Before a pilot: create a new project in **Mumbai (ap-south-1)**,
run the migrations and seed there, and switch the `.env` values (about 15 minutes, no code changes). The engine host
must also be in India (Fly.io `bom`, or a Mumbai provider).

### What protects data now
- **Hospital isolation:** row-level security on every table; tested that a second hospital sees nothing.
- **No writes from the browser:** browsers can only read; every change goes through the engine, which checks the
  user's role and hospital on each request.
- **Logins:** Supabase Auth. The engine verifies each request's token (JWKS or the project's JWT secret).
- **Audit trail:** `case_events` cannot be edited or deleted (database trigger). Original documents are stored
  unchanged in a **private** bucket, named by SHA-256; viewing uses links that expire in 5 minutes.
- **Messages out:** the owner's pack and digests carry case numbers, not patient names.
- **NHCX:** JWE encryption in transit (as the exchange requires); stored wire copies are encrypted.

### Known gaps (fix before real patients)
| Gap | Risk | Fix |
|---|---|---|
| **NHCX callbacks only check that a `bearer_auth` header exists**, not that it is the gateway's | Someone who finds the URL could post fake insurer decisions (they would still need a valid conversation ID) | Verify the gateway's token or signature on every callback; allow only the gateway's IPs |
| Readable copies of NHCX messages (`nhcx_exchanges.plain`) and document text (`document_pages`) are stored unencrypted in the database | Patient data in plain text if the database leaks | Encrypt these columns, or keep only what the UI needs; set a retention period |
| Server keys in `.env` files on laptops | Leak gives full database access | Keep `.env` out of git (already ignored); use the host's secret store in production; rotate keys if a laptop is lost |
| `SUPABASE_SERVICE_ROLE_KEY` bypasses all RLS | Full access if leaked | Engine only; never in `web/`, chat, screenshots or tickets |
| `dev-keys/` are dummy NHCX keys | None for the mock; must never reach production | Ignored by git; production keys come from NHCX onboarding |
| `AETERNIX_DEMO_MODE=true` lets the UI choose the insurer's answer | Must never be on against a real gateway | Off in production; the engine ignores scenarios unless demo mode is on |
| No rate limiting, no MFA, no session timeout policy | Brute force or shared-computer misuse | Supabase MFA for staff, rate limits on the engine, shorter sessions |
| Free Supabase plan: daily backups only, project pauses when idle | Data loss window, demo downtime | Paid plan with point-in-time recovery before a pilot |
| `npm audit` warnings (dev tooling) | Low (dev server only) | Update versions before production |
| No DPDP paperwork yet | Legal | Consent and notice at admission, data processing agreement with each hospital, breach process, retention policy |

### Never share
`.env` files, database password, service role key, private keys, or real patient documents in chat, email,
screenshots or GitHub issues. Use synthetic data for bug reports.

## Before real patients
1. **Mumbai region:** move off the Singapore project (above).
2. **Fix the known security gaps** above, starting with NHCX callback verification and column encryption.
3. **Clinical sign-off** of `engine/rules/clinical.yaml` and the SNOMED codes.
4. **Scheme rules** marked UNVERIFIED checked against current circulars.
5. **Letter citations** for grievance and ombudsman escalation reviewed by legal or operations.
6. **Accuracy on real files:** check OCR and field extraction on consented real documents from the pilot hospital.
7. **DPDP:** consent at admission, agreements with the hospital, retention and breach process.
