"""Source routes: add (file/url/text), list, toggle context mode, delete; and
grounded chat streaming. All scoped to the current user's notebooks."""

from __future__ import annotations

import json
import shutil

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .auth import current_user, require_notebook, require_source
from .chat import load_messages, stream_chat
from .db import execute, new_id, now, query_all, query_one, rows_to_dicts
from .ingest import _source_dir, ingest_source, load_chunks
from .jobs import start_job
from .settings import data_dir

router = APIRouter(prefix="/api", tags=["sources"])


def _kick_ingest(source_id: str, notebook_id: str) -> str:
    async def work(ctx):
        await ingest_source(source_id, ctx)

    return start_job("ingest", work, notebook_id=notebook_id)


@router.get("/notebooks/{notebook_id}/sources")
async def list_sources(
    notebook_id: str, user: dict = Depends(current_user)
) -> list[dict]:
    require_notebook(user["id"], notebook_id)
    rows = query_all(
        "SELECT id, kind, title, status, context_mode, url, meta_json, created "
        "FROM sources WHERE notebook_id = ? ORDER BY created", (notebook_id,)
    )
    out = []
    for r in rows_to_dicts(rows):
        meta = json.loads(r.pop("meta_json"))
        r["error"] = meta.get("error")
        out.append(r)
    return out


# Types the ingest pipeline actually parses. Reject anything else early so a
# user doesn't upload a 2GB zip and wait for a failed job to tell them.
ALLOWED_EXT = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".xlsm", ".csv", ".html", ".htm",
    ".epub", ".txt", ".md", ".markdown", ".rst", ".text", ".json", ".log",
}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB


