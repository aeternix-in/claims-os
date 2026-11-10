"""Bank match routes: import a statement, see proposals, confirm, match by hand, ignore."""
import random
from datetime import date, timedelta

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from . import adjudicate as adj
from . import matching as mt
from .auth import User, current_user
from .db import tx

router = APIRouter(prefix="/bank", tags=["bank"])
ROLES = {"accounts", "owner", "admin"}


def _need(user: User):
    if user.role not in ROLES:
        raise HTTPException(403, "Only accounts or the owner matches bank credits")


def _open_cases(c, hospital_id: str) -> list[dict]:
    rows = c.execute("""select cs.id, cs.case_number, cs.payer_id, cs.payer_ref, cs.nhcx_utr, cs.settled_amount, py.rules,
                               pt.full_name as patient, py.name as payer
                        from cases cs left join payers py on py.id=cs.payer_id join patients pt on pt.id=cs.patient_id
                        where cs.hospital_id=%s and cs.stage='settled' and cs.settled_amount is not null""",
                     (hospital_id,)).fetchall()
    out = []
    for r in rows:
        settled = float(r["settled_amount"])
        tds = float((r["rules"] or {}).get("tds_pct", 10))
        out.append({"id": str(r["id"]), "case_number": r["case_number"], "payer_id": str(r["payer_id"]) if r["payer_id"] else None,
                    "payer_ref": r["payer_ref"], "nhcx_utr": r["nhcx_utr"], "settled": settled, "patient": r["patient"], "payer": r["payer"],
                    "expected": adj.expected_credit(settled, tds)["expected_credit"]})
    return out


def _insert_credits(c, user: User, parsed: mt.Parsed, filename: str, sample: bool) -> dict:
    payers = c.execute("select id, name, via_tpa from payers where hospital_id=%s", (user.hospital_id,)).fetchall()
    imp = c.execute("""insert into bank_imports (hospital_id, filename, rows_read, skipped, is_sample, imported_by)
                       values (%s,%s,%s,%s,%s,%s) returning id""",
                    (user.hospital_id, filename, parsed.rows, parsed.skipped, sample, user.id)).fetchone()["id"]
    new = dup = 0
    for cr in parsed.credits:
        r = c.execute("""insert into bank_credits (hospital_id, import_id, value_date, amount, narration, utr, payer_id, is_sample)
                         values (%s,%s,%s,%s,%s,%s,%s,%s) on conflict (hospital_id, utr, amount) do nothing returning id""",
                      (user.hospital_id, imp, cr["value_date"], cr["amount"], cr["narration"], cr["utr"],
                       mt.detect_payer(cr["narration"], payers), sample)).fetchone()
        new += 1 if r else 0
        dup += 0 if r else 1
    c.execute("update bank_imports set credits_new=%s, duplicates=%s where id=%s", (new, dup, imp))
    return {"import_id": str(imp), "rows": parsed.rows, "credits_new": new, "duplicates": dup,
            "skipped": parsed.skipped, "problems": parsed.problems}


@router.post("/import")
async def import_statement(file: UploadFile = File(...), user: User = Depends(current_user)):
    _need(user)
    raw = await file.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(413, "Statement larger than 5 MB")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    parsed = mt.parse_statement(text)
    if parsed.problems and not parsed.credits:
        raise HTTPException(422, {"code": "unreadable", "message": parsed.problems[0] + ". Export the statement as CSV."})
    with tx() as c:
        return _insert_credits(c, user, parsed, file.filename or "statement.csv", False)


