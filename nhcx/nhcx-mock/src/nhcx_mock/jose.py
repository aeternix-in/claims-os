"""JWE helpers for the HCX envelope (sandbox format).

Compact JWE: protected header = {"alg": "RSA-OAEP-256", "enc": "A256GCM", x-hcx-* ...},
payload = the FHIR bundle as JSON, encrypted to the RECIPIENT's public key.
The protected header is integrity-protected but readable, so a gateway can route
without decrypting; only the recipient's private key can read the FHIR payload.

Keys are files: <keys_dir>/<participant_code>.private.pem and .public.pem.
(The real sandbox publishes participant certificates via its Fetch Certs API.)
"""
import base64
import json
from pathlib import Path

from jwcrypto import jwe, jwk
from jwcrypto.common import JWException

from .config import get_settings
from .errors import HcxError

ALG, ENC = "RSA-OAEP-256", "A256GCM"


def _key(code: str, kind: str) -> jwk.JWK:
    path = Path(get_settings().keys_dir) / f"{code}.{kind}.pem"
    if not path.exists():
        raise HcxError(400, "ERR_INVALID_ENCRYPTION", f"no {kind} key for participant {code!r}")
    return jwk.JWK.from_pem(path.read_bytes())


def encrypt(headers: dict, fhir: dict) -> str:
    """Encrypt the bundle to headers['x-hcx-recipient_code']'s public key."""
    protected = {"alg": ALG, "enc": ENC, **headers}
    token = jwe.JWE(json.dumps(fhir).encode(), recipient=_key(headers["x-hcx-recipient_code"], "public"),
                    protected=json.dumps(protected))
    return token.serialize(compact=True)


def read_protected(token: str) -> dict:
    """Read the protected header WITHOUT decrypting (what a gateway does for routing)."""
    parts = token.split(".")
    if len(parts) != 5:
        raise HcxError(400, "ERR_INVALID_ENCRYPTION", "payload is not a compact JWE (expected 5 parts)")
    try:
        raw = base64.urlsafe_b64decode(parts[0] + "=" * (-len(parts[0]) % 4))
        header = json.loads(raw)
    except ValueError as e:
        raise HcxError(400, "ERR_INVALID_ENCRYPTION", "unreadable JWE protected header") from e
    if header.get("alg") != ALG or header.get("enc") != ENC:
        raise HcxError(400, "ERR_INVALID_ENCRYPTION",
                       f"JWE must use alg={ALG}, enc={ENC} (got {header.get('alg')}/{header.get('enc')})")
    return header


def decrypt(token: str, recipient_code: str) -> dict:
    """Decrypt with the recipient's private key and return the FHIR bundle."""
    try:
        t = jwe.JWE()
        t.deserialize(token, key=_key(recipient_code, "private"))
        return json.loads(t.payload)
    except (JWException, ValueError) as e:
        raise HcxError(400, "ERR_INVALID_ENCRYPTION", f"cannot decrypt payload: {type(e).__name__}") from e


def protocol_headers(protected: dict) -> dict:
    return {k: v for k, v in protected.items() if k.startswith("x-hcx-")}
