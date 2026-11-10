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
    return jwt.PyJWKClient(f"{get_settings().supabase_url}/auth/v1/.well-known/jwks.json")


def _decode(token: str) -> dict:
    s = get_settings()
    opts = dict(audience="authenticated")
    if s.supabase_jwt_secret:
        return jwt.decode(token, s.supabase_jwt_secret, algorithms=["HS256"], **opts)
    key = _jwks().get_signing_key_from_jwt(token).key
    return jwt.decode(token, key, algorithms=["ES256", "RS256"], **opts)


def current_user(cred: HTTPAuthorizationCredentials = Depends(bearer)) -> User:
    try:
        claims = _decode(cred.credentials)
    except jwt.PyJWTError as e:
        raise HTTPException(401, f"Invalid token: {e}")
    with pool().connection() as c:
        row = c.execute("select hospital_id, role, full_name from profiles where user_id = %s",
                        (claims["sub"],)).fetchone()
    if not row:
        raise HTTPException(403, "No profile: ask an admin to add you to a hospital")
    return User(claims["sub"], str(row["hospital_id"]), row["role"], row["full_name"])
