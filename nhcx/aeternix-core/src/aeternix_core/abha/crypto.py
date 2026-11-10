"""ABDM public key and encryption of Aadhaar / mobile / OTP / ABHA number / index.

ABDM: GET {abha}/v3/profile/public/certificate -> {"publicKey": <base64 SPKI>,
"encryptionAlgorithm": "RSA/ECB/OAEPWithSHA-1AndMGF1Padding"}.
The key rotates (about every 3 months), so it is fetched and cached, never hard-coded;
`forget()` drops it so the next call refetches (used after an encryption-related rejection).
"""
import base64
import threading
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from ..config import get_settings

_lock = threading.Lock()
_key: rsa.RSAPublicKey | None = None
_fetched_at = 0.0

EXPECTED_ALGORITHM = "RSA/ECB/OAEPWithSHA-1AndMGF1Padding"


class KeyError_(Exception):
    pass


def _load(b64: str) -> rsa.RSAPublicKey:
    raw = "".join(b64.split())
    if raw.startswith("-----BEGIN"):
        key = serialization.load_pem_public_key(raw.encode())
    else:
        key = serialization.load_der_public_key(base64.b64decode(raw))
    if not isinstance(key, rsa.RSAPublicKey):
        raise KeyError_("ABDM public key is not an RSA key")
    return key


def public_key() -> rsa.RSAPublicKey:
    global _key, _fetched_at
    from . import client  # late import: client imports crypto

    ttl = get_settings().abha_key_cache_hours * 3600
    with _lock:
        if _key is None or time.time() - _fetched_at > ttl:
            data = client.get_json("/v3/profile/public/certificate")
            pk = data.get("publicKey")
            if not pk:
                raise KeyError_("ABDM certificate reply has no publicKey")
            algo = data.get("encryptionAlgorithm")
            if algo and algo != EXPECTED_ALGORITHM:
                raise KeyError_(f"ABDM now asks for {algo}; Aeternix implements {EXPECTED_ALGORITHM}")
            _key, _fetched_at = _load(pk), time.time()
        return _key


def encrypt(value: str) -> str:
    """RSA/ECB/OAEPWithSHA-1AndMGF1Padding, standard base64, no newlines."""
    ct = public_key().encrypt(value.encode("utf-8"), padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA1()), algorithm=hashes.SHA1(), label=None))
    return base64.b64encode(ct).decode("ascii")


def forget() -> None:
    global _key, _fetched_at
    with _lock:
        _key, _fetched_at = None, 0.0
