"""Files for the live UI's Documents tab: upload (stored privately, text and key fields read),
open, and remove (the record is kept; it is only marked removed)."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from psycopg.types.json import Jsonb

from . import intake, storage
from .auth import User, current_user
from .db import tx

router = APIRouter(prefix="/ui/files", tags=["ui"])

MAX_BYTES = 20 * 1024 * 1024
ALLOWED = {"application/pdf", "image/png", "image/jpeg", "image/jpg", "image/tiff"}
UPLOAD_ROLES = {"desk", "doctor", "accounts", "admin", "owner"}
FIELD_LABELS = {"patient_name": "patient", "policy_number": "policy no.", "member_id": "member ID", "uhid": "UHID",
                "ip_number": "IP no.", "admission_date": "admitted", "discharge_date": "discharged",
                "diagnosis": "diagnosis", "icd10": "ICD-10", "bill_total": "bill total", "utr": "UTR"}


def _claim_exists(c, user: User, claim_id: str) -> None:
    if not c.execute("select 1 from ui_claims where hospital_id=%s and id=%s", (user.hospital_id, claim_id)).fetchone():
        raise HTTPException(404, "Claim not found")


@router.post("", status_code=201)
async def upload(claim_id: str = Form(...), doc_id: str = Form(...), file: UploadFile = File(...),
                 user: User = Depends(current_user)):
    if user.role not in UPLOAD_ROLES:
        raise HTTPException(403, "Your role cannot upload documents")
    if not doc_id.startswith(claim_id):
        raise HTTPException(422, "The document does not belong to this claim")
    data = await file.read()
    if not data:
        raise HTTPException(422, "The file is empty")
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "File is larger than 20 MB")
    mime = (file.content_type or "").lower()
    if data[:5] == b"%PDF-":
        mime = "application/pdf"
    if mime not in ALLOWED:
        raise HTTPException(415, f"Unsupported file type {mime or 'unknown'}: upload a PDF, PNG, JPEG or TIFF")
    with tx() as c:
        _claim_exists(c, user, claim_id)

    digest = intake.sha256(data)
    try:
        result = intake.read_document(data, mime)          # OCR can be slow; outside the transaction
    except Exception:                                      # an unreadable file is still stored
        result = intake.ReadResult(pages=[], method="unreadable", doc_type="other")
    path = f"{user.hospital_id}/ui/{digest[:2]}/{digest}"
    storage.put(path, data, mime)
    fields = [f.__dict__ for f in result.fields]
    with tx() as c:
        row = c.execute("""insert into ui_files (hospital_id, claim_id, doc_id, sha256, storage_path, filename, mime, size_bytes,
                           pages, read_method, doc_type, text, fields, uploaded_by)
                           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           returning id, filename, mime, size_bytes, pages, read_method, doc_type, created_at""",
                        (user.hospital_id, claim_id, doc_id, digest, path, file.filename or "file", mime, len(data),
                         len(result.pages), result.method, result.doc_type, "\f".join(result.pages)[:500_000],
                         Jsonb(fields), user.id)).fetchone()
    summary = ", ".join(f"{FIELD_LABELS.get(f['name'], f['name'])} {f['value']}" for f in fields[:4])
    return {"file": row, "fields": fields, "summary": summary}


def _latest(c, user: User, doc_id: str):
    return c.execute("""select * from ui_files where hospital_id=%s and doc_id=%s and removed_at is null
                        order by created_at desc limit 1""", (user.hospital_id, doc_id)).fetchone()


@router.get("/by-doc/{doc_id}")
def open_file(doc_id: str, user: User = Depends(current_user)):
    with tx() as c:
        f = _latest(c, user, doc_id)
    if not f:
        raise HTTPException(404, "No file stored for this document")
    url = storage.signed_url(f["storage_path"])
    if url:
        return {"url": url, "mime": f["mime"], "filename": f["filename"]}
    return Response(storage.get_local(f["storage_path"]), media_type=f["mime"],
                    headers={"Content-Disposition": f'inline; filename="{f["filename"]}"'})


@router.delete("/by-doc/{doc_id}")
def remove(doc_id: str, user: User = Depends(current_user)):
    if user.role not in UPLOAD_ROLES:
        raise HTTPException(403, "Your role cannot remove documents")
    with tx() as c:
        n = c.execute("""update ui_files set removed_at=now(), removed_by=%s
                         where hospital_id=%s and doc_id=%s and removed_at is null""",
                      (user.id, user.hospital_id, doc_id)).rowcount
    return {"removed": n}
