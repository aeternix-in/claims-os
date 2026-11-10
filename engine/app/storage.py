"""Original documents. Supabase Storage (private bucket 'documents') when a service key is set,
otherwise a local folder for development. Files are named by hash and never overwritten."""
from pathlib import Path

import httpx

from .config import get_settings

BUCKET = "documents"


def _remote() -> bool:
    return bool(get_settings().supabase_service_role_key)


def _headers() -> dict:
    k = get_settings().supabase_service_role_key
    return {"Authorization": f"Bearer {k}", "apikey": k}


def put(path: str, data: bytes, mime: str) -> None:
    s = get_settings()
    if _remote():
        r = httpx.post(f"{s.supabase_url}/storage/v1/object/{BUCKET}/{path}", content=data,
                       headers={**_headers(), "Content-Type": mime, "x-upsert": "false"}, timeout=60)
        if r.status_code not in (200, 201) and "already exists" not in r.text.lower():
            raise RuntimeError(f"Storage upload failed: {r.status_code} {r.text[:200]}")
        return
    p = Path(s.local_storage_dir) / path
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_bytes(data)


def signed_url(path: str, seconds: int = 300) -> str | None:
    """Short-lived link for viewing. None in local mode (the API streams the file instead)."""
    s = get_settings()
    if not _remote():
        return None
    r = httpx.post(f"{s.supabase_url}/storage/v1/object/sign/{BUCKET}/{path}", json={"expiresIn": seconds},
                   headers=_headers(), timeout=30)
    r.raise_for_status()
    return f"{s.supabase_url}/storage/v1{r.json()['signedURL']}"


def get_bytes(path: str) -> bytes:
    """The original file's bytes (for attaching to an NHCX query reply)."""
    s = get_settings()
    if _remote():
        r = httpx.get(f"{s.supabase_url}/storage/v1/object/{BUCKET}/{path}", headers=_headers(), timeout=60)
        r.raise_for_status()
        return r.content
    return get_local(path)


def get_local(path: str) -> bytes:
    return (Path(get_settings().local_storage_dir) / path).read_bytes()
