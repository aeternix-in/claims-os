"""Aeternix web service.

Two kinds of endpoints:
  /cases/...      used by Aeternix (UI, intake adapters) to create and submit cases
  /hcx/v1/...     called BY NHCX: payer responses and payer-initiated requests
"""
import logging
import random
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Body, FastAPI, Header, Request
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from .. import services, store
from ..config import get_settings
from ..domain.lifecycle import InvalidTransition
from ..domain.models import ClaimCase
from ..nhcx.envelope import hcx_timestamp
from ..nhcx.inbound import InboundError, ack
from ..nhcx.jose import JoseError
from ..samples import sample_preauth

UI_FILE = Path(__file__).parent / "static" / "index.html"

log = logging.getLogger("aeternix.api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    s = get_settings()
    logging.basicConfig(level=s.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    store.init_db(s.database_url)
    yield


app = FastAPI(title="Aeternix Core", version="0.3.0", lifespan=lifespan)

from . import abha_routes  # noqa: E402  (M1: ABHA counter + Scan & Share)

abha_routes.install(app)


def _hcx_error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={
        "timestamp": hcx_timestamp(), "error": {"code": code, "message": message, "trace": None}})


@app.exception_handler(InboundError)
async def inbound_error(_: Request, e: InboundError):
    log.warning("rejected inbound message: %s %s", e.code, e.message)
    return _hcx_error(e.status, e.code, e.message)


@app.exception_handler(JoseError)
async def jose_error(_: Request, e: JoseError):
    # e.g. no public key for the insurer: a configuration problem, nothing was sent
    log.error("encryption error: %s", e)
    return JSONResponse(status_code=422, content={"detail": f"encryption error: {e}"})


@app.exception_handler(services.NotFound)
async def not_found(_: Request, e: services.NotFound):
    return JSONResponse(status_code=404, content={"detail": f"case {e} not found"})


@app.exception_handler(services.Conflict)
async def conflict(_: Request, e: services.Conflict):
    return JSONResponse(status_code=409, content={"detail": str(e)})


@app.exception_handler(InvalidTransition)
async def invalid_transition(_: Request, e: InvalidTransition):
    return JSONResponse(status_code=409, content={"detail": str(e)})


def _require_bearer(bearer_auth: str | None) -> None:
    # Lenient, like the mock: NHCX must send some bearer token. Real verification comes with the sandbox.
    if not bearer_auth or not bearer_auth.startswith("Bearer "):
        raise InboundError(401, "ERR_ACCESS_DENIED", "missing 'bearer_auth' header")


@app.get("/health", tags=["Ops"])
def health():
    return {"status": "ok"}


# ---- Staff UI ----------------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/ui")


@app.get("/ui", include_in_schema=False)
def ui():
    return FileResponse(UI_FILE, media_type="text/html")


@app.get("/ui/config", include_in_schema=False)
def ui_config():
    s = get_settings()
    return {"demo_mode": s.demo_mode, "envelope_mode": s.envelope_mode}


@app.get("/ui/sample", tags=["Cases"], summary="A filled-in example case for the form (dummy data)")
def ui_sample():
    return sample_preauth(case_id=f"AETX-{uuid.uuid4().hex[:6].upper()}").model_dump(mode="json")


# ---- Aeternix-facing --------------------------------------------------------------------------

@app.get("/cases", tags=["Cases"])
def list_cases():
    return services.list_cases()


@app.post("/cases", status_code=201, tags=["Cases"])
def create_case(case: ClaimCase):
    row = services.create_case(case)
    return {"case_id": row.case_id, "status": row.status}


@app.get("/cases/{case_id}", tags=["Cases"])
def get_case(case_id: str):
    return services.get_case(case_id)


@app.post("/cases/{case_id}/submit/{workflow}", tags=["Cases"],
          summary="Send a pre-auth or claim to NHCX (workflow: preauth | claim)")
async def submit_case(case_id: str, workflow: str, scenario: str | None = None):
    """`scenario` (approve | partial | reject | query | timeout) picks the MOCK insurer's decision.
    Honoured only in demo mode; ignored otherwise."""
    if workflow not in ("preauth", "claim"):
        return JSONResponse(status_code=400, content={"detail": "workflow must be 'preauth' or 'claim'"})
    exchange = await services.submit(case_id, workflow,
                                     scenario=scenario if get_settings().demo_mode else None)
    return {"exchange": exchange, "case": services.get_case(case_id)["status"]}


class QueryAttachment(BaseModel):
    title: str | None = None
    content_type: str = Field(pattern=r"^(application/pdf|image/jpeg|image/jpg|image/png|application/fhir\+json)$")
    data: str = Field(min_length=1)       # base64


class QueryReply(BaseModel):
    message: str = Field(min_length=1)
    attachments: list[QueryAttachment] = Field(default_factory=list)


@app.post("/cases/{case_id}/query-reply", tags=["Cases"],
          summary="Answer the insurer's document query (NHCX communication/on_request)")
async def reply_query(case_id: str, reply: QueryReply):
    exchange = await services.reply_to_query(case_id, reply.message,
                                             [a.model_dump() for a in reply.attachments])
    return {"exchange": exchange, "case": services.get_case(case_id)["status"]}


@app.get("/cases/{case_id}/messages/{message_id}", tags=["Cases"],
         summary="One message exchanged with NHCX: wire form, readable form, interpretation")
def get_message(case_id: str, message_id: int):
    return services.get_exchange(case_id, message_id)


# ---- Demo mode only ---------------------------------------------------------------------------

DEMO_PATIENTS = ["Meera Iyer", "Rohan Das", "Fatima Sheikh", "Arjun Mehta", "Kavya Reddy",
                 "Sanjay Kulkarni", "Ananya Bose", "Vikram Singh", "Priya Nair", "Imran Qureshi"]


def _require_demo():
    if not get_settings().demo_mode:
        raise services.NotFound("demo mode is off")


@app.post("/demo/case", status_code=201, tags=["Demo"], summary="Create a ready-made demo case")
def demo_case():
    _require_demo()
    case = sample_preauth(case_id=f"DEMO-{uuid.uuid4().hex[:5].upper()}")
    case.patient.name = random.choice(DEMO_PATIENTS)
    case.hospital_reference = f"IP-{random.randint(100000, 999999)}"
    row = services.create_case(case)
    return {"case_id": row.case_id, "status": row.status}


@app.post("/demo/cases/{case_id}/pay", tags=["Demo"], summary="Make the mock insurer pay this case's claim")
async def demo_pay(case_id: str):
    _require_demo()
    return await services.demo_pay(case_id)


# ---- NHCX-facing (called by the gateway) ------------------------------------------------------

def _make_on_submit(workflow: str):
    async def on_submit(body: dict[str, Any] = Body(...),
                        bearer_auth: str | None = Header(default=None, alias="bearer_auth",
                                                         convert_underscores=False)):
        _require_bearer(bearer_auth)
        headers, duplicate = services.handle_claim_response(workflow, body)
        if duplicate:
            log.info("duplicate callback ignored api_call_id=%s", headers["x-hcx-api_call_id"])
        return JSONResponse(status_code=202, content=ack(headers))
    on_submit.__name__ = f"{workflow}_on_submit"
    return on_submit


for _wf in ("preauth", "claim"):
    app.add_api_route(f"/hcx/v1/{_wf}/on_submit", _make_on_submit(_wf), methods=["POST"],
                      status_code=202, tags=["NHCX callbacks"],
                      summary=f"Payer's response to our {_wf}/submit")


@app.post("/hcx/v1/communication/request", status_code=202, tags=["NHCX callbacks"],
          summary="Insurer asks for more information (document query, workflow 24 / 27)")
async def communication_request(body: dict[str, Any] = Body(...),
                                bearer_auth: str | None = Header(default=None, alias="bearer_auth",
                                                                 convert_underscores=False)):
    _require_bearer(bearer_auth)
    headers, duplicate = services.handle_communication_request(body)
    if duplicate:
        log.info("duplicate communication request ignored api_call_id=%s", headers["x-hcx-api_call_id"])
    return JSONResponse(status_code=202, content=ack(headers))


@app.post("/hcx/v1/paymentnotice/request", status_code=202, tags=["NHCX callbacks"],
          summary="Payer notifies payment; we acknowledge via paymentnotice/on_request")
async def paymentnotice_request(background: BackgroundTasks, body: dict[str, Any] = Body(...),
                                bearer_auth: str | None = Header(default=None, alias="bearer_auth",
                                                                 convert_underscores=False)):
    _require_bearer(bearer_auth)
    headers, pending_ack = services.handle_payment_notice(body)
    if pending_ack:
        ack_body, ack_api_call_id = pending_ack
        background.add_task(services.send_payment_ack, ack_body, ack_api_call_id)
    return JSONResponse(status_code=202, content=ack(headers))