@router.post("/sample-statement")
def sample_statement(user: User = Depends(current_user)):
    """Demo only: a statement built from this hospital's settled claims, with one of each kind of credit.
    Credits are marked as sample. Never use with real patient data."""
    _need(user)
    with tx() as c:
        cases = _open_cases(c, user.hospital_id)
        if not cases:
            raise HTTPException(409, "No settled claims waiting for money: settle a claim first")
        rnd = random.Random()
        lines = ["Account statement (SAMPLE)", "", "Date,Narration,Ref No,Debit,Credit,Balance"]
        d = date.today()
        bal = 2500000.0
        used = 0
        leftover: list[dict] = []
        for i, cs in enumerate(cases):
            utr = f"HDFCR{d.year}{rnd.randint(10_000_000, 99_999_999)}"
            payer = (cs["payer"] or "PAYER").upper()
            kind = i % 4
            if kind == 0 and cs["payer_ref"]:            # exact
                amt, narr = cs["expected"], f"NEFT CR {payer} {cs['payer_ref']} {utr}"
            elif kind == 1:                              # likely (no reference)
                amt, narr = cs["expected"], f"NEFT CR {payer} CLAIM SETTLEMENT {utr}"
            elif kind == 2 and cs["payer_ref"]:          # short payment
                amt, narr = round(cs["expected"] * 0.97), f"NEFT CR {payer} {cs['payer_ref']} {utr}"
            else:
                leftover.append(cs)                       # left for a bulk credit below
                continue
            bal += amt
            used += 1
            lines.append(f"{(d - timedelta(days=i % 5)).strftime('%d/%m/%Y')},{narr},{utr},,{amt:.2f},{bal:.2f}")
        by_payer: dict[str, list[dict]] = {}
        for cs in leftover:
            by_payer.setdefault(cs["payer_id"] or "", []).append(cs)
        for group in by_payer.values():
            if len(group) < 2:
                continue                                  # a lone leftover stays unpaid (still awaiting money)
            bulk = group[:3]
            amt = sum(cs["expected"] for cs in bulk)
            utr = f"ICICR{d.year}{rnd.randint(10_000_000, 99_999_999)}"
            bal += amt
            lines.append(f"{d.strftime('%d/%m/%Y')},NEFT CR {(bulk[0]['payer'] or '').upper()} BULK SETTLEMENT {len(bulk)} CLAIMS {utr},{utr},,{amt:.2f},{bal:.2f}")
        lines.append(f"{d.strftime('%d/%m/%Y')},UPI CR WALK-IN PATIENT DEPOSIT,UPI{rnd.randint(10**9, 10**10)},,15000.00,{bal + 15000:.2f}")
        lines.append(f"{d.strftime('%d/%m/%Y')},NEFT DR PHARMA SUPPLIER PAYMENT,N{rnd.randint(10**8, 10**9)},84000.00,,{bal - 69000:.2f}")
        parsed = mt.parse_statement("\n".join(lines))
        res = _insert_credits(c, user, parsed, "sample-statement.csv", True)
    res["csv"] = "\n".join(lines)
    return res


@router.get("")
def overview(user: User = Depends(current_user)):
    with tx() as c:
        credits = c.execute("""select b.*, py.name as payer from bank_credits b left join payers py on py.id=b.payer_id
                               where b.hospital_id=%s order by b.status='matched', b.value_date desc, b.created_at desc limit 300""",
                            (user.hospital_id,)).fetchall()
        cases = _open_cases(c, user.hospital_id)
        unmatched = [dict(x, amount=float(x["amount"])) for x in credits if x["status"] == "unmatched"]
        proposals = mt.propose(unmatched, cases)
        matches = c.execute("""select m.*, cs.case_number from credit_matches m join cases cs on cs.id=m.case_id
                               where m.hospital_id=%s order by m.confirmed_at desc limit 200""", (user.hospital_id,)).fetchall()
    proposed_ids = {p["credit_id"] for p in proposals}
    by_credit: dict[str, list] = {}
    for m in matches:
        by_credit.setdefault(str(m["credit_id"]), []).append(m)
    for cr in credits:
        cr["proposal"] = next((p for p in proposals if p["credit_id"] == str(cr["id"])), None)
        cr["matches"] = by_credit.get(str(cr["id"]), [])
    un = [c for c in credits if c["status"] == "unmatched"]
    return {
        "credits": credits, "open_cases": cases,
        "stats": {"unmatched_count": len(un), "unmatched_amount": sum(float(c["amount"]) for c in un),
                  "exact": sum(1 for p in proposals if p["confidence"] == "exact"),
                  "likely": sum(1 for p in proposals if p["confidence"] in ("likely", "split")),
                  "no_clue": sum(1 for c in un if str(c["id"]) not in proposed_ids),
                  "awaiting_amount": sum(c["expected"] for c in cases), "awaiting_count": len(cases)},
    }


class Line(BaseModel):
    case_id: str
    amount: float = Field(gt=0)


class Confirm(BaseModel):
    credit_id: str
    lines: list[Line] = Field(min_length=1)
    confidence: str = "manual"
    why: str = "Matched by hand"


