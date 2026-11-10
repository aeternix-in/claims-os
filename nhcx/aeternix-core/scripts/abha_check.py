"""Safe ABHA sandbox check: no Aadhaar, no OTP, no patient data.

  uv run python scripts/abha_check.py

1. logs in to the ABDM gateway with your .env client credentials (session token)
2. fetches ABDM's encryption public key from the ABHA v3 API
3. encrypts a dummy string with it (proves the RSA-OAEP/SHA-1 setup works)

If all three print OK, the ABHA desk (/ui/abha) is ready for real OTP testing.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aeternix_core.abha import crypto  # noqa: E402
from aeternix_core.abha.client import AbhaError  # noqa: E402
from aeternix_core.config import get_settings  # noqa: E402
from aeternix_core.nhcx import session  # noqa: E402


def main() -> int:
    s = get_settings()
    print(f"ABHA API : {s.abha_base_url}")
    try:
        session.token()
        print("1. Gateway session ............ OK")
    except session.SessionError as e:
        print(f"1. Gateway session ............ FAILED: {e}")
        return 1
    try:
        key = crypto.public_key()
        print(f"2. ABDM public key ............ OK (RSA {key.key_size} bit)")
        out = crypto.encrypt("aeternix-check")
        print(f"3. Encryption ................. OK ({len(out)} base64 chars)")
    except (AbhaError, crypto.KeyError_) as e:
        print(f"2/3. Public key / encryption .. FAILED: {e}")
        return 1
    print(f"HIP ID   : {s.hip_id or 'not set yet (needed only for Scan & Share)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
