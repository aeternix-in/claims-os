"""JWE for the HCX envelope: compact JWE, RSA-OAEP-256 / A256GCM, x-hcx-* headers in
the protected header, FHIR bundle as the encrypted payload.

We encrypt outgoing messages to the RECIPIENT's public key and decrypt incoming ones
with OUR private key. Our private key: <keys_dir>/<our code>.private.pem. Recipients' public keys:
<keys_dir>/<code>.public.pem (key_source "files", local mock) or the NHCX registry (key_source
"nhcx": Fetch Certs, cached, see certs.py).
"""
import base64
import json
from pathlib import Path

from jwcrypto import jwe, jwk
from jwcrypto.common import JWException

from ..config import get_settings

ALG, ENC = "RSA-OAEP-256", "A256GCM"


class JoseError(Exception):
    pass


def _key(code: str, kind: str) -> jwk.JWK:
    if kind == "public" and get_settings().key_source == "nhcx":
        from .certs import CertLookupError, public_key_pem
        try:
            return jwk.JWK.from_pem(public_key_pem(code))
        except CertLookupError as e:
            raise JoseError(str(e)) from e
    path = Path(get_settings().keys_dir) / f"{code}.{kind}.pem"
    if not path.exists():
        raise JoseError(f"no {kind} key for {code!r} in {get_settings().keys_dir}")
    return jwk.JWK.from_pem(path.read_bytes())


def encrypt(headers: dict, fhir: dict) -> str:
    protected = {"alg": ALG, "enc": ENC, **headers}
    token = jwe.JWE(json.dumps(fhir).encode(), recipient=_key(headers["x-hcx-recipient_code"], "public"),
                    protected=json.dumps(protected))
    return token.serialize(compact=True)


def read_protected(token: str) -> dict:
    parts = token.split(".")
    if len(parts) != 5:
        raise JoseError("payload is not a compact JWE")
    try:
        header = json.loads(base64.urlsafe_b64decode(parts[0] + "=" * (-len(parts[0]) % 4)))
    except ValueError as e:
        raise JoseError("unreadable JWE protected header") from e
    if header.get("alg") != ALG or header.get("enc") != ENC:
        raise JoseError(f"unexpected JWE algorithms {header.get('alg')}/{header.get('enc')}")
    return {k: v for k, v in header.items() if k.startswith("x-hcx-")}


def decrypt(token: str, our_code: str) -> dict:
    try:
        t = jwe.JWE()
        t.deserialize(token, key=_key(our_code, "private"))
        return json.loads(t.payload)
    except (JWException, ValueError) as e:
        raise JoseError(f"cannot decrypt payload: {type(e).__name__}") from e
