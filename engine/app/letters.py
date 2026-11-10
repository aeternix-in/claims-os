"""Ageing sweep that drafts follow-up letters, plus approve / send / PDF.
Letters are drafts until a named person approves them; nothing is sent automatically."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from . import adjudicate as adj
from . import drafting as dr
from .auth import User, current_user
from .db import tx
from .querydesk import _ctx

router = APIRouter(tags=["letters"])
GRIEVANCE_AFTER_DAYS = 15        # after the payment term, and after a reminder was sent
REMINDER_REPEAT_DAYS = 15
SHORTFALL_MIN = 100


def _days(since) -> int:
    return (datetime.now(timezone.utc) - since).days if since else 0


def due_letters(c, case: dict, user: User) -> list[tuple[str, dict, str]]:
    """Which letters this case needs now: [(kind, facts, reason)]."""
    out = []
    row, ctx, lines, audit, docs, _ = _ctx(c, str(case["id"]), user)
    rules = (c.execute("select rules from payers where id=%s", (row["payer_id"],)).fetchone() or {}).get("rules") or {}
    term = int(rules.get("payment_term_days") or 30)
    window = int(rules.get("claim_file_window_days") or 15)
    sub = c.execute("select max(created_at) as t from case_events where case_id=%s and event='submit_claim'", (case["id"],)).fetchone()["t"]
    last = {r["kind"]: r for r in c.execute("""select distinct on (kind) kind, status, created_at, sent_at from letters
                                               where case_id=%s and status<>'dropped' order by kind, created_at desc""", (case["id"],)).fetchall()}
    open_kinds = {k for k, v in last.items() if v["status"] in ("draft", "approved")}
    amount = float(row["approved_amount"] or 0)

    if row["stage"] in ("submitted", "claim_query") and sub:
        days = _days(sub)
        if days > term and "reminder" not in open_kinds and (
                "reminder" not in last or _days(last["reminder"]["sent_at"] or last["reminder"]["created_at"]) >= REMINDER_REPEAT_DAYS):
            out.append(("reminder", {"days": days, "term": term, "amount": amount}, f"{days} days with payer, term {term}"))
        if days > term + GRIEVANCE_AFTER_DAYS and last.get("reminder", {}).get("status") == "sent" and "grievance" not in last:
            out.append(("grievance", {"days": days, "term": term, "amount": amount}, f"{days} days, reminder already sent"))

    if row["stage"] in ("settled", "paid") and row["settled_amount"] is not None and audit and "dispute" not in last:
        expected = float(audit["expected_payable"])
        settled = float(row["settled_amount"])
        if expected - settled > SHORTFALL_MIN:
            out.append(("dispute", {"expected": expected, "settled": settled, "amount": expected - settled, "deductions": []},
                        f"Short-paid by Rs {expected - settled:,.0f} against the bill audit"))
    if row["stage"] == "paid" and row["settled_amount"] is not None and row["received_amount"] is not None and "dispute" not in last:
        tds_pct = float(rules.get("tds_pct", 10))
        exp_credit = adj.expected_credit(float(row["settled_amount"]), tds_pct)["expected_credit"]
        if exp_credit - float(row["received_amount"]) > SHORTFALL_MIN and not any(k == "dispute" for k, _, _ in out):
            gap = exp_credit - float(row["received_amount"])
            out.append(("dispute", {"expected": exp_credit, "settled": float(row["received_amount"]), "amount": gap,
                                    "deductions": [f"Bank credit Rs {float(row['received_amount']):,.0f} is below the settled amount less TDS (Rs {exp_credit:,.0f})"]},
                        f"Bank credit short by Rs {gap:,.0f} after TDS"))
    if row["stage"] == "rejected" and "appeal" not in last:
        reason = c.execute("""select payload->>'reason' as r from case_events where case_id=%s and event='claim_rejected'
                              order by id desc limit 1""", (case["id"],)).fetchone()
        out.append(("appeal", {"reason": reason["r"] if reason else None}, "Claim rejected"))
    if row["stage"] in ("discharged", "claim") and row["discharged_at"]:
        days = _days(row["discharged_at"])
        if days > window and "condonation" not in last:
            out.append(("condonation", {"days": days, "window": window}, f"{days} days since discharge, window {window}"))
    return [(k, f, r) for k, f, r in out]


@router.post("/letters/sweep")
def sweep(user: User = Depends(current_user)):
    """Run the ageing sweep for this hospital. The scheduled worker calls the same function."""
    if user.role not in ("desk", "accounts", "owner", "admin"):
        raise HTTPException(403, "Your role cannot run the sweep")
    made = []
    with tx() as c:
        cases = c.execute("""select id from cases where hospital_id=%s and stage in
                             ('discharged','claim','submitted','claim_query','settled','paid','rejected')""", (user.hospital_id,)).fetchall()
        for case in cases:
            for kind, facts, reason in due_letters(c, case, user):
                _, ctx, *_ = _ctx(c, str(case["id"]), user)
                L = dr.letter(kind, ctx, facts)
                r = c.execute("""insert into letters (hospital_id, case_id, kind, subject, body, reason, sources, amount)
                                 values (%s,%s,%s,%s,%s,%s,%s,%s) returning id, kind, subject""",
                              (user.hospital_id, case["id"], kind, L["subject"], L["body"], reason, Jsonb(L["sources"]),
                               facts.get("amount"))).fetchone()
                made.append(r)
    return {"drafted": made}


def _view(l: dict) -> dict:
    return {**l, "placeholders": dr.placeholders(l["body"])}


@router.get("/letters")
def list_letters(status: str | None = None, user: User = Depends(current_user)):
    with tx() as c:
        rows = c.execute("""select l.*, cs.case_number, pt.full_name as patient, py.name as payer from letters l
                            join cases cs on cs.id=l.case_id join patients pt on pt.id=cs.patient_id left join payers py on py.id=cs.payer_id
                            where l.hospital_id=%s and (%s::text is null or l.status=%s) and l.status<>'dropped'
                            order by l.status='sent', l.created_at desc""", (user.hospital_id, status, status)).fetchall()
    return {"letters": [_view(r) for r in rows]}


class LetterEdit(BaseModel):
    subject: str | None = None
    body: str | None = None


def _get(c, lid, user, lock=False):
    l = c.execute("select * from letters where id=%s and hospital_id=%s" + (" for update" if lock else ""), (lid, user.hospital_id)).fetchone()
    if not l:
        raise HTTPException(404, "Letter not found")
    return l


@router.put("/letters/{lid}")
def edit_letter(lid: str, body: LetterEdit, user: User = Depends(current_user)):
    with tx() as c:
        l = _get(c, lid, user, True)
        if l["status"] == "sent":
            raise HTTPException(409, "Already sent")
        l = c.execute("""update letters set subject=coalesce(%s,subject), body=coalesce(%s,body), status='draft',
                         approved_by=null, approved_at=null where id=%s returning *""", (body.subject, body.body, lid)).fetchone()
    return _view(l)


def _set(lid: str, user: User, status: str, roles: set[str]):
    if user.role not in roles:
        raise HTTPException(403, "Your role cannot do this")
    with tx() as c:
        l = _get(c, lid, user, True)
        if status == "approved":
            if l["status"] != "draft":
                raise HTTPException(409, "Only a draft can be approved")
            if (ph := dr.placeholders(l["body"])):
                raise HTTPException(422, {"code": "placeholders", "message": f"Fill in {len(ph)} bracketed item(s) first", "items": ph})
            l = c.execute("update letters set status='approved', approved_by=%s, approved_at=now() where id=%s returning *", (user.id, lid)).fetchone()
        elif status == "sent":
            if l["status"] != "approved":
                raise HTTPException(409, "Approve the letter before marking it sent")
            l = c.execute("update letters set status='sent', sent_at=now() where id=%s returning *", (lid,)).fetchone()
        else:
            l = c.execute("update letters set status='dropped' where id=%s returning *", (lid,)).fetchone()
        st = c.execute("select stage from cases where id=%s", (l["case_id"],)).fetchone()["stage"]
        c.execute("""insert into case_events (hospital_id, case_id, event, from_stage, to_stage, actor_id, actor_role, payload)
                     values (%s,%s,%s,%s,%s,%s,%s,%s)""", (user.hospital_id, l["case_id"], f"letter_{status}", st, st, user.id, user.role,
                                                       Jsonb({"letter_id": lid, "kind": l["kind"]})))
    return _view(l)


@router.post("/letters/{lid}/approve")
def approve(lid: str, user: User = Depends(current_user)):
    return _set(lid, user, "approved", {"accounts", "owner", "admin", "desk"})


@router.post("/letters/{lid}/sent")
def mark_sent(lid: str, user: User = Depends(current_user)):
    return _set(lid, user, "sent", {"accounts", "owner", "admin", "desk"})


@router.post("/letters/{lid}/drop")
def drop(lid: str, user: User = Depends(current_user)):
    return _set(lid, user, "dropped", {"accounts", "owner", "admin", "desk"})


@router.get("/letters/{lid}/pdf")
def pdf(lid: str, user: User = Depends(current_user)):
    from fpdf import FPDF
    with tx() as c:
        l = _get(c, lid, user)
        h = c.execute("select name, address from hospitals where id=%s", (user.hospital_id,)).fetchone()
    p = FPDF()
    p.add_page()
    p.set_font("Helvetica", "B", 14)
    p.cell(0, 8, h["name"], new_x="LMARGIN", new_y="NEXT")
    p.set_font("Helvetica", size=10)
    if h["address"]:
        p.multi_cell(0, 5, h["address"])
    p.cell(0, 6, (l["approved_at"] or l["created_at"]).strftime("%d %b %Y"), new_x="LMARGIN", new_y="NEXT")
    p.ln(4)
    p.set_font("Helvetica", "B", 11)
    clean = lambda s: s.replace("₹", "Rs ").encode("latin-1", "replace").decode("latin-1")
    p.multi_cell(0, 6, clean(l["subject"]))
    p.ln(2)
    p.set_font("Helvetica", size=11)
    p.multi_cell(0, 6, clean(l["body"]))
    if l["status"] == "draft":
        p.set_text_color(173, 42, 34)
        p.ln(4)
        p.cell(0, 6, "DRAFT - not approved", new_x="LMARGIN", new_y="NEXT")
    return Response(bytes(p.output()), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{l["kind"]}-{lid[:8]}.pdf"'})
