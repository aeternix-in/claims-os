"""Bank statement parsing and the matching ladder. Pure functions.

Ladder (product context file, "Bank matching ladder"):
 1. exact  - the payer's reference for the claim appears in the narration
 2. likely - same payer and the exact expected amount (settled less TDS), and only one claim fits
 3. split  - one credit covers several claims of the same payer; proposed only when exactly one
             combination of claims adds up to it
 4. manual - everything else is matched by hand
Nothing is matched until a person confirms. Only a confirmed match closes a claim."""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from itertools import combinations

TOL = 1.0               # rupees: rounding tolerance when comparing amounts
MAX_SPLIT = 4           # largest number of claims one bulk credit is split across
MAX_SPLIT_POOL = 18     # combinations are searched only among this many candidate claims


# ---------------- statement parsing ----------------
COLS = {
    "date": ["txn date", "transaction date", "tran date", "date", "value date", "value dt", "posting date"],
    "narration": ["narration", "description", "particulars", "remarks", "transaction remarks", "transaction details", "details"],
    "credit": ["credit", "deposit", "deposits", "deposit amt", "cr amount", "credit amount", "amount (cr)"],
    "debit": ["debit", "withdrawal", "withdrawals", "withdrawal amt", "dr amount", "debit amount", "amount (dr)"],
    "amount": ["amount", "txn amount", "transaction amount"],
    "drcr": ["dr/cr", "cr/dr", "type", "dr / cr"],
    "ref": ["utr", "utr no", "ref no", "reference", "reference no", "chq ref no", "ref no cheque no", "cheque no"],
}
UTR_RE = re.compile(r"\b([A-Z]{4}[RHN0-9][0-9A-Z]{6,17})\b")


def _k(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower())


def _find(headers: list[str], key: str) -> int | None:
    """Header match ignoring case and punctuation; exact first, then 'starts with' (e.g. 'Deposit Amt.')."""
    h = [_k(x) for x in headers]
    names = [_k(n) for n in COLS[key]]
    for n in names:
        if n in h:
            return h.index(n)
    for n in names:
        if len(n) >= 4:
            for i, x in enumerate(h):
                if x.startswith(n) and not any(x == _k(o) for k2, v in COLS.items() if k2 != key for o in v):
                    return i
    return None


