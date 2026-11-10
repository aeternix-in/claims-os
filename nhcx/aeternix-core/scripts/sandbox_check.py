"""First contact with the NHCX sandbox: get a session token and check what it allows.

Run on YOUR machine (credentials never leave it). Put them in aeternix-core/.env:

    AETERNIX_SANDBOX_CLIENT_ID=SBX_00xxxx
    AETERNIX_SANDBOX_CLIENT_SECRET=...

Then:
    uv run python scripts/sandbox_check.py                 # try the known session endpoints
    uv run python scripts/sandbox_check.py --participants  # also call NHCX Participant List
    uv run python scripts/sandbox_check.py --certs CODE    # also fetch an insurer's public certificate

It prints status codes, token expiry and the roles inside the token - NEVER the token or secret.

ASSUMPTION: NHA's guide names checkpoint 06 "POST /get/session" without a full URL or body.
The session endpoints below are ABDM gateway sessions APIs (from memory / public write-ups).
If NHA's onboarding email gives a different session URL, pass it with --session-url.
"""
import argparse
import base64
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

CANDIDATES = [
    # ABDM gateway v3 (current ABDM APIs)
    {"name": "ABDM gateway v3", "url": "https://dev.abdm.gov.in/api/hiecm/gateway/v3/sessions",
     "v3": True},
    # ABDM gateway v0.5 (older; still referenced by NHCX material)
    {"name": "ABDM gateway v0.5", "url": "https://dev.abdm.gov.in/gateway/v0.5/sessions", "v3": False},
]
PARTICIPANT_LIST = "https://apisbx.abdm.gov.in/pmjay/sbxhcx/participanthcxservice/fetch/participants/list"


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def decode_jwt(token: str) -> dict:
    """Read a JWT's claims (no signature check: we only want expiry and roles)."""
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError):
        return {}


def summarise(claims: dict) -> None:
    if not claims:
        print("   (token is not a readable JWT)")
        return
    exp = claims.get("exp")
    if exp:
        left = int(exp - time.time())
        print(f"   expires: {datetime.fromtimestamp(exp, timezone.utc).isoformat()}  ({left // 60} min left)")
    print("   client:", claims.get("clientId") or claims.get("azp"))
    print("   issuer:", claims.get("iss"))
    roles = set((claims.get("realm_access") or {}).get("roles", []))
    for client_roles in (claims.get("resource_access") or {}).values():
        roles |= set(client_roles.get("roles", []))
    print("   roles:", ", ".join(sorted(roles)) or "(none)")
    nhcx_like = {r for r in roles if any(w in r.lower() for w in ("provider", "payer", "payor", "hcx", "nhcx"))}
    if nhcx_like:
        print("   NHCX-related roles found:", ", ".join(sorted(nhcx_like)))
    else:
        print("   WARNING: no provider/payer/NHCX role in this token. NHCX calls may be refused;")
        print("            ask NHA/ABDM integration support to attach the NHCX provider role.")


def get_session(client: httpx.Client, cand: dict, cid: str, secret: str) -> str | None:
    body = {"clientId": cid, "clientSecret": secret}
    headers = {"Content-Type": "application/json"}
    if cand.get("v3"):
        body["grantType"] = "client_credentials"
        headers |= {"REQUEST-ID": str(uuid.uuid4()),
                    "TIMESTAMP": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                    "X-CM-ID": "sbx"}
    print(f"\n== {cand['name']}: POST {cand['url']}")
    try:
        r = client.post(cand["url"], json=body, headers=headers)
    except httpx.HTTPError as e:
        print("   network error:", type(e).__name__, e)
        return None
    print("   HTTP", r.status_code)
    try:
        data = r.json()
    except ValueError:
        print("   (non-JSON reply)", r.text[:200].replace(secret, "***"))
        return None
    token = data.get("accessToken") or data.get("access_token")
    if not token:
        safe = {k: v for k, v in data.items() if "token" not in k.lower()}
        print("   no token in reply:", json.dumps(safe)[:300])
        return None
    print("   token received (not printed). expiresIn:", data.get("expiresIn") or data.get("expires_in"))
    summarise(decode_jwt(token))
    return token