def _confirm_one(user: User, body: Confirm) -> dict:
    """Locks the credit, checks the amounts add up, records the match, then moves each claim to paid
    through the same audited apply() path."""
    from .cases import apply
    if body.confidence not in ("exact", "likely", "split", "manual"):
        raise HTTPException(422, "Bad confidence")
    with tx() as c:
        cr = c.execute("select * from bank_credits where id=%s and hospital_id=%s for update", (body.credit_id, user.hospital_id)).fetchone()
        if not cr:
            raise HTTPException(404, "Credit not found")
        if cr["status"] != "unmatched":
            raise HTTPException(409, {"code": "already", "message": "This credit is already matched or ignored"})
        total = sum(l.amount for l in body.lines)
        if abs(total - float(cr["amount"])) > mt.TOL * len(body.lines):
            raise HTTPException(422, {"code": "sum", "message": f"Lines add up to ₹{total:,.0f}; the credit is ₹{float(cr['amount']):,.0f}"})
        if len({l.case_id for l in body.lines}) != len(body.lines):
            raise HTTPException(422, "A claim appears twice")
        open_ = {x["id"]: x for x in _open_cases(c, user.hospital_id)}
        for l in body.lines:
            if l.case_id not in open_:
                raise HTTPException(422, {"code": "not_open", "message": "Each claim must be settled and waiting for money"})
    done = []
    for l in body.lines:
        exp = open_[l.case_id]["expected"]

        def record(conn, case, l=l, exp=exp):
            conn.execute("""insert into credit_matches (hospital_id, credit_id, case_id, amount, expected, confidence, why, confirmed_by)
                            values (%s,%s,%s,%s,%s,%s,%s,%s)""",
                         (user.hospital_id, body.credit_id, l.case_id, l.amount, exp, body.confidence, body.why, user.id))
        r = apply(l.case_id, "bank_credit_matched", {"utr": cr["utr"] or f"CREDIT-{str(cr['id'])[:8]}",
                  "received_amount": l.amount, "credit_id": body.credit_id}, user,
                  note=f"Bank credit {cr['value_date']} ₹{float(cr['amount']):,.0f} ({body.confidence})", hook=record)
        done.append({"case_id": l.case_id, "case_number": r["case"]["case_number"], "amount": l.amount,
                     "shortfall": max(0.0, round(exp - l.amount, 2))})
    with tx() as c:
        c.execute("update bank_credits set status='matched' where id=%s", (body.credit_id,))
    return {"credit_id": body.credit_id, "matched": done}


@router.post("/confirm")
def confirm(body: Confirm, user: User = Depends(current_user)):
    _need(user)
    return _confirm_one(user, body)


class Bulk(BaseModel):
    levels: list[str] = ["exact"]


@router.post("/confirm-proposals")
def confirm_proposals(body: Bulk, user: User = Depends(current_user)):
    """'Match certain': confirm every current proposal at the chosen levels (default: exact only)."""
    _need(user)
    data = overview(user)
    results, errors = [], []
    for cr in data["credits"]:
        p = cr.get("proposal")
        if not p or p["confidence"] not in body.levels:
            continue
        try:
            results.append(_confirm_one(user, Confirm(credit_id=p["credit_id"], confidence=p["confidence"], why=p["why"],
                                                      lines=[Line(case_id=l["case_id"], amount=l["amount"]) for l in p["lines"]])))
        except HTTPException as e:
            errors.append({"credit_id": p["credit_id"], "error": e.detail})
    shortfalls = [m for r in results for m in r["matched"] if m["shortfall"] > mt.TOL]
    return {"confirmed": len(results), "results": results, "errors": errors, "shortfalls": shortfalls}


class Ignore(BaseModel):
    note: str = Field(min_length=3)


@router.post("/credits/{credit_id}/ignore")
def ignore(credit_id: str, body: Ignore, user: User = Depends(current_user)):
    """Not a claim payment (patient deposit, refund, interest). Kept, with the reason."""
    _need(user)
    with tx() as c:
        r = c.execute("""update bank_credits set status='ignored', ignored_note=%s
                         where id=%s and hospital_id=%s and status='unmatched' returning *""",
                      (body.note, credit_id, user.hospital_id)).fetchone()
    if not r:
        raise HTTPException(409, "Credit not found or already handled")
    return r
