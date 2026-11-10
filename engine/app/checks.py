"""Run the pre-flight scrubber, clear ambers, and gate submissions."""
from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import adjudicate as adj
from . import scrubber
from .auth import User, current_user
from .billing import _context, _f
from .db import tx

router = APIRouter(tags=["checks"])


def stage_for(case_stage: str) -> str:
    if case_stage in ("admitted", "preauth", "preauth_pending", "preauth_query", "denied"):
        return "preauth"
    if case_stage in ("in_treatment", "discharge", "discharge_pending", "discharge_query"):
        return "discharge"
    return "claim"


def run(c, case_id: str, user: User, stage: str, trigger: str = "manual") -> dict:
    row, policy, rules, hospital = _context(c, case_id, user.hospital_id)
    lines = c.execute("select * from bill_lines where case_id=%s order by position", (case_id,)).fetchall()
    audit = None
    if lines:
        bill = [adj.BillLine(l["category"], l["description"], _f(l["qty"]), _f(l["rate"]), _f(l["mou_rate"]), l["to_payer"])
                for l in lines]
        audit = adj.adjudicate(bill, policy, rules, hospital, row["procedure_id"], row["room_category"],
                               _f(row["approved_amount"]) or 0)
        audit["_categories"] = [l["category"] for l in lines]
        audit["_icu_days"] = sum(_f(l["qty"]) for l in lines if l["category"] == "icu")
    docs = c.execute("""select d.id, d.doc_type, d.filename, coalesce(string_agg(p.text, E'\\n' order by p.page), '') as text
                        from documents d left join document_pages p on p.document_id=d.id
                        where d.case_id=%s group by d.id""", (case_id,)).fetchall()
    fields = c.execute("select id, document_id, name, value, page, confirmed, snippet from extracted_fields where case_id=%s",
                       (case_id,)).fetchall()
    payer = c.execute("select kind, rules from payers where id=%s", (row["payer_id"],)).fetchone()
    patient = c.execute("select full_name from patients where id=%s", (row["patient_id"],)).fetchone()
    results = scrubber.run_checks(stage, row, payer["kind"] if payer else "insurer", docs, fields, audit,
                                  (payer or {}).get("rules") or {}, patient["full_name"])
    clear = {r["rule_id"]: r for r in c.execute(
        "select rule_id, note, cleared_name, cleared_role, created_at from check_clearances where case_id=%s and stage=%s",
        (case_id, stage)).fetchall()}
    summary = scrubber.summarise(results, clear)
    summary["stage"] = stage
    for r in summary["results"]:
        for k in ("id",):
            r.pop(k, None)
    c.execute("insert into check_runs (hospital_id, case_id, stage, status, summary, run_by, trigger) values (%s,%s,%s,%s,%s,%s,%s)",
              (user.hospital_id, case_id, stage, summary["status"], Jsonb(summary, dumps=_dumps), user.id, trigger))
    return summary


def _dumps(o):
    import json
    return json.dumps(o, default=str)


def gate(case_id: str, user: User, event: str) -> None:
    """Called by apply_event before a submission. Red, or an uncleared amber, blocks it.
    The check run is committed first, so a refused attempt is still on record."""
    stage = scrubber.STAGE_FOR_EVENT.get(event)
    if not stage:
        return
    with tx() as c:
        s = run(c, case_id, user, stage, trigger="gate")
    if s["blocked"]:
        raise HTTPException(422, {"code": "preflight_blocked",
                                  "message": f"Pre-flight check: {s['red']} red, {s['amber_open']} amber to clear before submitting",
                                  "summary": s})


@router.post("/cases/{case_id}/checks")
def run_check(case_id: str, stage: str | None = None, user: User = Depends(current_user)):
    with tx() as c:
        st = c.execute("select stage from cases where id=%s and hospital_id=%s", (case_id, user.hospital_id)).fetchone()
        if not st:
            raise HTTPException(404, "Case not found")
        stage = stage or stage_for(st["stage"])
        if stage not in ("preauth", "discharge", "claim"):
            raise HTTPException(422, "stage must be preauth, discharge or claim")
        return run(c, case_id, user, stage)


@router.get("/cases/{case_id}/checks/latest")
def latest(case_id: str, stage: str | None = None, user: User = Depends(current_user)):
    with tx() as c:
        st = c.execute("select stage from cases where id=%s and hospital_id=%s", (case_id, user.hospital_id)).fetchone()
        if not st:
            raise HTTPException(404, "Case not found")
        stage = stage or stage_for(st["stage"])
        r = c.execute("select summary, created_at from check_runs where case_id=%s and stage=%s order by id desc limit 1",
                      (case_id, stage)).fetchone()
    return {"stage": stage, "summary": r["summary"] if r else None, "at": r["created_at"] if r else None}


class ClearIn(BaseModel):
    stage: str
    note: str = Field(min_length=5)


@router.post("/cases/{case_id}/checks/{rule_id}/clear")
def clear(case_id: str, rule_id: str, body: ClearIn, user: User = Depends(current_user)):
    with tx() as c:
        last = c.execute("select summary from check_runs where case_id=%s and stage=%s and hospital_id=%s order by id desc limit 1",
                         (case_id, body.stage, user.hospital_id)).fetchone()
        if not last:
            raise HTTPException(409, "Run the check first")
        r = next((x for x in last["summary"]["results"] if x["rule_id"] == rule_id), None)
        if not r or r["level"] != "amber":
            raise HTTPException(422, {"code": "not_amber", "message": "Only amber items can be cleared; red must be fixed"})
        if r["clinical"] and user.role not in ("doctor", "admin"):
            raise HTTPException(403, {"code": "clinician_gate", "message": "A clinical item needs the treating doctor to clear it"})
        c.execute("""insert into check_clearances (hospital_id, case_id, stage, rule_id, note, cleared_by, cleared_name, cleared_role)
                     values (%s,%s,%s,%s,%s,%s,%s,%s)
                     on conflict (case_id, stage, rule_id) do update set note=excluded.note, cleared_by=excluded.cleared_by,
                       cleared_name=excluded.cleared_name, cleared_role=excluded.cleared_role, created_at=now()""",
                  (user.hospital_id, case_id, body.stage, rule_id, body.note.strip(), user.id, user.full_name, user.role))
        cs = c.execute("select id, stage from cases where id=%s", (case_id,)).fetchone()
        c.execute("""insert into case_events (hospital_id, case_id, event, from_stage, to_stage, actor_id, actor_role, payload, note)
                     values (%s,%s,'check_cleared',%s,%s,%s,%s,%s,%s)""",
                  (user.hospital_id, case_id, cs["stage"], cs["stage"], user.id, user.role,
                   Jsonb({"rule_id": rule_id, "stage": body.stage}), body.note.strip()))
        return run(c, case_id, user, body.stage)
