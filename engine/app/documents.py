"""Document upload, listing, re-sorting and field confirmation."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from . import intake, storage
from .auth import User, current_user
from .db import tx

router = APIRouter(tags=["documents"])

MAX_BYTES = 20 * 1024 * 1024
ALLOWED = {"application/pdf", "image/png", "image/jpeg", "image/jpg", "image/tiff", "text/plain"}
UPLOAD_ROLES = {"desk", "doctor", "accounts", "admin"}


def _event(c, user: User, case: dict, event: str, payload: dict):
    c.execute("""insert into case_events (hospital_id, case_id, event, from_stage, to_stage, actor_id, actor_role, payload)
                 values (%s,%s,%s,%s,%s,%s,%s,%s)""",
              (user.hospital_id, case["id"], event, case["stage"], case["stage"], user.id, user.role, Jsonb(payload)))


def _case(c, case_id: str, user: User) -> dict:
    case = c.execute("select id, stage from cases where id=%s and hospital_id=%s", (case_id, user.hospital_id)).fetchone()
    if not case:
        raise HTTPException(404, "Case not found")
    return case


@router.post("/cases/{case_id}/documents", status_code=201)
async def upload(case_id: str, file: UploadFile = File(...), doc_type: str | None = Form(None),
                 user: User = Depends(current_user)):
    if user.role not in UPLOAD_ROLES:
        raise HTTPException(403, "Your role cannot upload documents")
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, "File is larger than 20 MB")
    mime = (file.content_type or "").lower()
    if data[:5] == b"%PDF-":
        mime = "application/pdf"
    if mime not in ALLOWED:
        raise HTTPException(415, f"Unsupported file type {mime or 'unknown'}: upload PDF, PNG, JPEG or TIFF")
    if doc_type and doc_type not in intake.DOC_TYPES:
        raise HTTPException(422, f"Unknown document type {doc_type}")

    digest = intake.sha256(data)
    with tx() as c:
        case = _case(c, case_id, user)
        dup = c.execute("select id, case_id, filename from documents where hospital_id=%s and sha256=%s",
                        (user.hospital_id, digest)).fetchone()
    if dup:
        same = str(dup["case_id"]) == case_id
        raise HTTPException(409, {"code": "duplicate", "document_id": str(dup["id"]),
                                  "message": f"Already uploaded as {dup['filename']}" + ("" if same else " on another case")})

    result = intake.read_document(data, mime)              # OCR can be slow; outside the transaction
    path = f"{user.hospital_id}/{digest[:2]}/{digest}"
    storage.put(path, data, mime)

    final_type = doc_type or result.doc_type
    with tx() as c:
        doc = c.execute("""insert into documents (hospital_id, case_id, sha256, storage_path, filename, mime, doc_type, pages,
                           uploaded_by, read_method, page_types, type_scores, type_confirmed, size_bytes)
                           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *""",
                        (user.hospital_id, case_id, digest, path, file.filename, mime, final_type, len(result.pages),
                         user.id, result.method, Jsonb(result.page_types), Jsonb(result.scores), bool(doc_type), len(data))).fetchone()
        for i, (text, pt) in enumerate(zip(result.pages, result.page_types), start=1):
            c.execute("insert into document_pages (document_id, hospital_id, page, text, page_type) values (%s,%s,%s,%s,%s)",
                      (doc["id"], user.hospital_id, i, text, pt))
        for f in result.fields:
            c.execute("""insert into extracted_fields (hospital_id, case_id, document_id, name, value, original_value, page,
                         start_pos, end_pos, snippet, method) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                      (user.hospital_id, case_id, doc["id"], f.name, f.value, f.value, f.page, f.start, f.end, f.snippet, f.method))
        _event(c, user, case, "document_added", {"document_id": str(doc["id"]), "doc_type": final_type, "sha256": digest})
    return {"document": doc, "fields": [f.__dict__ for f in result.fields], "read_method": result.method}


@router.get("/cases/{case_id}/documents")
def list_docs(case_id: str, user: User = Depends(current_user)):
    with tx() as c:
        _case(c, case_id, user)
        docs = c.execute("select * from documents where case_id=%s order by created_at", (case_id,)).fetchall()
        fields = c.execute("select * from extracted_fields where case_id=%s order by created_at, name", (case_id,)).fetchall()
    return {"documents": docs, "fields": fields, "types": intake.DOC_TYPES, "key_fields": intake.KEY_FIELDS}


class DocPatch(BaseModel):
    doc_type: str


@router.patch("/documents/{doc_id}")
def set_type(doc_id: str, body: DocPatch, user: User = Depends(current_user)):
    if body.doc_type not in intake.DOC_TYPES:
        raise HTTPException(422, "Unknown document type")
    with tx() as c:
        d = c.execute("""update documents set doc_type=%s, type_confirmed=true where id=%s and hospital_id=%s
                         returning *""", (body.doc_type, doc_id, user.hospital_id)).fetchone()
        if not d:
            raise HTTPException(404, "Document not found")
        _event(c, user, _case(c, str(d["case_id"]), user), "document_retyped", {"document_id": doc_id, "doc_type": body.doc_type})
    return d


@router.get("/documents/{doc_id}/file")
def get_file(doc_id: str, user: User = Depends(current_user)):
    with tx() as c:
        d = c.execute("select * from documents where id=%s and hospital_id=%s", (doc_id, user.hospital_id)).fetchone()
    if not d:
        raise HTTPException(404, "Document not found")
    url = storage.signed_url(d["storage_path"])
    if url:
        return {"url": url}
    return Response(storage.get_local(d["storage_path"]), media_type=d["mime"],
                    headers={"Content-Disposition": f'inline; filename="{d["filename"]}"'})


class Confirm(BaseModel):
    value: str | None = None     # pass to correct the value; omit to confirm as read


@router.post("/fields/{field_id}/confirm")
def confirm(field_id: str, body: Confirm, user: User = Depends(current_user)):
    if user.role not in UPLOAD_ROLES:
        raise HTTPException(403, "Your role cannot confirm fields")
    with tx() as c:
        f = c.execute("select * from extracted_fields where id=%s and hospital_id=%s for update",
                      (field_id, user.hospital_id)).fetchone()
        if not f:
            raise HTTPException(404, "Field not found")
        value = (body.value or f["value"]).strip()
        if not value:
            raise HTTPException(422, "Value cannot be empty")
        f = c.execute("""update extracted_fields set value=%s, confirmed=true, confirmed_by=%s, confirmed_at=%s
                         where id=%s returning *""", (value, user.id, datetime.now(timezone.utc), field_id)).fetchone()
        _event(c, user, _case(c, str(f["case_id"]), user), "field_confirmed",
               {"field_id": field_id, "name": f["name"], "corrected": value != f["original_value"]})
    return f