def _num(s: str) -> float | None:
    s = (s or "").replace(",", "").replace("₹", "").replace("INR", "").strip()
    if not s or s in ("-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _date(s: str) -> date | None:
    s = (s or "").strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y", "%Y-%m-%d", "%d %b %Y", "%d-%b-%Y", "%d-%b-%y", "%d %b %y", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


@dataclass
class Parsed:
    credits: list[dict] = field(default_factory=list)
    rows: int = 0
    skipped: int = 0
    problems: list[str] = field(default_factory=list)


def parse_statement(text: str) -> Parsed:
    """Reads a bank CSV export. Finds the header row (banks put account details above it)."""
    out = Parsed()
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    hdr_i = next((i for i, r in enumerate(rows[:40])
                  if _find(r, "date") is not None and _find(r, "narration") is not None
                  and (_find(r, "credit") is not None or _find(r, "amount") is not None)), None)
    if hdr_i is None:
        out.problems.append("Could not find a header row with date, narration and credit/amount columns")
        return out
    h = rows[hdr_i]
    di, ni, ci, ai, ti, ri = (_find(h, k) for k in ("date", "narration", "credit", "amount", "drcr", "ref"))
    for r in rows[hdr_i + 1:]:
        if not any(x.strip() for x in r):
            continue
        out.rows += 1
        get = lambda i: r[i] if i is not None and i < len(r) else ""
        d = _date(get(di))
        amt = _num(get(ci)) if ci is not None else None
        if ci is None and ai is not None:
            amt = _num(get(ai))
            kind = get(ti).strip().upper()
            if kind and not kind.startswith("C"):
                amt = None
        if not d or not amt or amt <= 0:
            out.skipped += 1
            continue
        narr = " ".join(get(ni).split())
        ref = get(ri).strip()
        m = UTR_RE.search(narr.upper())
        utr = ref if len(ref) >= 8 else (m.group(1) if m else (ref or None))
        out.credits.append({"value_date": d, "amount": round(amt, 2), "narration": narr, "utr": utr})
    return out


def detect_payer(narration: str, payers: list[dict]) -> str | None:
    """payers: [{id, name, via_tpa}]. Longest name match wins (so 'Insurer C' beats 'Insurer')."""
    n = narration.upper()
    best = None
    for p in payers:
        for name in filter(None, [p["name"], p.get("via_tpa")]):
            if name.upper() in n and (best is None or len(name) > best[1]):
                best = (str(p["id"]), len(name))
    return best[0] if best else None


# ---------------- matching ----------------
def _norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def propose(credits: list[dict], cases: list[dict]) -> list[dict]:
    """
    credits: unmatched [{id, amount, narration, payer_id}]
    cases:   settled, unpaid [{id, case_number, payer_id, payer_ref, expected, settled}]
    -> [{credit_id, confidence, why, lines:[{case_id, case_number, amount, expected}], shortfall}]
    """
    out: list[dict] = []
    taken: set[str] = set()

    def line(c, amount):
        return {"case_id": str(c["id"]), "case_number": c["case_number"], "amount": round(amount, 2),
                "expected": round(c["expected"], 2)}

    # 1. exact: the UTR from the insurer's NHCX payment notice, or the payer's reference, in the narration.
    #    Short payment still matches, flagged.
    rest = []
    for cr in credits:
        n = _norm(cr["narration"]) + _norm(cr.get("utr") or "")
        hit = [c for c in cases if c.get("nhcx_utr") and len(_norm(c["nhcx_utr"])) >= 6
               and _norm(c["nhcx_utr"]) in n and str(c["id"]) not in taken]
        why = "UTR {} from the insurer's NHCX payment notice"
        if len(hit) != 1:
            hit = [c for c in cases if c.get("payer_ref") and len(_norm(c["payer_ref"])) >= 4
                   and _norm(c["payer_ref"]) in n and str(c["id"]) not in taken]
            why = "Payer reference {} in the narration"
        if len(hit) == 1:
            c = hit[0]
            taken.add(str(c["id"]))
            short = round(c["expected"] - cr["amount"], 2)
            out.append({"credit_id": str(cr["id"]), "confidence": "exact",
                        "why": why.format(c.get("nhcx_utr") if why.startswith("UTR") else c["payer_ref"])
                               + (f"; short by ₹{short:,.0f} after TDS" if short > TOL else ""),
                        "lines": [line(c, cr["amount"])], "shortfall": max(0.0, short)})
        else:
            rest.append(cr)

    # 2. likely: same payer, exact expected amount, one claim only. Also accept "no TDS deducted".
    rest2 = []
    for cr in rest:
        pool = [c for c in cases if str(c["id"]) not in taken and (not cr.get("payer_id") or str(c["payer_id"]) == str(cr["payer_id"]))]
        same = [c for c in pool if abs(c["expected"] - cr["amount"]) <= TOL]
        why = "Same payer and the exact expected amount (settled less TDS)"
        if not same:
            same = [c for c in pool if abs(c["settled"] - cr["amount"]) <= TOL]
            why = "Same payer and the full settled amount (payer did not deduct TDS)"
        if len(same) == 1 and cr.get("payer_id"):
            c = same[0]
            taken.add(str(c["id"]))
            out.append({"credit_id": str(cr["id"]), "confidence": "likely", "why": why,
                        "lines": [line(c, cr["amount"])], "shortfall": 0.0})
        else:
            if len(same) > 1:
                cr = {**cr, "_ambiguous": [c["case_number"] for c in same]}
            rest2.append(cr)

    # 3. split: one bulk credit, exactly one combination of the payer's claims adds up to it
    for cr in rest2:
        if not cr.get("payer_id"):
            continue
        pool = [c for c in cases if str(c["id"]) not in taken and str(c["payer_id"]) == str(cr["payer_id"])][:MAX_SPLIT_POOL]
        fits = []
        for k in range(2, min(MAX_SPLIT, len(pool)) + 1):
            for combo in combinations(pool, k):
                if abs(sum(c["expected"] for c in combo) - cr["amount"]) <= TOL * k:
                    fits.append(combo)
                    if len(fits) > 1:
                        break
            if len(fits) > 1:
                break
        if len(fits) == 1:
            combo = fits[0]
            taken.update(str(c["id"]) for c in combo)
            out.append({"credit_id": str(cr["id"]), "confidence": "split",
                        "why": f"One bulk credit; the only combination of {len(combo)} settled claims of this payer that adds up to it",
                        "lines": [line(c, c["expected"]) for c in combo], "shortfall": 0.0})
    return out