def _abdm_headers() -> dict:
    return {"REQUEST-ID": str(uuid.uuid4()),
            "TIMESTAMP": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "X-CM-ID": "sbx"}


def participant_list(client: httpx.Client, token: str) -> None:
    """Read-only call, so it is safe to retry with different header sets.
    Variant A = exactly what NHA's Postman collection sends (Accept, Content-Type, bearer_auth)."""
    print(f"\n== NHCX Participant List: POST {PARTICIPANT_LIST}")
    body = {"role": "PAYER", "fromdate": "01/01/2023", "todate": datetime.now().strftime("%d/%m/%Y")}
    base = {"Accept": "application/json", "Content-Type": "application/json", "bearer_auth": f"Bearer {token}"}
    variants = [
        ("A: Postman headers (Accept, Content-Type, bearer_auth)", base),
        ("B: A + ABDM headers (REQUEST-ID, TIMESTAMP, X-CM-ID)", lambda: {**base, **_abdm_headers()}),
        ("C: B + Authorization: Bearer", lambda: {**base, **_abdm_headers(), "Authorization": f"Bearer {token}"}),
    ]
    for name, headers in variants:
        h = headers() if callable(headers) else headers
        try:
            r = client.post(PARTICIPANT_LIST, json=body, headers=h)
        except httpx.HTTPError as e:
            print(f"   {name}: network error {type(e).__name__}")
            continue
        print(f"   {name}: HTTP {r.status_code}")
        print("     ", r.text[:500].replace(token, "***"))
        if r.status_code < 300:
            print(f"   -> accepted with variant {name[0]}")
            return
    print("   none accepted; send this output to NHA support with error code shown")


FETCH_CERTS = "https://apisbx.abdm.gov.in/pmjay/sbxhcx/participanthcxservice/fetch/certs"


def describe_key(text: str) -> None:
    """Work out what kind of key/certificate NHCX returned and whether we can use it for JWE."""
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    import re

    def rebuilt_pem(t: str) -> bytes | None:
        """Re-wrap a PEM whose line breaks/whitespace are unusual (CRLF, literal \\n, one long line)."""
        t = t.replace("\\r", "").replace("\\n", "\n")
        m = re.search(r"-----BEGIN ([A-Z ]+)-----(.*?)-----END \1-----", t, re.S)
        if not m:
            return None
        body = re.sub(r"[^A-Za-z0-9+/=]", "", m.group(2))
        lines = "\n".join(body[i:i + 64] for i in range(0, len(body), 64))
        return f"-----BEGIN {m.group(1)}-----\n{lines}\n-----END {m.group(1)}-----\n".encode()

    candidates = [("as returned", text.encode())]
    if (fixed := rebuilt_pem(text)) is not None:
        candidates.append(("re-wrapped PEM", fixed))
    try:
        decoded = base64.b64decode(text.strip(), validate=False)
        candidates.append(("base64-decoded", decoded))
        if (fixed := rebuilt_pem(decoded.decode(errors="ignore"))) is not None:
            candidates.append(("base64-decoded, re-wrapped", fixed))
    except ValueError:
        pass
    errors = []
    for label, data in candidates:
        for kind, load in (("X.509 certificate", lambda d: x509.load_pem_x509_certificate(d)),
                           ("public key", load_pem_public_key)):
            try:
                obj = load(data)
            except Exception as e:  # noqa: BLE001 - trying formats in turn
                errors.append(f"{label} as {kind}: {type(e).__name__}: {str(e)[:120]}")
                continue
            key = obj.public_key() if kind == "X.509 certificate" else obj
            print(f"   format: PEM {kind} ({label})")
            if kind == "X.509 certificate":
                print(f"   subject: {obj.subject.rfc4514_string()}")
                print(f"   valid: {obj.not_valid_before_utc:%Y-%m-%d} to {obj.not_valid_after_utc:%Y-%m-%d}")
            if isinstance(key, rsa.RSAPublicKey):
                print(f"   key: RSA {key.key_size} bits -> usable for RSA-OAEP-256 JWE")
            else:
                print(f"   key: {type(key).__name__} -> NOT RSA, check with NHA")
            return
    # Strict parsing failed. NHCX sandbox certificates have name fields the strict parser refuses;
    # read the key directly from the certificate structure instead.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from lenient_cert import CertError, read_certificate
        info = read_certificate(text)
        print("   format: PEM X.509 certificate with NON-STANDARD name fields (strict parser refuses it)")
        print("   read with the lenient reader (scripts/lenient_cert.py): key extracted OK")
        if info["not_before"] and info["not_after"]:
            print(f"   valid: {info['not_before']:%Y-%m-%d} to {info['not_after']:%Y-%m-%d}")
        if info["rsa_bits"]:
            print(f"   key: RSA {info['rsa_bits']} bits -> usable for RSA-OAEP-256 JWE (via lenient reader)")
        else:
            print(f"   key: {type(info['public_key']).__name__} -> NOT RSA, check with NHA")
        return
    except (CertError, ValueError, ImportError) as e:
        errors.append(f"lenient reader: {type(e).__name__}: {str(e)[:120]}")
    print("   format: could not be parsed. First 60 chars:", repr(text[:60]))
    print("   line endings:", "CRLF" if "\r\n" in text else "literal \\n" if "\\n" in text else "LF/none",
          "| lines:", text.count("\n") + 1)
    for err in errors[:6]:
        print("   parser:", err)


