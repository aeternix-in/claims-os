"""Read the RSA public key from an X.509 certificate without validating its name fields.

Why: NHCX sandbox certificates contain subject/issuer attributes encoded in a way the ASN.1
standard does not allow. The `cryptography` library (and jwcrypto, built on it) refuses the
whole certificate ("error parsing asn1 value ... AttributeTypeValue::value"), even though the
public key inside is fine. This walks the DER structure and extracts only:
    Certificate ::= SEQUENCE { tbsCertificate, signatureAlgorithm, signature }
    tbsCertificate ::= SEQUENCE { [0] version OPTIONAL, serialNumber, signature, issuer,
                                  validity, subject, subjectPublicKeyInfo, ... }
The key is still loaded by `cryptography`, so a malformed KEY is still refused.
"""
import base64
import re
from datetime import datetime, timezone

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_der_public_key


class CertError(ValueError):
    pass


def _tlv(data: bytes, pos: int) -> tuple[int, int, int, int]:
    """Read one DER element at pos -> (tag, content_start, content_end, element_end)."""
    if pos + 2 > len(data):
        raise CertError("truncated DER")
    tag, length = data[pos], data[pos + 1]
    pos += 2
    if length & 0x80:
        n = length & 0x7F
        if n == 0 or n > 4 or pos + n > len(data):
            raise CertError("bad DER length")
        length = int.from_bytes(data[pos:pos + n], "big")
        pos += n
    end = pos + length
    if end > len(data):
        raise CertError("DER element runs past the end")
    return tag, pos, end, end


def _children(data: bytes, start: int, end: int) -> list[tuple[int, int, int, int]]:
    out, pos = [], start
    while pos < end:
        tag, cs, ce, nxt = _tlv(data, pos)
        out.append((tag, pos, cs, ce))     # (tag, element_start, content_start, content_end)
        pos = nxt
    return out


def pem_to_der(text: str) -> bytes:
    text = text.replace("\\r", "").replace("\\n", "\n")
    m = re.search(r"-----BEGIN CERTIFICATE-----(.*?)-----END CERTIFICATE-----", text, re.S)
    body = m.group(1) if m else text
    try:
        return base64.b64decode(re.sub(r"[^A-Za-z0-9+/=]", "", body))
    except ValueError as e:
        raise CertError("certificate is not valid base64") from e


def _time(data: bytes, tag: int, cs: int, ce: int) -> datetime | None:
    s = data[cs:ce].decode("ascii", "ignore").rstrip("Z")
    try:
        if tag == 0x17:   # UTCTime YYMMDDHHMMSS
            return datetime.strptime(s, "%y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        if tag == 0x18:   # GeneralizedTime YYYYMMDDHHMMSS
            return datetime.strptime(s[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return None


def read_certificate(text: str) -> dict:
    """Return {"public_key", "not_before", "not_after"} from a PEM (or base64 DER) certificate."""
    der = pem_to_der(text)
    tag, cs, ce, _ = _tlv(der, 0)
    if tag != 0x30:
        raise CertError("not a certificate (outer SEQUENCE missing)")
    top = _children(der, cs, ce)
    if not top or top[0][0] != 0x30:
        raise CertError("tbsCertificate missing")
    _, _, tcs, tce = top[0]
    fields = _children(der, tcs, tce)
    if fields and fields[0][0] == 0xA0:          # explicit [0] version
        fields = fields[1:]
    if len(fields) < 6:
        raise CertError("tbsCertificate has too few fields")
    # fields: serial, signature alg, issuer, validity, subject, subjectPublicKeyInfo
    validity, spki = fields[3], fields[5]
    if spki[0] != 0x30:
        raise CertError("subjectPublicKeyInfo missing")
    key = load_der_public_key(der[spki[1]:spki[3]])
    times = _children(der, validity[2], validity[3])
    nb = _time(der, times[0][0], times[0][2], times[0][3]) if len(times) > 0 else None
    na = _time(der, times[1][0], times[1][2], times[1][3]) if len(times) > 1 else None
    return {"public_key": key, "not_before": nb, "not_after": na,
            "rsa_bits": key.key_size if isinstance(key, rsa.RSAPublicKey) else None}
