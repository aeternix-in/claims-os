"""Document intake: hash, store the original, read text (PDF text or OCR), sort pages
by type and pull key fields. Every field keeps its provenance.

Pure functions here; storage and DB writes live in documents.py."""
from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass, field

EXTRACTOR_VERSION = "regex-2026.11.0"

DOC_TYPES = {
    "discharge_summary": "Discharge summary", "admission_note": "Admission note", "bill": "Bill",
    "investigation": "Investigation report", "policy_card": "Policy card", "scheme_card": "Scheme card",
    "id_proof": "Photo ID", "ot_notes": "Operation notes", "implant_invoice": "Implant invoice / sticker",
    "icu_chart": "ICU chart", "referral": "Referral letter", "consent": "Consent form",
    "preauth_form": "Pre-auth form", "prescription": "Prescription", "other": "Other",
}

# (doc_type, weight, regex). Highest total score wins; a title match scores most.
CLASSIFIER: list[tuple[str, int, str]] = [
    ("discharge_summary", 10, r"discharge\s+summary"), ("discharge_summary", 3, r"condition at discharge|advice on discharge|course in (the )?hospital"),
    ("admission_note", 8, r"admission\s+(note|record)|initial assessment|case sheet"), ("admission_note", 3, r"chief complaints?|history of present"),
    ("bill", 10, r"(final|interim|itemi[sz]ed|ip)\s+bill"), ("bill", 4, r"bill\s+no|net amount|amount payable|grand total"),
    ("investigation", 6, r"ultrasound|usg|sonography|ct scan|mri|x-?ray|radiology|impression:"),
    ("investigation", 6, r"haematology|hematology|biochemistry|complete blood count|cbc|laboratory|lab report|biometry|angiograph"),
    ("policy_card", 8, r"e-?card|policy (no|number)|member id|health card"), ("policy_card", 3, r"sum insured|valid (till|upto)|tpa"),
    ("scheme_card", 9, r"cghs|ayushman|pm-?jay|echs|dgehs|beneficiary id"),
    ("id_proof", 9, r"aadhaa?r|unique identification|income tax department|permanent account number|passport"),
    ("ot_notes", 10, r"operation\s+notes?|ot\s+notes?|operative\s+(notes?|findings)|procedure\s+notes?"),
    ("implant_invoice", 8, r"implant|intraocular lens|stent|prosthesis"), ("implant_invoice", 4, r"batch\s*no|lot\s*no|serial\s*no|sticker"),
    ("icu_chart", 9, r"icu\s+(chart|notes|flow\s*sheet)|vitals?\s+chart|intensive care"),
    ("referral", 9, r"referral|referred (to|by)|permission letter"),
    ("consent", 9, r"informed consent|consent form"),
    ("preauth_form", 10, r"pre-?authori[sz]ation|request for cashless"),
    ("prescription", 5, r"\brx\b|prescription"),
]


def classify(text: str) -> tuple[str, dict[str, int]]:
    t = text.lower()
    scores: dict[str, int] = {}
    for dtype, w, pat in CLASSIFIER:
        n = len(re.findall(pat, t))
        if n:
            scores[dtype] = scores.get(dtype, 0) + w * min(n, 3)
    if not scores:
        return "other", {}
    return max(scores, key=lambda k: scores[k]), scores


@dataclass
class Field:
    name: str
    value: str
    page: int
    start: int
    end: int
    snippet: str
    method: str = EXTRACTOR_VERSION


