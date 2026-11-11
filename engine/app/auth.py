"""Verifies the Supabase access token the browser sends, then loads the
caller's hospital and role. Every route scopes its queries by `user.hospital_id`."""
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings
from .db import pool

bearer = HTTPBearer()


@dataclass
class User:
    id: str
    hospital_id: str
    role: str
    full_name: str


@lru_cache
def _jwks() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{get_settings().supabase_url.strip().rstrip('/')}/auth/v1/.well-known/jwks.json")


def _decode(token: str) -> dict:
    """Supabase signs tokens either with the project's shared JWT secret (HS256, older projects) or with
    its signing keys (ES256/RS256, the default for newer projects). Go by what the token says it is,
    so the engine works whether or not SUPABASE_JWT_SECRET is set."""
    s = get_settings()
    opts = dict(audience="authenticated")
    alg = jwt.get_unverified_header(token).get("alg")
    if alg == "HS256":
        if not s.supabase_jwt_secret:
            raise jwt.InvalidTokenError("token is HS256 but SUPABASE_JWT_SECRET is not set on the engine")
        return jwt.decode(token, s.supabase_jwt_secret.strip(), algorithms=["HS256"], **opts)
    if alg not in ("ES256", "RS256"):
        raise jwt.InvalidTokenError(f"unsupported token algorithm {alg}")
    if not s.supabase_url:
        raise jwt.InvalidTokenError("SUPABASE_URL is not set on the engine")
    key = _jwks().get_signing_key_from_jwt(token).key
    return jwt.decode(token, key, algorithms=[alg], **opts)


def current_user(cred: HTTPAuthorizationCredentials = Depends(bearer)) -> User:
    try:
        claims = _decode(cred.credentials)
    except jwt.PyJWTError as e:
        print(f"auth: rejected token: {e}", flush=True)  # shows in the host's logs (Render → Logs)
        raise HTTPException(401, f"Invalid token: {e}")
    with pool().connection() as c:
        row = c.execute("select hospital_id, role, full_name from profiles where user_id = %s",
                        (claims["sub"],)).fetchone()
    if not row:
        raise HTTPException(403, "No profile: ask an admin to add you to a hospital")
    return User(claims["sub"], str(row["hospital_id"]), row["role"], row["full_name"])
