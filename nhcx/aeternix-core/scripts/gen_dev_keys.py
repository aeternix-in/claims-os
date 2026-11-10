"""Generate DUMMY RSA keypairs for local JWE testing. Never use these anywhere real.

    uv run python scripts/gen_dev_keys.py [keys_dir] [participant_code ...]

Defaults: keys_dir=../dev-keys, participants aeternix-provider@hcx and mock-payer@hcx.
Writes <code>.private.pem and <code>.public.pem; existing keys are left untouched.
"""
import sys
from pathlib import Path

from jwcrypto import jwk

keys_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "../dev-keys")
codes = sys.argv[2:] or ["aeternix-provider@hcx", "mock-payer@hcx"]
keys_dir.mkdir(parents=True, exist_ok=True)

for code in codes:
    priv, pub = keys_dir / f"{code}.private.pem", keys_dir / f"{code}.public.pem"
    if priv.exists():
        print(f"exists, skipped: {priv}")
        continue
    key = jwk.JWK.generate(kty="RSA", size=2048)
    priv.write_bytes(key.export_to_pem(private_key=True, password=None))
    priv.chmod(0o600)
    pub.write_bytes(key.export_to_pem())
    print(f"created: {priv} + {pub.name}")