@router.post("/notebooks/{notebook_id}/sources/file")
async def add_file(
    notebook_id: str, file: UploadFile, user: dict = Depends(current_user)
) -> dict:
    require_notebook(user["id"], notebook_id)
    # basename only: strip any directory components (both separators) so a
    # crafted filename like "../../evil" cannot escape the source directory
    name = (file.filename or "upload").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or name in (".", ".."):
        name = "upload"
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext not in ALLOWED_EXT:
        raise HTTPException(
            415, f"unsupported file type {ext or '(none)'}; allowed: "
                 + ", ".join(sorted(ALLOWED_EXT)))

    sid = new_id()
    dest = _source_dir(sid) / name
    # stream to disk with a hard size cap so an oversized upload is rejected
    # without buffering the whole file in memory
    written = 0
    try:
        with dest.open("wb") as f:
            while True:
                block = await file.read(1024 * 1024)
                if not block:
                    break
                written += len(block)
                if written > MAX_UPLOAD_BYTES:
                    f.close()
                    shutil.rmtree(_source_dir(sid), ignore_errors=True)
                    raise HTTPException(
                        413, f"file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
                f.write(block)
    except HTTPException:
        raise
    execute(
        "INSERT INTO sources (id, notebook_id, kind, title, path, status, "
        "context_mode, meta_json, created) VALUES (?, ?, 'file', ?, ?, 'pending', "
        "'full', '{}', ?)",
        (sid, notebook_id, name, str(dest), now()),
    )
    job = _kick_ingest(sid, notebook_id)
    return {"source_id": sid, "job_id": job}


@router.get("/notebooks/{notebook_id}/messages")
async def chat_history(
    notebook_id: str, user: dict = Depends(current_user)
) -> list[dict]:
    """Persisted conversation for the notebook (for reload / navigate-back)."""
    require_notebook(user["id"], notebook_id)
    return load_messages(notebook_id)


@router.get("/sources/{source_id}/content")
async def source_content(
    source_id: str, user: dict = Depends(current_user)
) -> dict:
    """A source's parsed chunks — the source-reader renders these and highlights
    the cited passage."""
    require_source(user["id"], source_id)
    row = query_one("SELECT title FROM sources WHERE id = ?", (source_id,))
    return {
        "title": row["title"] if row else "",
        "chunks": [
            {"chunk_ix": c.get("chunk_ix"), "page": c.get("page"),
             "section": c.get("section", ""), "text": c.get("text", "")}
            for c in load_chunks(source_id)
        ],
    }


class UrlIn(BaseModel):
    url: str


@router.post("/notebooks/{notebook_id}/sources/url")
async def add_url(
    notebook_id: str, body: UrlIn, user: dict = Depends(current_user)
) -> dict:
    require_notebook(user["id"], notebook_id)
    sid = new_id()
    execute(
        "INSERT INTO sources (id, notebook_id, kind, title, url, status, "
        "context_mode, meta_json, created) VALUES (?, ?, 'url', ?, ?, 'pending', "
        "'full', '{}', ?)",
        (sid, notebook_id, body.url, body.url, now()),
    )
    return {"source_id": sid, "job_id": _kick_ingest(sid, notebook_id)}


class TextIn(BaseModel):
    title: str = "Pasted text"
    text: str


@router.post("/notebooks/{notebook_id}/sources/text")
async def add_text(
    notebook_id: str, body: TextIn, user: dict = Depends(current_user)
) -> dict:
    require_notebook(user["id"], notebook_id)
    sid = new_id()
    execute(
        "INSERT INTO sources (id, notebook_id, kind, title, status, "
        "context_mode, meta_json, created) VALUES (?, ?, 'text', ?, 'pending', "
        "'full', ?, ?)",
        (sid, notebook_id, body.title, json.dumps({"text": body.text}), now()),
    )
    return {"source_id": sid, "job_id": _kick_ingest(sid, notebook_id)}


class ContextPatch(BaseModel):
    context_mode: str


@router.patch("/sources/{source_id}")
async def patch_source(
    source_id: str, body: ContextPatch, user: dict = Depends(current_user)
) -> dict:
    require_source(user["id"], source_id)
    if body.context_mode not in ("full", "insights", "excluded"):
        raise HTTPException(400, "bad context_mode")
    execute("UPDATE sources SET context_mode = ? WHERE id = ?",
            (body.context_mode, source_id))
    return {"ok": True}


@router.post("/sources/{source_id}/reingest")
async def reingest_source(
    source_id: str, user: dict = Depends(current_user)
) -> dict:
    """Re-parse + re-embed a source. Used to recover a source flagged 'stale'
    (its vectors no longer match its chunks) without deleting and re-uploading."""
    require_source(user["id"], source_id)
    row = query_one("SELECT notebook_id FROM sources WHERE id = ?", (source_id,))
    execute("UPDATE sources SET status = 'pending' WHERE id = ?", (source_id,))
    job_id = _kick_ingest(source_id, row["notebook_id"])
    return {"job_id": job_id}


@router.delete("/sources/{source_id}")
async def delete_source(
    source_id: str, user: dict = Depends(current_user)
) -> dict:
    require_source(user["id"], source_id)
    execute("DELETE FROM sources WHERE id = ?", (source_id,))
    d = data_dir() / "sources" / source_id
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}


class ChatIn(BaseModel):
    message: str


@router.post("/notebooks/{notebook_id}/chat")
async def chat(
    notebook_id: str, body: ChatIn, user: dict = Depends(current_user)
) -> StreamingResponse:
    require_notebook(user["id"], notebook_id)
    return StreamingResponse(stream_chat(notebook_id, body.message),
                             media_type="application/x-ndjson")


class ResearchIn(BaseModel):
    query: str


@router.post("/notebooks/{notebook_id}/research")
async def research(
    notebook_id: str, body: ResearchIn, user: dict = Depends(current_user)
) -> dict:
    require_notebook(user["id"], notebook_id)
    from .research import run_research

    async def work(ctx):
        await run_research(ctx, notebook_id, body.query)

    return {"job_id": start_job("research", work, notebook_id=notebook_id)}
