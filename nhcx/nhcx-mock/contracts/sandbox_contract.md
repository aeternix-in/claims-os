# NHCX sandbox contract notes

Source: NHA-ABDM/nhcx GitHub, AWS(Sandbox) use-case Postman collection.

## Confirmed from the collection
- Base URL: `https://apisbx.abdm.gov.in/hcx/v1/...`, all endpoints POST.
- Auth header is literally `bearer_auth: Bearer <JWT>` (not `Authorization`). Token = ABDM Keycloak (central-registry).
- Action body: `{"payload": "<JWE>"}`; callback body: `{"type": "JWEPayload", "payload": "<JWE>"}`.
- JWE protected header: `alg=RSA-OAEP-256`, `enc=A256GCM`, plus x-hcx-api_call_id, x-hcx-workflow_id,
  x-hcx-request_id, x-hcx-status, x-hcx-timestamp (`2023-11-06T13:22:06+0530`), x-hcx-sender_code,
  x-hcx-recipient_code (`xxxxx@hcx`), x-hcx-correlation_id, x-hcx-ben-abha-id.
- x-hcx-status: `request.initiated` on actions, `response.complete` on callbacks.
- Endpoint pairs: insuranceplan request/on_request, coverageeligibility check/on_check,
  preauth submit/on_submit, claim submit/on_submit, paymentnotice request/on_request,
  task submit/on_submit, communication request/on_request, search submit/on_submit,
  predetermination submit/on_submit, status/on_status.

## Assumptions in this mock (verify on first sandbox call)
- Sync ack = HTTP 202 `{timestamp, api_call_id, correlation_id}` (collection has no saved responses).
- Error body = HCX style `{timestamp, api_call_id, correlation_id, error{code,message,trace}}`; codes are HCX-like.
- Callback gets a NEW api_call_id; correlation_id / workflow_id / request_id preserved
  (the sample reuses api_call_id; the HCX spec says new per call).
- PaymentNotice direction: payer -> provider via paymentnotice/request; provider acks with on_request.
- Duplicate api_call_id -> 400 ERR_DUPLICATE_API_CALL_ID.

## Not yet covered
- JWE: implemented (RSA-OAEP-256 / A256GCM, file-based dummy keys). Not yet: JWS signing, cert lookup via Fetch Certs.
- FHIR profile validation (rule-based checks only),
  coverageeligibility / communication / task / search / predetermination / status.
- Collection typo: paymentnotice/on_request URL is `apisbx.abdm.gov.inhcx/...` (missing slash).

## FHIR sources used (ABDM FHIR IG v6.5.0, nrces.in)
- Request fixture: Bundle-ClaimBundle-preauthorization-example-01 (narrative text removed).
- Response shape: Bundle-ClaimResponseBundle-preauthorization-example-01
  (ClaimResponse + echoed request entries; submitted/eligpercent adjudication;
  ndhm-adjudication-reason "covered"; preAuthRef/preAuthPeriod; identifier echoes request).
- Other official examples available: ClaimBundle / ClaimResponseBundle for enhancement,
  predetermination and settlement.
