from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import state_machine as sm
from .auth import User, current_user
from .bank import router as bank_router
from .billing import router as billing_router
from .cases import router as cases_router
from .checks import router as checks_router
from .documents import router as documents_router
from .letters import router as letters_router
from .nhcx import gateway as nhcx_gateway
from .nhcx import router as nhcx_router
from .querydesk import router as queries_router
from .reports import router as reports_router
from .setup import router as setup_router
from .ui_store import router as ui_router
from .ui_files import router as ui_files_router
from .config import get_settings

app = FastAPI(title="Aeternix Claims Engine", version="0.1.0")
app.add_middleware(CORSMiddleware,
                   allow_origins=[o.strip() for o in get_settings().cors_origins.split(",")],
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(cases_router)
app.include_router(billing_router)
app.include_router(documents_router)
app.include_router(checks_router)
app.include_router(queries_router)
app.include_router(letters_router)
app.include_router(bank_router)
app.include_router(reports_router)
app.include_router(nhcx_router)
app.include_router(setup_router)
app.include_router(ui_router)
app.include_router(ui_files_router)
app.include_router(nhcx_gateway)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/me")
def me(user: User = Depends(current_user)):
    return user


@app.get("/meta/stages")
def stages():
    return {"stages": sm.STAGES,
            "transitions": [{"event": t.event, "from": sorted(t.sources), "to": t.target,
                             "roles": sorted(t.roles)} for t in sm.TRANSITIONS]}
