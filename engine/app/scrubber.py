"""Pre-flight scrubber: checks a claim the way the payer will, before it leaves.

red   = blocks submission
amber = needs a named person's note to clear (clinical ambers need a doctor); a cleared amber
        stays cleared on the next run
green = passed

Pure: takes plain dicts, returns results. Persistence is in checks.py."""
from __future__ import annotations

import re
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

import yaml

from . import adjudicate as adj

RULES_DIR = Path(__file__).resolve().parent.parent / "rules"
STAGE_FOR_EVENT = {"submit_preauth": "preauth", "request_final_auth": "discharge", "submit_claim": "claim"}


@lru_cache
def pack(kind: str) -> dict:
    name = "scheme" if kind == "scheme" else "insurer"
    return yaml.safe_load((RULES_DIR / "packs" / f"{name}.yaml").read_text())


@lru_cache
def clinical() -> dict:
    return yaml.safe_load((RULES_DIR / "clinical.yaml").read_text())


def _r(rule_id, level, title, detail="", source="", fix="", clinical_=False, evidence=None):
    return {"rule_id": rule_id, "level": level, "title": title, "detail": detail, "source": source,
            "fix": fix, "clinical": clinical_, "evidence": evidence or []}


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())


def _parse_date(s: str) -> date | None:
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def run_checks(stage: str, case: dict, payer_kind: str, docs: list[dict], fields: list[dict],
               audit: dict | None, rules: dict, patient_name: str, today: date | None = None) -> list[dict]:
    """
    case:   cases row (+ procedure_id, room_category, admitted_at, discharged_at, icd10, provisional_dx)
    docs:   [{id, doc_type, filename, text}]
    fields: [{id, document_id, name, value, page, confirmed, snippet}]
    audit:  adjudicate() output for the current bill, or None
    rules:  payer rulebook (claim_file_window_days ...)
    """
    today = today or date.today()
    p = pack(payer_kind)
    cl = clinical()
    proc = cl["procedures"].get(case.get("procedure_id") or "")
    out: list[dict] = []
    have = {d["doc_type"] for d in docs}

    # 1. required documents
    req = list(p["required_documents"].get(stage, []))
    if stage == "claim":
        if proc and proc.get("surgical"):
            req += p["required_documents"].get("claim_if_surgical", [])
        implants_billed = bool(audit and any(c == "implants" for c in (audit.get("_categories") or [])))
        if implants_billed or (proc and proc.get("implants")):
            req += p["required_documents"].get("claim_if_implants", [])
    for d in req:
        ok = d["type"] in have or (d["type"] == "admission_note" and "preauth_form" in have)
        out.append(_r(f"doc.{d['type']}", "green" if ok else "red",
                      f"{d['label']}: {'attached' if ok else 'missing'}", source=d["source"],
                      fix="" if ok else "Upload it on the Documents tab"))

    # 2. key fields must be confirmed by a person, and must match the case
    by_name: dict[str, list[dict]] = {}
    for f in fields:
        by_name.setdefault(f["name"], []).append(f)
    unconfirmed = [f for f in fields if f["name"] in ("patient_name", "admission_date", "diagnosis") and not f["confirmed"]]
    if unconfirmed:
        out.append(_r("fields.unconfirmed", "red", f"{len(unconfirmed)} key field(s) read from documents not yet confirmed",
                      detail=", ".join(sorted({f['name'].replace('_', ' ') for f in unconfirmed})),
                      source="Aeternix rule D2: a person confirms every key field before it reaches a payer",
                      fix="Confirm or correct them on the Documents tab",
                      evidence=[{"field_id": f["id"], "document_id": f["document_id"], "page": f["page"]} for f in unconfirmed]))
    elif fields:
        out.append(_r("fields.unconfirmed", "green", "Key fields confirmed"))

    names = {_norm(f["value"]) for f in by_name.get("patient_name", [])}
    if names:
        target = _norm(patient_name)
        bad = [n for n in names if n and target and n not in target and target not in n]
        out.append(_r("fields.name_match", "red" if bad else "green",
                      "Patient name differs between documents and the case" if bad else "Patient name matches across documents",
                      detail="; ".join(f["value"] for f in by_name["patient_name"]) if bad else "",
                      source="Payer identity check; mismatches are a common query reason",
                      fix="Correct the document or the case before submitting" if bad else ""))

    adm_dates = {_parse_date(f["value"]) for f in by_name.get("admission_date", [])} - {None}
    case_adm = case.get("admitted_at")
    if adm_dates and case_adm:
        cad = case_adm.date() if isinstance(case_adm, datetime) else case_adm
        off = [d for d in adm_dates if abs((d - cad).days) > 1]
        out.append(_r("fields.admission_date", "amber" if off else "green",
                      "Admission date in documents differs from the case" if off else "Admission date consistent",
                      detail=", ".join(d.isoformat() for d in off), source="Payer date checks",
                      fix="Check which date is right and correct the other" if off else ""))

    # 3. coding
    if stage in ("preauth", "claim") and not case.get("icd10") and not by_name.get("icd10"):
        out.append(_r("coding.icd10", "amber", "No ICD-10 code on the case",
                      source="IRDAI standard pre-auth and claim forms ask for ICD-10", fix="Add the ICD-10 code"))

    # 4. money
    if audit:
        if audit.get("nonpayable_to_payer"):
            out.append(_r("bill.nonpayable", "amber", f"₹{audit['nonpayable_to_payer']:,.0f} of non-medical items billed to the payer",
                          detail="They will be cut. Collect from the family or move them to the family's bill.",
                          source="IRDAI list of non-payable items (Master Circular 2024, annexure)",
                          fix="Untick 'To payer' on those lines, or note that the family has been told"))
        if audit.get("pd_applies"):
            out.append(_r("bill.room_proportion", "amber", f"Room-rent proportionate cut of ₹{audit['pd']:,.0f}",
                          detail=audit.get("pd_note", ""), source="IRDAI guidelines on proportionate deduction, June 2020",
                          fix="Confirm the family was told at admission"))
        if audit.get("above_mou"):
            out.append(_r("bill.above_mou", "amber", f"₹{audit['above_mou']:,.0f} charged above the MoU rate",
                          source="Hospital–payer MoU tariff", fix="Bill at the MoU rate or note why not"))
        if stage == "discharge" and audit.get("unauthorised_excess"):
            out.append(_r("bill.enhancement", "red", f"Bill exceeds approval by ₹{audit['unauthorised_excess']:,.0f}",
                          source="Final authorisation is capped by what was pre-authorised plus enhancements",
                          fix="Raise an enhancement with justification first"))

    # 5. clinical fit: the record must justify the bill (doctor clears these)
    if proc and stage in ("preauth", "claim", "discharge"):
        for ev in proc["evidence"]:
            hits = [d for d in docs if d["doc_type"] in ev["in_types"]
                    and any(k in (d.get("text") or "").lower() for k in ev["any_keywords"])]
            out.append(_r(f"clinical.{ev['id']}", "green" if hits else "amber",
                          f"{ev['label']}: {'found' if hits else 'not found in the record'}",
                          detail=f"For {proc['name']}" + (f" (in {hits[0]['filename']})" if hits else ""),
                          source=f"Aeternix clinical rules {cl['version']} (draft, pending clinical review)",
                          fix="" if hits else "Attach the report, or the treating doctor notes why it is not needed",
                          clinical_=True, evidence=[{"document_id": h["id"]} for h in hits]))
    if audit and audit.get("_icu_days", 0) > 0 and "icu_chart" not in have and stage in ("discharge", "claim"):
        out.append(_r("clinical.icu_chart", "amber", f"{audit['_icu_days']:g} ICU day(s) billed without ICU notes or vitals chart",
                      source=cl["general"]["icu_needs_chart"]["source"], fix="Attach the daily ICU chart",
                      clinical_=True))
    if proc and case.get("discharged_at") and case.get("admitted_at") and stage == "claim":
        los = (case["discharged_at"] - case["admitted_at"]).days
        lim = proc["expected_los_days"] + cl["general"]["los_tolerance_days"]
        if los > lim:
            out.append(_r("clinical.los", "amber", f"Stay of {los} days against about {proc['expected_los_days']} expected",
                          source=f"Aeternix clinical rules {cl['version']}", clinical_=True,
                          fix="Treating doctor notes the reason for the longer stay in the discharge summary"))

    # 6. deadlines
    if stage == "claim" and case.get("discharged_at"):
        window = rules.get("claim_file_window_days") or p["deadlines"]["claim_file_days_after_discharge"]["default"]
        dd = case["discharged_at"].date() if isinstance(case["discharged_at"], datetime) else case["discharged_at"]
        days = (today - dd).days
        if days > window:
            out.append(_r("deadline.claim_file", "amber", f"Filing is {days - window} day(s) late ({days} since discharge, window {window})",
                          source=p["deadlines"]["claim_file_days_after_discharge"]["source"],
                          fix="Attach a delay explanation / condonation request"))
        else:
            out.append(_r("deadline.claim_file", "green", f"Within the filing window ({window - days} day(s) left)"))
    return out


def summarise(results: list[dict], clearances: dict[str, dict]) -> dict:
    """Apply clearances; decide whether submission is blocked."""
    for r in results:
        c = clearances.get(r["rule_id"])
        r["cleared"] = bool(c) and r["level"] == "amber"
        r["clearance"] = c if r["cleared"] else None
    red = [r for r in results if r["level"] == "red"]
    open_amber = [r for r in results if r["level"] == "amber" and not r["cleared"]]
    return {"results": results, "red": len(red), "amber_open": len(open_amber),
            "amber_cleared": sum(1 for r in results if r["cleared"]),
            "green": sum(1 for r in results if r["level"] == "green"),
            "blocked": bool(red or open_amber),
            "status": "red" if red else "amber" if open_amber else "green"}
