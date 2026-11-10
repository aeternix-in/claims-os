"""Synthetic hospital documents for tests and demos. No real patient data."""
from fpdf import FPDF

DOCS = {
    "policy_card": ["HEALTH E-CARD", "Insurer C Health Insurance  |  TPA Two", "Member Name: Ravi Kumar",
                    "Policy No: IC-2026-778812", "Member ID: TT88231", "Sum Insured: Rs 3,00,000", "Valid till: 31/03/2027"],
    "id_proof": ["GOVERNMENT OF INDIA", "Unique Identification Authority of India", "Aadhaar", "Name: Ravi Kumar",
                 "XXXX XXXX 4821"],
    "admission_note": ["ADMISSION NOTE", "Patient Name: Ravi Kumar   Age: 46  Sex: M", "UHID: UH204511",
                       "Date of Admission: {adm}", "Chief complaints: right upper abdominal pain for 3 months, worse after meals",
                       "History of present illness: recurrent biliary colic, two episodes in last month",
                       "Provisional Diagnosis: Symptomatic cholelithiasis", "ICD-10: K80.2",
                       "Plan: Laparoscopic cholecystectomy under GA", "Treating Doctor: Dr. A. Mehta"],
    "usg": ["DEPARTMENT OF RADIOLOGY", "ULTRASOUND ABDOMEN", "Patient Name: Ravi Kumar", "Findings: Gall bladder distended,",
            "multiple calculi seen, largest 14 mm. Wall thickness normal.", "Impression: Cholelithiasis."],
    "usg_normal": ["DEPARTMENT OF RADIOLOGY", "ULTRASOUND ABDOMEN", "Patient Name: Ravi Kumar",
                   "Findings: Liver, spleen and kidneys normal.", "Impression: No significant abnormality."],
    "usg_other_name": ["DEPARTMENT OF RADIOLOGY", "ULTRASOUND ABDOMEN", "Patient Name: Rakesh Verma",
                       "Impression: Cholelithiasis."],
    "discharge_summary": ["DISCHARGE SUMMARY", "Patient Name: Ravi Kumar", "Date of Admission: {adm}",
                          "Date of Discharge: {dis}", "Final Diagnosis: Cholelithiasis", "ICD-10: K80.2",
                          "Course in hospital: Underwent laparoscopic cholecystectomy. Uneventful recovery.",
                          "Condition at discharge: stable", "Treating Doctor: Dr. A. Mehta"],
    "ot_notes": ["OPERATION NOTES", "Patient Name: Ravi Kumar", "Procedure: Laparoscopic cholecystectomy",
                 "Operative findings: distended gall bladder with multiple calculi", "Surgeon: Dr. A. Mehta"],
    "bill": ["FINAL BILL", "Bill No: B/26/1189", "Patient Name: Ravi Kumar", "Room charges  13000", "OT charges  22000",
             "Surgeon fees  32000", "Grand Total: Rs 76,800"],
}


def pdf(kind: str, adm: str = "08/11/2026", dis: str = "10/11/2026") -> bytes:
    p = FPDF()
    p.add_page()
    p.set_font("Helvetica", size=12)
    for line in DOCS[kind]:
        p.cell(0, 8, line.format(adm=adm, dis=dis), new_x="LMARGIN", new_y="NEXT")
    return bytes(p.output())


def png(kind: str) -> bytes:
    """A 'photo' of a document: text rendered to an image, so it must be OCR'd."""
    import io
    from PIL import Image, ImageDraw, ImageFont
    lines = DOCS[kind]
    img = Image.new("RGB", (1400, 80 + 50 * len(lines)), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 32)
    except OSError:
        font = ImageFont.load_default(size=32)
    for i, l in enumerate(lines):
        d.text((40, 40 + 50 * i), l, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
