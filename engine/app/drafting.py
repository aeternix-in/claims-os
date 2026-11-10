"""Query classification and reply drafting from the patient's own record, plus letter text.
Pure functions. A bracketed instruction like [Doctor: ...] marks something a person must
write; a draft that still contains one cannot be sent."""
from __future__ import annotations

import re
from datetime import date, datetime

QUERY_CODES = {
    "Q1": {"name": "Missing document", "doctor": False},
    "Q2": {"name": "Clinical justification", "doctor": True},
    "Q3": {"name": "Past history or pre-existing disease", "doctor": True},
    "Q4": {"name": "Billing clarification", "doctor": False},
    "Q5": {"name": "Identity or policy", "doctor": False},
    "Q6": {"name": "Accident or medico-legal", "doctor": False},
    "Q7": {"name": "Discrepancy", "doctor": True},
    "Q8": {"name": "Investigation", "doctor": False},
}
# checked in order; first match wins
CLASSIFY: list[tuple[str, str]] = [
    ("Q8", r"field (visit|verification)|investigat(or|ion team)|fraud|suspicious|verification visit"),
    ("Q6", r"\bmlc\b|\bfir\b|accident|medico[- ]?legal|police|alcohol|road traffic|\brta\b"),
    ("Q3", r"past history|pre[- ]?existing|\bped\b|duration of (illness|ailment|disease|symptoms)|since when|previous (records|treatment)|first consultation|history of"),
    ("Q2", r"justif|necessity|need for (admission|icu|hospitali[sz]ation)|why (was )?(admission|icu)|extra days|prolonged|line of treatment|indication|day ?care"),
    ("Q5", r"name mismatch|age mismatch|\bkyc\b|id proof|identity|photo id|policy copy|member id|relationship"),
    ("Q7", r"discrepanc|mismatch|signature|conflicting|overwrit|differs?|different dates?"),
    ("Q4", r"break[- ]?up|tariff|package|bill clarification|itemi[sz]|rate list|charges for|justify (the )?(amount|charges)|non[- ]?payable"),
    ("Q1", r"submit|provide|send|attach|report|copy|document|papers|summary|invoice|sticker|notes"),
]
# words in a query -> document types to attach
ASKED_DOCS: list[tuple[str, str]] = [
    ("discharge_summary", r"discharge summary"),
    ("investigation", r"investigation|report|usg|ultrasound|x-?ray|ct\b|mri|lab|histopath|biopsy|angiograph|biometry"),
    ("admission_note", r"indoor case papers|\bicp\b|case sheet|admission note|clinical notes|treatment papers"),
    ("icu_chart", r"icu (chart|notes)|vitals|tpr chart|flow ?sheet"),
    ("ot_notes", r"ot notes|operation notes|operative|procedure notes"),
    ("implant_invoice", r"implant|sticker|invoice|lens|stent"),
    ("bill", r"\bbill\b|break[- ]?up|itemi[sz]ed"),
    ("id_proof", r"id proof|photo id|aadhaa?r|\bkyc\b|pan card"),
    ("policy_card", r"policy copy|e-?card|policy card"),
    ("consent", r"consent"),
    ("referral", r"referral|permission letter"),
]
DOC_LABEL = {"discharge_summary": "Discharge summary", "investigation": "Investigation report(s)",
             "admission_note": "Indoor case papers / admission note", "icu_chart": "ICU chart", "ot_notes": "Operation notes",
             "implant_invoice": "Implant invoice and sticker", "bill": "Itemised bill", "id_proof": "Photo ID",
             "policy_card": "Policy card", "consent": "Consent form", "referral": "Referral letter"}
PLACEHOLDER = re.compile(r"\[[^\]\n]{3,}\]")
STAGE_LABEL = {"preauth": "pre-authorisation", "discharge": "final authorisation", "claim": "claim"}


def classify(text: str) -> str:
    t = text.lower()
    for code, pat in CLASSIFY:
        if re.search(pat, t):
            return code
    return "Q1"


def asked_types(text: str) -> list[str]:
    t = text.lower()
    return [d for d, pat in ASKED_DOCS if re.search(pat, t)]


def placeholders(text: str) -> list[str]:
    return PLACEHOLDER.findall(text or "")


def _impressions(docs: list[dict]) -> list[str]:
    out = []
    for d in docs:
        if d["doc_type"] != "investigation":
            continue
        m = re.search(r"impression\s*[:\-]\s*([^\n]{3,200})", d.get("text") or "", re.I)
        if m:
            out.append(f"{d['filename']}: {m.group(1).strip()}")
    return out


