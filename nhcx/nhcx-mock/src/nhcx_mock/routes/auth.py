from fastapi import Header

from ..errors import HcxError


def require_bearer_auth(
    bearer_auth: str | None = Header(default=None, alias="bearer_auth", convert_underscores=False,
                                     description="Sandbox uses a header literally named 'bearer_auth': 'Bearer <token>'"),
) -> str:
    """Lenient auth: any non-empty 'Bearer <token>' passes, except the token 'invalid'."""
    if not bearer_auth:
        raise HcxError(401, "ERR_ACCESS_DENIED", "Missing 'bearer_auth' header (note: not 'Authorization')")
    scheme, _, token = bearer_auth.partition(" ")
    if scheme != "Bearer" or not token.strip():
        raise HcxError(401, "ERR_ACCESS_DENIED", "bearer_auth must be 'Bearer <token>'")
    if token.strip() == "invalid":
        raise HcxError(401, "ERR_ACCESS_DENIED", "Token rejected (simulated)")
    return token.strip()
