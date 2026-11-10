"""ABDM Milestone 1: ABHA creation, ABHA verification and Scan & Share (OPD registration).

ABDM creates the ABHA; Aeternix is the counter around it: consent, input checks, encryption,
OTP handling, showing the card, and linking each ABHA to exactly one hospital patient record.

Modules
  validate   Aadhaar (Verhoeff), mobile, ABHA number / address checks and masking
  crypto     ABDM public key (fetched + cached, never hard-coded) and RSA-OAEP(SHA-1) encryption
  client     HTTP to the ABHA v3 APIs (session token, REQUEST-ID/TIMESTAMP, error mapping)
  flows      the M1 flows: enrolment by Aadhaar OTP, login/verification, search, profile, card
  patients   one ABHA <-> one patient ID
  share      Scan & Share: profile push from the gateway, OPD token, on-share callback
  consent    the consent text shown before ABHA creation
"""
