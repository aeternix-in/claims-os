"""Input checks and masking for M1 (test cases CRT_ABHA_104, VRFY_ABHA_301/401).

Checks run before anything is encrypted or sent, so staff see a clear message at once.
"""
import re

# Verhoeff checksum tables (UIDAI uses Verhoeff for the 12th Aadhaar digit)
_D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
      [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
      [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
      [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
      [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
      [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]


class InvalidInput(ValueError):
    """Bad input from the counter; the message is safe to show as-is."""


def _digits(value: str) -> str:
    return re.sub(r"[\s-]", "", value or "")


def verhoeff_ok(number: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def aadhaar(value: str) -> str:
    v = _digits(value)
    if not re.fullmatch(r"\d{12}", v):
        raise InvalidInput("Aadhaar number must be 12 digits.")
    if v[0] in "01" or not verhoeff_ok(v):
        raise InvalidInput("Aadhaar number is not valid. Please check and re-enter.")
    return v


def mobile(value: str) -> str:
    v = _digits(value)
    if v.startswith("+91"):
        v = v[3:]
    if not re.fullmatch(r"[6-9]\d{9}", v):
        raise InvalidInput("Please enter a valid mobile number.")
    return v


def otp(value: str) -> str:
    v = _digits(value)
    if not re.fullmatch(r"\d{6}", v):
        raise InvalidInput("OTP must be 6 digits.")
    return v


def abha_number(value: str) -> str:
    """Returns the ABHA number in ABDM's display form XX-XXXX-XXXX-XXXX."""
    v = _digits(value)
    if not re.fullmatch(r"\d{14}", v):
        raise InvalidInput("ABHA number must be 14 digits.")
    return f"{v[:2]}-{v[2:6]}-{v[6:10]}-{v[10:]}"


def abha_address(value: str, suffix: str = "sbx") -> str:
    """Full ABHA address (adds @sbx if the user typed only the handle)."""
    v = (value or "").strip().lower()
    if "@" not in v:
        v = f"{v}@{suffix}"
    handle = v.split("@", 1)[0]
    if not re.fullmatch(r"[a-z0-9][a-z0-9._]{2,30}[a-z0-9]", handle):
        raise InvalidInput("ABHA address: 4-32 characters, letters, numbers, '.' or '_', "
                           "not starting or ending with '.' or '_'.")
    return v


def abha_handle(value: str) -> str:
    """The handle only (what enrol/abha-address expects), validated."""
    return abha_address(value).split("@", 1)[0]


def is_abha_number(value: str) -> bool:
    return bool(re.fullmatch(r"\d{14}", _digits(value)))


def mask(value: str | None, keep: int = 4) -> str:
    if not value:
        return ""
    d = _digits(value)
    return "*" * max(len(d) - keep, 0) + d[-keep:]
