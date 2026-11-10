from datetime import datetime, timezone

import pytest

from app import drafting as dr


@pytest.mark.parametrize("text,code", [
    ("Kindly submit USG abdomen report and indoor case papers", "Q1"),
    ("Please justify the need for ICU admission and extra days", "Q2"),
    ("Provide duration of ailment and first consultation papers", "Q3"),
    ("Send itemised bill breakup; OT charges exceed tariff", "Q4"),
    ("Name mismatch between policy and ID proof, submit KYC", "Q5"),
    ("Is this an RTA case? Submit MLC/FIR copy", "Q6"),
    ("Date of admission differs between discharge summary and bill", "Q7"),
    ("Claim is under investigation; field verification visit scheduled", "Q8"),
])
def test_classify(text, code):
    assert dr.classify(text) == code


CTX = {"case_number": "AX-2611-0007", "patient": "Ravi Kumar", "policy_number": "IC-1", "payer": "Insurer C",
       "hospital": "Demo Hospital", "dx": "Cholelithiasis", "icd10": "K80.2", "procedure": "Laparoscopic cholecystectomy",
       "admitted_at": datetime(2026, 11, 8, tzinfo=timezone.utc), "discharged_at": None, "signatory": "Priya S."}
DOCS = [{"id": "d1", "doc_type": "investigation", "filename": "usg.pdf", "text": "ULTRASOUND\nImpression: Cholelithiasis."},
        {"id": "d2", "doc_type": "admission_note", "filename": "adm.pdf", "text": "Admission note"}]


def test_q1_attaches_what_exists_and_flags_what_is_missing():
    d = dr.draft_reply("Q1", "Submit USG report and implant sticker", "claim", CTX, DOCS, [], None)
    assert d["attachments"] == ["d1"]
    assert d["missing"] == ["implant_invoice"]
    assert any("Implant invoice" in p for p in d["placeholders"])     # cannot be sent until resolved
    assert not d["requires_signoff"]


def test_q1_complete_has_no_placeholders():
    d = dr.draft_reply("Q1", "Submit USG report", "preauth", CTX, DOCS, [], None)
    assert d["placeholders"] == [] and "usg.pdf" in d["draft"]


def test_q2_pulls_record_and_needs_doctor():
    d = dr.draft_reply("Q2", "Justify need for admission", "preauth", CTX, DOCS, [], None)
    assert d["requires_signoff"]
    assert "usg.pdf: Cholelithiasis." in d["draft"] and "K80.2" in d["draft"]
    assert any(p.startswith("[Doctor:") for p in d["placeholders"])


def test_q4_bill_breakup():
    lines = [{"category": "room", "qty": 2, "rate": 6500}, {"category": "ot", "qty": 1, "rate": 22000}]
    d = dr.draft_reply("Q4", "Send bill breakup", "claim", CTX, DOCS, lines, {"expected_payable": 30000, "discount": 1000, "package_rate": None})
    assert "Room and nursing: Rs 13,000" in d["draft"] and "Rs 30,000" in d["draft"]


def test_q8_never_auto():
    d = dr.draft_reply("Q8", "field verification", "claim", CTX, DOCS, [], None)
    assert d["placeholders"]


def test_letters():
    L = dr.letter("reminder", CTX, {"days": 41, "term": 30, "amount": 56000})
    assert "41 days" in L["body"] and not dr.placeholders(L["body"]) and L["sources"]
    L = dr.letter("dispute", CTX, {"expected": 60000, "settled": 54000, "amount": 6000, "deductions": []})
    assert dr.placeholders(L["body"])   # desk must give the ground before approval
    for k, f in [("grievance", {"days": 50, "term": 30, "amount": 1}), ("appeal", {"reason": "PED"}), ("condonation", {"days": 20, "window": 15})]:
        assert dr.letter(k, CTX, f)["subject"]