def _d(x) -> str:
    if isinstance(x, datetime):
        x = x.date()
    return x.strftime("%d %b %Y") if isinstance(x, date) else "—"


def draft_reply(code: str, query_text: str, stage: str, ctx: dict, docs: list[dict], bill_lines: list[dict],
                audit: dict | None) -> dict:
    """ctx: case_number, patient, policy_number, payer, hospital, dx, icd10, procedure, admitted_at, discharged_at, signatory"""
    wanted = asked_types(query_text)
    if code == "Q4" and "bill" not in wanted:
        wanted.append("bill")
    if code == "Q5":
        wanted += [t for t in ("id_proof", "policy_card") if t not in wanted]
    if code in ("Q2", "Q3") and "admission_note" not in wanted:
        wanted.append("admission_note")
    if code == "Q2" and "investigation" not in wanted:
        wanted.append("investigation")
    by_type: dict[str, list[dict]] = {}
    for d in docs:
        by_type.setdefault(d["doc_type"], []).append(d)
    attach = [d for t in wanted for d in by_type.get(t, [])]
    missing = [t for t in wanted if t not in by_type]

    head = [f"Subject: Reply to {STAGE_LABEL[stage]} query, case {ctx['case_number']}",
            f"Patient: {ctx['patient']}   Policy no: {ctx.get('policy_number') or '—'}   Payer: {ctx.get('payer') or '—'}",
            "", "Dear Sir/Madam,", "", f"With reference to your query: \"{query_text.strip()}\"", ""]
    body: list[str] = []
    impressions = _impressions(docs)

    if code == "Q1":
        body.append("Please find the requested documents attached." if attach else "")
    elif code == "Q2":
        body += ["Clinical summary from the patient's record:",
                 f"- Diagnosis: {ctx.get('dx') or '—'}" + (f" (ICD-10 {ctx['icd10']})" if ctx.get("icd10") else ""),
                 f"- Admitted on {_d(ctx.get('admitted_at'))}" + (f", procedure: {ctx['procedure']}" if ctx.get("procedure") else "")]
        body += [f"- {i}" for i in impressions] or ["- Investigations: [Desk: attach the report that supports the diagnosis]"]
        body += ["", "[Doctor: state in two or three lines why inpatient care (or ICU / the extra days) was medically "
                 "necessary, citing the findings above]"]
    elif code == "Q3":
        body += [f"Diagnosis: {ctx.get('dx') or '—'}.",
                 "[Doctor: state the duration of the present illness and date of first consultation as recorded, and "
                 "whether there is any history of this or a related condition]",
                 "Relevant records from this admission are attached."]
    elif code == "Q4":
        body.append("Bill breakup by head:")
        heads: dict[str, float] = {}
        for l in bill_lines:
            heads[l["category"]] = heads.get(l["category"], 0) + float(l["qty"]) * float(l["rate"])
        names = {"room": "Room and nursing", "icu": "ICU", "ot": "OT", "surgeon": "Surgeon", "anaesthesia": "Anaesthetist",
                 "consult": "Consultant visits", "procedure": "Procedure", "investigations": "Diagnostics",
                 "pharmacy": "Pharmacy", "consumables": "Consumables", "implants": "Implants", "misc": "Other",
                 "nonpayable": "Non-medical items (patient pays)"}
        for k, v in heads.items():
            body.append(f"- {names.get(k, k)}: Rs {v:,.0f}")
        if audit:
            if audit.get("package_rate") is not None:
                body.append(f"The agreed package rate of Rs {audit['package_rate']:,.0f} has been applied.")
            elif audit.get("discount"):
                body.append(f"The agreed discount of Rs {audit['discount']:,.0f} has been applied.")
            body.append(f"Amount claimed after these adjustments: Rs {audit['expected_payable']:,.0f}.")
        body.append("The itemised bill is attached.")
    elif code == "Q5":
        body += [f"The patient's name as per photo ID is {ctx['patient']}. Copies of the photo ID and policy card are attached.",
                 "[Desk: if the query names a specific mismatch, explain it here]"]
    elif code == "Q6":
        body += ["[Desk: attach the MLC / FIR copy if one exists. If none: \"This is not a medico-legal case; no MLC was "
                 "registered.\" and have the treating doctor confirm]"]
    elif code == "Q7":
        body += ["[Desk: describe the discrepancy and the correction. Attach the corrected page signed and dated by the "
                 "person who made the entry]"]
    elif code == "Q8":
        body += ["[Desk head: this query concerns an investigation or verification. Decide the response personally; "
                 "do not send an automated reply]"]

    for t in missing:
        body.append(f"[Missing on file: {DOC_LABEL.get(t, t)}. Upload it, or state that it does not exist]")
    if attach:
        body += ["", "Attachments:"] + [f"{i}. {d['filename']} ({DOC_LABEL.get(d['doc_type'], d['doc_type'])})"
                                        for i, d in enumerate(attach, 1)]
    foot = ["", "Regards,", ctx.get("signatory") or "Insurance desk", ctx.get("hospital") or ""]
    text = "\n".join(head + [b for b in body if b is not None] + foot).replace("\n\n\n", "\n\n")
    return {"draft": text, "attachments": [str(d["id"]) for d in attach], "missing": missing,
            "requires_signoff": QUERY_CODES[code]["doctor"], "placeholders": placeholders(text)}


