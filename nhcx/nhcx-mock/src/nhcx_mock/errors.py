from datetime import datetime, timezone

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class HcxError(Exception):
    """Protocol/business error returned synchronously to the caller.

    ASSUMPTION: error body shape follows the HCX spec (timestamp + error{code,message,trace});
    the NHA Postman collections contain no saved responses, so verify against the real sandbox.
    """

    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


def error_body(code: str, message: str, api_call_id=None, correlation_id=None) -> dict:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "api_call_id": api_call_id,
        "correlation_id": correlation_id,
        "error": {"code": code, "message": message, "trace": None},
    }


async def hcx_error_handler(_: Request, exc: HcxError):
    return JSONResponse(status_code=exc.status, content=error_body(exc.code, exc.message))


async def validation_error_handler(_: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content=error_body("ERR_INVALID_PAYLOAD", str(exc.errors())))