def fetch_certs(client: httpx.Client, token: str, participant: str) -> None:
    print(f"\n== NHCX Fetch Certs for {participant}: POST {FETCH_CERTS}")
    try:
        r = client.post(FETCH_CERTS, json={"participantid": participant},
                        headers={"Accept": "application/json", "Content-Type": "application/json",
                                 "bearer_auth": f"Bearer {token}"})
    except httpx.HTTPError as e:
        print("   network error:", type(e).__name__, e)
        return
    print("   HTTP", r.status_code)
    try:
        data = r.json()
    except ValueError:
        print("   (non-JSON reply)", r.text[:300])
        return
    if r.status_code >= 300:
        print("  ", json.dumps(data)[:500])
        return

    def strings(o, path=""):
        if isinstance(o, dict):
            for k, v in o.items():
                yield from strings(v, f"{path}.{k}" if path else k)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                yield from strings(v, f"{path}[{i}]")
        elif isinstance(o, str):
            yield path, o

    print("   reply fields:", ", ".join(p for p, _ in strings(data)))
    long = [(p, v) for p, v in strings(data) if len(v) > 200]     # certificates are long strings
    if not long:
        print("   no certificate-like field found:", json.dumps(data)[:500])
    for path, value in long:
        print(f"   field '{path}' ({len(value)} chars):")
        describe_key(value)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session-url", help="session endpoint from NHA's onboarding email (overrides the defaults)")
    ap.add_argument("--participants", action="store_true", help="also call NHCX Participant List with the token")
    ap.add_argument("--certs", metavar="CODE", help="also fetch the public certificate of this participant (e.g. 1000xxxx@hcx)")
    args = ap.parse_args()

    load_env(Path(__file__).resolve().parents[1] / ".env")
    cid = os.environ.get("AETERNIX_SANDBOX_CLIENT_ID")
    secret = os.environ.get("AETERNIX_SANDBOX_CLIENT_SECRET")
    if not cid or not secret:
        print("Set AETERNIX_SANDBOX_CLIENT_ID and AETERNIX_SANDBOX_CLIENT_SECRET in aeternix-core/.env")
        return 2

    cands = [{"name": "custom", "url": args.session_url, "v3": "/v3/" in args.session_url}] if args.session_url \
        else CANDIDATES
    with httpx.Client(timeout=30) as client:
        token = None
        for cand in cands:
            token = get_session(client, cand, cid, secret)
            if token:
                break
        if not token:
            print("\nNo session obtained. Check the session URL in NHA's onboarding email and pass --session-url.")
            return 1
        if args.participants:
            participant_list(client, token)
        if args.certs:
            fetch_certs(client, token, args.certs)
    print("\nDone. Nothing secret was printed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