# ---------------- letters ----------------
SRC = {
    "mou": "Payment term in the hospital–payer MoU (payer rulebook)",
    "ppi": "IRDAI (Protection of Policyholders' Interests and Allied Matters of Insurers) Regulations, 2024 – grievance redressal by the insurer. UNVERIFIED for hospital-raised cashless disputes",
    "ombudsman": "Insurance Ombudsman Rules, 2017 – escalation if the insurer does not resolve the grievance. UNVERIFIED applicability to hospitals",
    "nonpay": "IRDAI list of non-payable items (Master Circular on Health Insurance Business, 2024)",
    "pd": "IRDAI guidelines on proportionate deduction, June 2020",
    "194j": "Section 194J, CBDT Circular 8/2009 (TDS 10%)",
}


def letter(kind: str, ctx: dict, facts: dict) -> dict:
    """facts: days, amount, settled, expected, received, tds, reason, window, deductions (list of str)"""
    cn, pt, payer = ctx["case_number"], ctx["patient"], ctx.get("payer") or "the payer"
    ref = f"Case {cn} | Patient {pt} | Policy {ctx.get('policy_number') or '—'} | Discharged {_d(ctx.get('discharged_at'))}"
    sign = ["", "Regards,", ctx.get("signatory") or "Credit control / Insurance desk", ctx.get("hospital") or ""]
    if kind == "reminder":
        subj = f"Reminder: settlement pending for case {cn}"
        lines = [f"The claim for the above case was submitted {facts['days']} days ago with all documents. The agreed payment "
                 f"term is {facts['term']} days. We request settlement of Rs {facts['amount']:,.0f} at the earliest, or the "
                 "reason for the delay."]
        src = [SRC["mou"]]
    elif kind == "grievance":
        subj = f"Grievance: settlement delayed {facts['days']} days, case {cn}"
        lines = [f"Despite our reminder, the claim submitted {facts['days']} days ago remains unsettled, against an agreed "
                 f"term of {facts['term']} days. We raise this as a formal grievance and request settlement of "
                 f"Rs {facts['amount']:,.0f} with a written reply.",
                 "If this is not resolved, we will escalate as provided under the applicable IRDAI regulations."]
        src = [SRC["mou"], SRC["ppi"], SRC["ombudsman"]]
    elif kind == "dispute":
        subj = f"Dispute of short payment, case {cn}"
        lines = [f"Against an admissible amount of Rs {facts['expected']:,.0f} as per the policy terms and our agreed tariff, "
                 f"Rs {facts['settled']:,.0f} has been settled, a shortfall of Rs {facts['amount']:,.0f}."]
        if facts.get("deductions"):
            lines += ["Deductions we dispute:"] + [f"- {d}" for d in facts["deductions"]]
        lines += ["[Desk: give the ground for each disputed deduction, citing the record or the MoU]",
                  "We request reconsideration and payment of the balance."]
        src = [SRC["nonpay"], SRC["pd"], SRC["mou"]]
    elif kind == "appeal":
        subj = f"Appeal against rejection, case {cn}"
        lines = [f"The claim was rejected on the ground: \"{facts.get('reason') or '—'}\".",
                 "[Doctor / desk: state why the rejection is not justified, citing the clinical record and the policy clause]",
                 "We request a review of the decision. Supporting records are enclosed."]
        src = [SRC["ppi"]]
    elif kind == "condonation":
        subj = f"Request to condone delay in claim submission, case {cn}"
        lines = [f"The claim file is being submitted {facts['days']} days after discharge, beyond the {facts['window']}-day window.",
                 "[Desk: state the reason for the delay]",
                 "The delay was not due to any lapse by the insured. We request that it be condoned and the claim processed on merit."]
        src = [SRC["mou"]]
    else:
        raise ValueError(kind)
    body = "\n".join([f"To: {payer}", f"Ref: {ref}", "", "Dear Sir/Madam,", ""] + lines + sign)
    return {"subject": subj, "body": body, "sources": src}
