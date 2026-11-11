"""Storage for live mode of the CTO's UI.

The UI keeps its own claims model (the approved demo's logic). The engine stores it per claim,
scoped to the user's hospital, with:
  - optimistic versions: a save based on an old version is refused (409), never overwrites;
  - append-only history: a save that drops or rewrites an earlier claim event is refused;
  - an audit row per save (who, role, events added).
"""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from .auth import User, current_user
from .db import tx

router = APIRouter(prefix="/ui", tags=["live UI"])
WRITE_ROLES = {"desk", "doctor", "accounts", "owner", "admin"}
# Hospital-wide lists and money records a treating doctor may read but not change.
READONLY_MASTERS_ROLES = {"doctor"}
MASTER_KEYS = ("hospital", "insurers", "tpas", "doctors", "procedures", "rules", "procedureRates", "bank")
MAX_DOC_BYTES = 2 * 1024 * 1024


def _dumps(o):
    import json
    return json.dumps(o, separators=(",", ":"), default=str)


@router.get("/state")
def get_state(user: User = Depends(current_user)):
    with tx() as c:
        st = c.execute("select doc, version from ui_state where hospital_id=%s", (user.hospital_id,)).fetchone()
        claims = c.execute("select id, doc, version from ui_claims where hospital_id=%s", (user.hospital_id,)).fetchall()
        h = c.execute("select name, city, beds, settings from hospitals where id=%s", (user.hospital_id,)).fetchone()
        payers = c.execute("select id, name, kind, via_tpa, nhcx_participant_code from payers where hospital_id=%s order by name",
                           (user.hospital_id,)).fetchall()
    return {"state": st["doc"] if st else None, "version": st["version"] if st else 0,
            "claims": claims, "hospital": h, "payers": payers,
            "me": {"id": user.id, "role": user.role, "name": user.full_name},
            "server_time": datetime.now(timezone.utc).isoformat()}


@router.get("/changes")
def changes(since: str, user: User = Depends(current_user)):
    """What other users saved after `since` (ISO time): for keeping open screens in step."""
    with tx() as c:
        claims = c.execute("select id, doc, version, updated_at from ui_claims where hospital_id=%s and updated_at > %s",
                           (user.hospital_id, since)).fetchall()
        st = c.execute("select doc, version, updated_at from ui_state where hospital_id=%s and updated_at > %s",
                       (user.hospital_id, since)).fetchone()
        now = c.execute("select now() as t").fetchone()["t"]
    return {"claims": claims, "state": st, "server_time": now.isoformat()}


class StateIn(BaseModel):
    doc: dict
    base_version: int = Field(ge=0)


@router.put("/state")
def put_state(body: StateIn, user: User = Depends(current_user)):
    if user.role not in WRITE_ROLES:
        raise HTTPException(403, "Your role cannot change hospital data")
    body.doc.pop("claims", None)
    body.doc.pop("order", None)
    if len(_dumps(body.doc)) > MAX_DOC_BYTES:
        raise HTTPException(413, "Hospital document too large")
    with tx() as c:
        cur = c.execute("select version from ui_state where hospital_id=%s for update", (user.hospital_id,)).fetchone()
        have = cur["version"] if cur else 0
        if body.base_version != have:
            raise HTTPException(409, {"code": "stale", "message": "Someone else changed this; reloading", "version": have})
        if user.role in READONLY_MASTERS_ROLES and cur:
            old = c.execute("select doc from ui_state where hospital_id=%s", (user.hospital_id,)).fetchone()["doc"]
            touched = [k for k in MASTER_KEYS if json.dumps(old.get(k), sort_keys=True) != json.dumps(body.doc.get(k), sort_keys=True)]
            if touched:
                names = {"hospital": "details", "insurers": "payers", "tpas": "TPAs", "doctors": "doctors", "procedures": "procedures",
                         "rules": "payer rules", "procedureRates": "tariff", "bank": "bank credits"}
                raise HTTPException(403, f"A {user.role} cannot change the hospital's {', '.join(names[k] for k in touched)}")
        if cur:
            r = c.execute("""update ui_state set doc=%s, version=version+1, updated_at=now(), updated_by=%s
                             where hospital_id=%s returning version""",
                          (Jsonb(body.doc, dumps=_dumps), user.id, user.hospital_id)).fetchone()
        else:
            r = c.execute("""insert into ui_state (hospital_id, doc, updated_by) values (%s,%s,%s) returning version""",
                          (user.hospital_id, Jsonb(body.doc, dumps=_dumps), user.id)).fetchone()
    return {"version": r["version"]}


class ClaimIn(BaseModel):
    doc: dict
    base_version: int = Field(ge=0)


@router.put("/claims/{claim_id}")
def put_claim(claim_id: str, body: ClaimIn, user: User = Depends(current_user)):
    if user.role not in WRITE_ROLES:
        raise HTTPException(403, "Your role cannot change claims")
    if body.doc.get("id") != claim_id:
        raise HTTPException(422, "Claim id does not match")
    if len(_dumps(body.doc)) > MAX_DOC_BYTES:
        raise HTTPException(413, "Claim document too large")
    new_events = body.doc.get("events") or []
    with tx() as c:
        cur = c.execute("select doc, version from ui_claims where hospital_id=%s and id=%s for update",
                        (user.hospital_id, claim_id)).fetchone()
        have = cur["version"] if cur else 0
        if body.base_version != have:
            raise HTTPException(409, {"code": "stale", "message": "Someone else changed this claim; reloading", "version": have})
        old_events = (cur["doc"].get("events") or []) if cur else []
        # history is append-only: every earlier event must still be there, unchanged
        if len(new_events) < len(old_events) or any(
                (a.get("id"), a.get("at"), a.get("type"), a.get("text")) != (b.get("id"), b.get("at"), b.get("type"), b.get("text"))
                for a, b in zip(old_events, new_events)):
            raise HTTPException(422, {"code": "history_changed", "message": "A claim's earlier events cannot be changed or removed"})
        added = new_events[len(old_events):]
        doc = Jsonb(body.doc, dumps=_dumps)
        if cur:
            r = c.execute("""update ui_claims set doc=%s, version=version+1, stage=%s, updated_at=now(), updated_by=%s
                             where hospital_id=%s and id=%s returning version""",
                          (doc, body.doc.get("stage"), user.id, user.hospital_id, claim_id)).fetchone()
        else:
            r = c.execute("""insert into ui_claims (hospital_id, id, doc, stage, updated_by) values (%s,%s,%s,%s,%s)
                             returning version""", (user.hospital_id, claim_id, doc, body.doc.get("stage"), user.id)).fetchone()
        c.execute("""insert into ui_claim_log (hospital_id, claim_id, version, actor_id, actor_role, events_added)
                     values (%s,%s,%s,%s,%s,%s)""",
                  (user.hospital_id, claim_id, r["version"], user.id, user.role, Jsonb(added, dumps=_dumps)))
    return {"version": r["version"]}
