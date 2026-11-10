"""Insurers' public encryption keys from the NHCX registry (Fetch Certs), cached.

NHCX sandbox certificates contain name fields the strict `cryptography` parser refuses, so the
key is extracted with the lenient reader (x509_lenient). Verified 2026-10-13 on a real sandbox
certificate (participant 1000004816@hcx): RSA 2048, valid 2026-08-25 to 2027-08-25.
Reply fields seen: encryption_cert, participant_name, participant_id.
"""
import logging
import threading
import time
from datetime import datetime, timezone

import httpx
from cryptography.hazmat.primitives import serialization

from ..config import get_settings
from . import session
from .x509_lenient import CertError, read_certificate

log = logging.getLogger("aeternix.certs")

# Tests replace this with httpx.MockTransport(...).
transport: httpx.BaseTransport | None = None

_lock = threading.Lock()
_cache: dict[str, tuple[bytes, float]] = {}     # participant code -> (public key PEM, cached until)


class CertLookupError(Exception):
    pass


def _fetch(code: str) -> tuple[bytes, datetime | None]:
    s = get_settings()
    url = s.registry_base_url.rstrip("/") + "/fetch/certs"

    def call():
        with httpx.Client(transport=transport, timeout=s.nhcx_timeout_seconds) as c:
            return c.post(url, json={"participantid": code},
                          headers={"Accept": "application/json", "Content-Type": "application/json",
                                   "bearer_auth": session.bearer()})
    try:
        r = call()
        if r.status_code == 401 and s.auth_mode == "session":   # token expired early: refresh once
            session.invalidate()
            r = call()
    except httpx.HTTPError as e:
        raise CertLookupError(f"fetch certs for {code} failed: {type(e).__name__}") from e
    if r.status_code != 200:
        raise CertLookupError(f"fetch certs for {code} refused: HTTP {r.status_code} {r.text[:200]}")
    cert = (r.json() or {}).get("encryption_cert")
    if not cert:
        raise CertLookupError(f"no encryption_cert for {code}")
    try:
        info = read_certificate(cert)
    except (CertError, ValueError) as e:
        raise CertLookupError(f"unreadable certificate for {code}: {e}") from e
    if info["rsa_bits"] is None:
        raise CertLookupError(f"certificate for {code} is not an RSA key")
    if info["not_after"] and info["not_after"] < datetime.now(timezone.utc):
        raise CertLookupError(f"certificate for {code} expired on {info['not_after']:%Y-%m-%d}")
    pem = info["public_key"].public_bytes(serialization.Encoding.PEM,
                                          serialization.PublicFormat.SubjectPublicKeyInfo)
    return pem, info["not_after"]


def public_key_pem(code: str) -> bytes:
    """The participant's public key (PEM), from cache or NHCX."""
    with _lock:
        hit = _cache.get(code)
        if hit and time.time() < hit[1]:
            return hit[0]
    pem, not_after = _fetch(code)
    until = time.time() + get_settings().cert_cache_hours * 3600
    if not_after:
        until = min(until, not_after.timestamp())
    with _lock:
        _cache[code] = (pem, until)
    log.info("fetched NHCX certificate for %s", code)
    return pem


def clear_cache() -> None:
    with _lock:
        _cache.clear()
