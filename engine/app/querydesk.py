"""Query desk: one row per payer query. Draft from the record → (doctor signs if clinical) → send.
Sending records the reply and moves the case through the same apply() path."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from . import adjudicate as adj
from . import drafting as dr
from .auth import User, current_user
from .billing import _context, _f
from .db import tx

router = APIRouter(tags=["queries"])
ANSWER_EVENT = {"preauth": "answer_preauth_query", "discharge": "answer_discharge_query", "claim": "answer_claim_query"}


def open_query(c, user: User, case: dict, stage: str, payload: dict):
    text = (payload.get("query_text") or "").strip()
    code = payload.get("code") or dr.classify(text)
    c.execute("""insert into queries (hospital_id, case_id, stage, query_text, code, code_set_by, requires_signoff)
                 values (%s,%s,%s,%s,%s,%s,%s)""",
              (user.hospital_id, case["id"], stage, text, code, "person" if payload.get("code") else "auto",
               dr.QUERY_CODES[code]["doctor"]))


def _ctx(c, case_id: str, user: User):
    row, policy, rules, hospital = _context(c, case_id, user.hospital_id)
    extra = c.execute("""select pt.full_name, p.policy_number, py.name as payer, h.name as hospital, h.letter_signatory
                         from cases cs join patients pt on pt.id=cs.patient_id left join policies p on p.id=cs.policy_id
                         left join payers py on py.id=cs.payer_id join hospitals h on h.id=cs.hospital_id
                         where cs.id=%s""", (case_id,)).fetchone()
    from .scrubber import clinical
    proc = clinical()["procedures"].get(row["procedure_id"] or "")
    ctx = {"case_number": row["case_number"], "patient": extra["full_name"], "policy_number": extra["policy_number"],
           "payer": extra["payer"], "hospital": extra["hospital"], "signatory": extra["letter_signatory"],
           "dx": row["provisional_dx"], "icd10": row["icd10"], "procedure": proc["name"] if proc else None,
           "admitted_at": row["admitted_at"], "discharged_at": row["discharged_at"]}
    lines = c.execute("select * from bill_lines where case_id=%s order by position", (case_id,)).fetchall()
    audit = None
    if lines:
        audit = adj.adjudicate([adj.BillLine(l["category"], l["description"], _f(l["qty"]), _f(l["rate"]), _f(l["mou_rate"]),
                                             l["to_payer"]) for l in lines], policy, rules, hospital, row["procedure_id"],
                               row["room_category"], _f(row["approved_amount"]) or 0)
    docs = c.execute("""select d.id, d.doc_type, d.filename, coalesce(string_agg(p.text, E'\\n' order by p.page), '') as text
                        from documents d left join document_pages p on p.document_id=d.id where d.case_id=%s
                        group by d.id order by min(d.created_at)""", (case_id,)).fetchall()
    return row, ctx, lines, audit, docs, rules


def _load(c, qid: str, user: User, lock=False) -> dict:
    q = c.execute("select * from queries where id=%s and hospital_id=%s" + (" for update" if lock else ""),
                  (qid, user.hospital_id)).fetchone()
    if not q:
        raise HTTPException(404, "Query not found")
    return q


def _log(c, user: User, case_id, event: str, payload: dict):
    st = c.execute("select stage from cases where id=%s", (case_id,)).fetchone()["stage"]
    c.execute("""insert into case_events (hospital_id, case_id, event, from_stage, to_stage, actor_id, actor_role, payload)
                 values (%s,%s,%s,%s,%s,%s,%s,%s)""", (user.hospital_id, case_id, event, st, st, user.id, user.role, Jsonb(payload)))


def _view(q: dict) -> dict:
    return {**q, "code_name": dr.QUERY_CODES[q["code"]]["name"], "placeholders": dr.placeholders(q["draft"] or ""),
            "can_send": q["status"] in ("drafted", "signed") and not dr.placeholders(q["draft"] or "")
                        and (not q["requires_signoff"] or q["status"] == "signed")}


@router.get("/queries")
def list_queries(status: str | None = None, user: User = Depends(current_user)):
    with tx() as c:
        rows = c.execute("""select q.*, cs.case_number, pt.full_name as patient, py.name as payer
                            from queries q join cases cs on cs.id=q.case_id join patients pt on pt.id=cs.patient_id
                            left join payers py on py.id=cs.payer_id
                            where q.hospital_id=%s and (%s::text is null or q.status=%s) order by q.status='sent', q.received_at""",
                         (user.hospital_id, status, status)).fetchall()
    return {"queries": [_view(r) for r in rows], "codes": dr.QUERY_CODES}


@router.get("/cases/{case_id}/queries")
def case_queries(case_id: str, user: User = Depends(current_user)):
    with tx() as c:
        rows = c.execute("select * from queries where case_id=%s and hospital_id=%s order by received_at desc",
                         (case_id, user.hospital_id)).fetchall()
    return {"queries": [_view(r) for r in rows], "codes": dr.QUERY_CODES}


@router.post("/queries/{qid}/draft")
def make_draft(qid: str, user: User = Depends(current_user)):
    if user.role not in ("desk", "doctor", "accounts", "admin"):
        raise HTTPException(403, "Your role cannot draft replies")
    with tx() as c:
        q = _load(c, qid, user, lock=True)
        if q["status"] == "sent":
            raise HTTPException(409, "Already answered")
        row, ctx, lines, audit, docs, _ = _ctx(c, str(q["case_id"]), user)
        d = dr.draft_reply(q["code"], q["query_text"], q["stage"], ctx, docs, lines, audit)
        q = c.execute("""update queries set draft=%s, attachments=%s, missing=%s, requires_signoff=%s, status='drafted',
                         signed_by=null, signed_name=null, signed_at=null where id=%s returning *""",
                      (d["draft"], Jsonb(d["attachments"]), Jsonb(d["missing"]), d["requires_signoff"], qid)).fetchone()
        _log(c, user, q["case_id"], "query_drafted", {"query_id": qid, "code": q["code"], "placeholders": len(d["placeholders"])})
    return _view(q)


class QueryEdit(BaseModel):
    draft: str | None = None
    attachments: list[str] | None = None
    code: str | None = None


@router.put("/queries/{qid}")
def edit(qid: str, body: QueryEdit, user: User = Depends(current_user)):
    if user.role not in ("desk", "doctor", "accounts", "admin"):
        raise HTTPException(403, "Your role cannot edit replies")
    if body.code and body.code not in dr.QUERY_CODES:
        raise HTTPException(422, "Unknown query code")
    with tx() as c:
        q = _load(c, qid, user, lock=True)
        if q["status"] == "sent":
            raise HTTPException(409, "Already answered")
        if body.attachments is not None:
            ok = {str(r["id"]) for r in c.execute("select id from documents where case_id=%s", (q["case_id"],)).fetchall()}
            if not set(body.attachments) <= ok:
                raise HTTPException(422, "Attachments must be documents on this case")
        code = body.code or q["code"]
        draft = body.draft if body.draft is not None else q["draft"]
        attachments = body.attachments if body.attachments is not None else q["attachments"]
        requires = dr.QUERY_CODES[code]["doctor"] if body.code else q["requires_signoff"]
        status = "drafted" if draft else "open"          # any edit clears a signature
        q = c.execute("""update queries set draft=%s, attachments=%s, code=%s, code_set_by=%s, requires_signoff=%s,
                         status=%s, signed_by=null, signed_name=null, signed_at=null where id=%s returning *""",
                      (draft, Jsonb(attachments), code, "person" if body.code else q["code_set_by"], requires,
                       status, qid)).fetchone()
    return _view(q)


@router.post("/queries/{qid}/signoff")
def signoff(qid: str, user: User = Depends(current_user)):
    if user.role not in ("doctor", "admin"):
        raise HTTPException(403, {"code": "clinician_gate", "message": "Only a doctor signs a clinical reply"})
    with tx() as c:
        q = _load(c, qid, user, lock=True)
        if q["status"] != "drafted":
            raise HTTPException(409, "Nothing to sign: draft the reply first")
        if (ph := dr.placeholders(q["draft"])):
            raise HTTPException(422, {"code": "placeholders", "message": f"Fill in {len(ph)} bracketed item(s) before signing",
                                      "items": ph})
        q = c.execute("""update queries set status='signed', signed_by=%s, signed_name=%s, signed_at=%s where id=%s returning *""",
                      (user.id, user.full_name, datetime.now(timezone.utc), qid)).fetchone()
        _log(c, user, q["case_id"], "query_signed", {"query_id": qid})
    return _view(q)


@router.post("/queries/{qid}/send")
async def send(qid: str, user: User = Depends(current_user)):
    """A person confirms the reply goes to the payer. Desk queries: recorded as sent (portal / email).
    NHCX queries: sent over NHCX as a Communication (workflow 19 / 161) with the attached documents."""
    from .cases import apply
    from . import nhcx
    with tx() as c:
        q = _load(c, qid, user)
    if q["status"] == "sent":
        raise HTTPException(409, "Already answered")
    if not q["draft"]:
        raise HTTPException(409, "Draft the reply first")
    if (ph := dr.placeholders(q["draft"])):
        raise HTTPException(422, {"code": "placeholders", "message": f"{len(ph)} bracketed item(s) still to fill in", "items": ph})
    if q["requires_signoff"] and q["status"] != "signed":
        raise HTTPException(422, {"code": "needs_signoff", "message": "A doctor must sign this clinical reply first"})
    over_nhcx = q["source"] == "nhcx" and nhcx.enabled() and (q.get("nhcx_request") or {}).get("headers")
    holder: dict = {}
    if over_nhcx:
        with tx() as c:
            headers, fhir, wire = nhcx.build_query_reply(c, q, user)

    def mark(c, case):
        c.execute("update queries set status='sent', sent_by=%s, sent_at=%s where id=%s",
                  (user.id, datetime.now(timezone.utc), qid))
        if over_nhcx:
            holder["xid"] = nhcx._log(c, user.hospital_id, q["case_id"], "communication", "on_request", "outbound",
                                      headers, wire, fhir, "sending")

    # same ordering rule as NHCX submits: move the case BEFORE sending (the decision can arrive in flight)
    res = apply(str(q["case_id"]), ANSWER_EVENT[q["stage"]],
                {"reply_text": q["draft"], "query_id": qid, "attachments": q["attachments"]}, user,
                note=f"Reply to {q['code']}" + (" (over NHCX)" if over_nhcx else ""), hook=mark)
    if over_nhcx:
        error = await nhcx.send_query_reply(holder["xid"], wire)
        if error:   # the reply never reached NHCX: reopen the query so the desk can try again
            with tx() as c:
                c.execute("update queries set status=%s, sent_by=null, sent_at=null where id=%s",
                          ("signed" if q["requires_signoff"] else "drafted", qid))
            try:
                apply(str(q["case_id"]), f"{q['stage']}_query", {"query_text": q["query_text"]}, nhcx._system(user.hospital_id),
                      note=f"Reply not delivered: {error}", open_query=False)
            except HTTPException:
                pass   # the insurer already decided in the meantime
            raise HTTPException(502, {"code": "nhcx_send_failed", "message": f"Not delivered to NHCX: {error}. The query is open again."})
        res["nhcx"] = {"exchange_id": holder["xid"], "status": "acked"}
    return res
