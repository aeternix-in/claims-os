"""Document reading and the five demo packs (green; five red; red + clinical amber; unconfirmed; amber)."""
from datetime import datetime, timezone

import pytest

from app import intake, scrubber
from tests.sample_docs import pdf, png


def test_reads_and_classifies_pdfs():
    expect = {"policy_card": "policy_card", "id_proof": "id_proof", "admission_note": "admission_note",
              "usg": "investigation", "discharge_summary": "discharge_summary", "ot_notes": "ot_notes", "bill": "bill"}
    for kind, dtype in expect.items():
        r = intake.read_document(pdf(kind), "application/pdf")
        assert r.method == "pdf-text"
        assert r.doc_type == dtype, (kind, r.doc_type, r.scores)


def test_extracts_fields_with_provenance():
    r = intake.read_document(pdf("admission_note"), "application/pdf")
    f = {x.name: x for x in r.fields}
    assert f["patient_name"].value == "Ravi Kumar"
    assert f["admission_date"].value == "08/11/2026"
    assert f["diagnosis"].value.startswith("Symptomatic cholelithiasis")
    assert f["icd10"].value == "K80.2"
    assert f["uhid"].value == "UH204511"
    assert f["patient_name"].page == 1 and "Ravi Kumar" in f["patient_name"].snippet
    assert f["patient_name"].method == intake.EXTRACTOR_VERSION
    b = {x.name: x.value for x in intake.read_document(pdf("bill"), "application/pdf").fields}
    assert b["total_amount"] == "76800"


def test_ocr_photo():
    r = intake.read_document(png("usg"), "image/png")
    assert r.method == "ocr-tesseract"
    assert r.doc_type == "investigation"
    assert "cholelithiasis" in r.pages[0].lower()


# ---------- scrubber: five demo packs ----------
CASE = {"procedure_id": "lapchole", "icd10": "K80.2", "admitted_at": datetime(2026, 11, 8, tzinfo=timezone.utc),
        "discharged_at": None}


def docs_and_fields(kinds, confirm=True):
    docs, fields = [], []
    for i, k in enumerate(kinds):
        r = intake.read_document(pdf(k), "application/pdf")
        docs.append({"id": f"d{i}", "doc_type": r.doc_type, "filename": f"{k}.pdf", "text": "\n".join(r.pages)})
        for j, f in enumerate(r.fields):
            fields.append({"id": f"f{i}{j}", "document_id": f"d{i}", "name": f.name, "value": f.value,
                           "page": f.page, "confirmed": confirm, "snippet": f.snippet})
    return docs, fields


def check(kinds, confirm=True, audit=None, clear=None):
    docs, fields = docs_and_fields(kinds, confirm)
    res = scrubber.run_checks("preauth", CASE, "insurer", docs, fields, audit, {}, "Ravi Kumar")
    return scrubber.summarise(res, clear or {})


FULL = ["policy_card", "id_proof", "admission_note", "usg"]


def test_pack_green():
    s = check(FULL)
    assert s["status"] == "green" and not s["blocked"], [r for r in s["results"] if r["level"] != "green"]


def test_pack_five_red():
    s = check([])
    assert s["red"] == 4  # four required preauth documents missing
    s = check(["usg_other_name"] + FULL[:3])
    assert s["status"] == "red"
    assert any(r["rule_id"] == "fields.name_match" and r["level"] == "red" for r in s["results"])


def test_pack_red_plus_clinical_amber():
    s = check(["policy_card", "admission_note", "usg_normal"])
    ids = {r["rule_id"]: r for r in s["results"]}
    assert ids["doc.id_proof"]["level"] == "red"
    assert ids["clinical.usg_gallstones"]["level"] == "amber" and ids["clinical.usg_gallstones"]["clinical"]


def test_pack_unconfirmed():
    s = check(FULL, confirm=False)
    assert s["status"] == "red"
    assert any(r["rule_id"] == "fields.unconfirmed" and r["level"] == "red" for r in s["results"])


def test_pack_amber_then_cleared():
    audit = {"nonpayable_to_payer": 800, "pd_applies": False, "above_mou": 0, "unauthorised_excess": 0}
    s = check(FULL, audit=audit)
    assert s["status"] == "amber" and s["blocked"]
    s = check(FULL, audit=audit, clear={"bill.nonpayable": {"note": "Family told; collected at counter"}})
    assert s["status"] == "green" and not s["blocked"] and s["amber_cleared"] == 1


def test_every_rule_cites_a_source():
    s = check([], confirm=False)
    assert all(r["source"] for r in s["results"] if r["level"] != "green")
