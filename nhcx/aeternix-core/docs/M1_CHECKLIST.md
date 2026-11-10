# ABDM Milestone 1: what Aeternix covers, and how to demo it

Screen: **http://localhost:8001/ui/abha** (also linked as "ABHA desk" on the insurance desk).
Mandatory rows of NHA's sheet "M1 ABHA Creation and Verification with APIs" (V1.2), as a private integrator.

## Creation via Aadhaar OTP (tab "Create ABHA")

| Test case | What the tester checks | Where in Aeternix |
|---|---|---|
| CRT_ABHA_101 | Option to create ABHA via Aadhaar OTP | "Create ABHA" tab |
| CRT_ABHA_102 | Consent text shown, "I agree" collected | Step 1; consent sent as `abha-enrollment` v1.4 |
| CRT_ABHA_103 (optional) | Consent in other languages | English / हिन्दी toggle |
| CRT_ABHA_104 | Aadhaar entry, error for wrong number | Step 2: masked field + eye toggle, 12-digit + Verhoeff check |
| CRT_ABHA_105 | OTP to Aadhaar mobile, entered | Step 3; message shows only last 4 digits |
| CRT_ABHA_106 | Resend OTP | Max 2 resends, 60 s apart (screen and server) |
| CRT_ABHA_107 | OTP verification | "Verify & create ABHA"; wrong OTP shows ABDM's message |
| CRT_ABHA_109 | Communication mobile verified by OTP | Step 4 appears only if it differs from the Aadhaar mobile |
| CRT_ABHA_112 | Suggested ABHA address / custom | Step 5: suggestion chips or own handle (@sbx) |
| CRT_ABHA_113 | 14-digit ABHA number + address shown | Result panel |
| CRT_ABHA_114 | ABHA card from API, view + download | "View ABHA card" / "Download ABHA card" |
| (guide) | Existing Aadhaar: no dead end | "ABHA already exists" + profile shown |

## Verification (tab "Verify ABHA")

| Test case | Flow | Choose |
|---|---|---|
| VRFY_ABHA_101 | ABHA number + Aadhaar OTP | ABHA number, OTP to Aadhaar-linked mobile |
| VRFY_ABHA_102 | ABHA address + Aadhaar OTP | ABHA address, Aadhaar-linked mobile |
| VRFY_ABHA_201 | ABHA number + ABHA mobile OTP | ABHA number, ABHA-linked mobile |
| VRFY_ABHA_202 | ABHA address + ABHA mobile OTP | ABHA address, ABHA-linked mobile |
| VRFY_ABHA_301-305 | Find ABHA by mobile: invalid mobile error, none found, one/many found (pick), wrong OTP, resend | Mobile number |
| VRFY_ABHA_401-405 | Find ABHA by Aadhaar: invalid Aadhaar, wrong OTP, none found (offer "Create"), found, resend | Aadhaar number |

Every successful verification shows the profile and the card.

## Hospital rules

| Test case | Where |
|---|---|
| TAGGING_UNIQUEPATIENTID_UNIQUEABHANUMBER | "Patients" tab: one ABHA = one patient ID (`AXP-...`); repeat visits reuse it (unique index in the DB) |
| SHARE_PATIENT_PROFILE_701 | "Scan & Share" tab: counter QR, profile arrives at `{bridge}/api/v3/hip/patient/share`, OPD token sent back via on-share right away (10 s SLA) |

## Security (privacy points the tester looks for)
- Aadhaar, mobile, OTP and ABHA number are RSA-encrypted (OAEP/SHA-1) with ABDM's key, which is fetched and cached (12 h), never hard-coded.
- Aadhaar field masked by default; cleared after use. Mobile numbers shown only as ******1234.
- ABDM user tokens stay on the server; the browser gets a short opaque session id.
- No CAPTCHA (per NHA).

## Before the NHA demo
1. `uv run python scripts/abha_check.py` → all OK.
2. Compare the consent text (`src/aeternix_core/abha/consent.py`) word for word with NHA's published text.
3. Scan & Share needs the facility's **HIP ID** (HFR registration + linked to bridge SBXID_089767). Put it in `.env` as `AETERNIX_HIP_ID`, keep the tunnel running and the bridge URL current.
4. Fill the sheet's Status / Remark columns; record the screen while running each row with your own Aadhaar.