DATE = r"(\d{1,2}[\/\-.]\d{1,2}[\/\-.]\d{2,4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})"
AMOUNT = r"(?:rs\.?|inr|₹)?\s*([\d,]+(?:\.\d{1,2})?)"
FIELD_PATTERNS: dict[str, str] = {
    "patient_name": r"(?:patient(?:'s)?\s*name|name\s+of\s+(?:the\s+)?patient|^name)\s*[:\-]\s*(?:mr\.?|mrs\.?|ms\.?|master|baby)?\s*([A-Za-z][A-Za-z .']{1,60}?)(?=\s{2,}|\s*(?:age|sex|uhid|ip|$|\n))",
    "uhid": r"(?:uhid|mrn|reg(?:istration)?\.?\s*no)\s*[:\-]?\s*([A-Z0-9\/-]{4,20})",
    "admission_date": r"(?:date\s+of\s+admission|admission\s+date|doa|admitted\s+on)\s*[:\-]?\s*" + DATE,
    "discharge_date": r"(?:date\s+of\s+discharge|discharge\s+date|dod|discharged\s+on)\s*[:\-]?\s*" + DATE,
    "diagnosis": r"(?:final\s+diagnosis|provisional\s+diagnosis|diagnosis)\s*[:\-]\s*([^\n]{3,120})",
    "icd10": r"\b([A-TV-Z][0-9]{2}(?:\.[0-9]{1,2})?)\b(?=[^\n]{0,40}(?:icd|$))|icd[- ]?10\s*(?:code)?\s*[:\-]?\s*([A-TV-Z][0-9]{2}(?:\.[0-9]{1,2})?)",
    "policy_number": r"policy\s*(?:no|number)\.?\s*[:\-]?\s*([A-Z0-9\/-]{5,30})",
    "total_amount": r"(?:grand\s+total|net\s+amount|amount\s+payable|total\s+amount|bill\s+total)\s*[:\-]?\s*" + AMOUNT,
    "treating_doctor": r"(?:treating\s+doctor|consultant|attending\s+physician|surgeon)\s*[:\-]\s*(dr\.?\s*[A-Za-z .]{2,50}?)(?=\s{2,}|\n|,|$)",
}
KEY_FIELDS = ["patient_name", "admission_date", "diagnosis"]


def extract_fields(pages: list[str]) -> list[Field]:
    out: list[Field] = []
    seen: set[str] = set()
    for pno, text in enumerate(pages, start=1):
        for name, pat in FIELD_PATTERNS.items():
            if name in seen:
                continue
            m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
            if not m:
                continue
            gi = next((i for i in range(1, (m.lastindex or 0) + 1) if m.group(i)), None)
            if gi is None:
                continue
            val = m.group(gi).strip().strip(".,;:")
            if name == "total_amount":
                val = val.replace(",", "")
            if not val:
                continue
            seen.add(name)
            s, e = m.span(gi)
            out.append(Field(name, val, pno, s, e, text[max(0, s - 40): e + 40].replace("\n", " ")))
    return out


@dataclass
class ReadResult:
    pages: list[str]
    method: str               # 'pdf-text' | 'ocr-tesseract' | 'pdf-text+ocr' | 'text'
    doc_type: str
    scores: dict[str, int] = field(default_factory=dict)
    page_types: list[str] = field(default_factory=list)
    fields: list[Field] = field(default_factory=list)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ocr_image(img) -> str:
    import pytesseract
    return pytesseract.image_to_string(img, lang="eng")


def read_document(data: bytes, mime: str, ocr: bool = True) -> ReadResult:
    method = "text"
    if mime == "application/pdf" or data[:5] == b"%PDF-":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        pages = [(p.extract_text() or "") for p in reader.pages]
        method = "pdf-text"
        # scanned PDF: pages with almost no text get OCR'd
        if ocr and any(len(p.strip()) < 20 for p in pages):
            try:
                import subprocess, tempfile, os
                with tempfile.TemporaryDirectory() as td:
                    src = os.path.join(td, "in.pdf")
                    open(src, "wb").write(data)
                    for i, p in enumerate(pages):
                        if len(p.strip()) >= 20:
                            continue
                        subprocess.run(["pdftoppm", "-r", "200", "-f", str(i + 1), "-l", str(i + 1), "-png", src,
                                        os.path.join(td, f"p{i}")], check=True, capture_output=True)
                        png = next(f for f in os.listdir(td) if f.startswith(f"p{i}"))
                        from PIL import Image
                        pages[i] = _ocr_image(Image.open(os.path.join(td, png)))
                method = "pdf-text+ocr"
            except Exception:
                pass
    elif mime.startswith("image/"):
        from PIL import Image
        pages = [_ocr_image(Image.open(io.BytesIO(data)))] if ocr else [""]
        method = "ocr-tesseract"
    else:
        pages = [data.decode("utf-8", errors="replace")]

    page_types = [classify(p)[0] for p in pages]
    doc_type, scores = classify("\n".join(pages))
    return ReadResult(pages, method, doc_type, scores, page_types, extract_fields(pages))
