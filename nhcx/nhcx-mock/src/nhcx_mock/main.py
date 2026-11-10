import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from . import db
from .config import get_settings
from .errors import HcxError, hcx_error_handler, validation_error_handler
from .routes import hcx, mock_admin, receiver


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    db.init_db(settings.database_url)
    yield


app = FastAPI(
    title="NHCX Local Mock",
    version="0.1.0",
    description="Behavioural test double for the NHCX sandbox (plain-mode envelopes, mock payer, async callbacks).",
    lifespan=lifespan,
)
app.add_exception_handler(HcxError, hcx_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.include_router(hcx.router)
app.include_router(mock_admin.router)
app.include_router(receiver.router)


@app.get("/health", tags=["Ops"])
def health():
    return {"status": "ok"}
