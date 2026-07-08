"""Source routes: add (file/url/text), list, toggle context mode, delete; and
grounded chat streaming."""

from __future__ import annotations

import json
import shutil

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .chat import stream_chat
from .db import execute, new_id, now, query_all, query_one, rows_to_dicts
from .ingest import _source_dir, ingest_source
from .jobs import start_job
from .settings import data_dir

router = APIRouter(prefix="/api", tags=["sources"])


def _kick_ingest(source_id: str, notebook_id: str) -> str:
    async def work(ctx):
        await ingest_source(source_id, ctx)

    return start_job("ingest", work, notebook_id=notebook_id)


@router.get("/notebooks/{notebook_id}/sources")
async def list_sources(notebook_id: str) -> list[dict]:
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


@router.post("/notebooks/{notebook_id}/sources/file")
async def add_file(notebook_id: str, file: UploadFile) -> dict:
    if query_one("SELECT id FROM notebooks WHERE id = ?", (notebook_id,)) is None:
        raise HTTPException(404, "notebook not found")
    sid = new_id()
    dest = _source_dir(sid) / (file.filename or "upload")
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    execute(
        "INSERT INTO sources (id, notebook_id, kind, title, path, status, "
        "context_mode, meta_json, created) VALUES (?, ?, 'file', ?, ?, 'pending', "
        "'full', '{}', ?)",
        (sid, notebook_id, file.filename or "Upload", str(dest), now()),
    )
    job = _kick_ingest(sid, notebook_id)
    return {"source_id": sid, "job_id": job}


class UrlIn(BaseModel):
    url: str


@router.post("/notebooks/{notebook_id}/sources/url")
async def add_url(notebook_id: str, body: UrlIn) -> dict:
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
async def add_text(notebook_id: str, body: TextIn) -> dict:
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
async def patch_source(source_id: str, body: ContextPatch) -> dict:
    if body.context_mode not in ("full", "insights", "excluded"):
        raise HTTPException(400, "bad context_mode")
    execute("UPDATE sources SET context_mode = ? WHERE id = ?",
            (body.context_mode, source_id))
    return {"ok": True}


@router.delete("/sources/{source_id}")
async def delete_source(source_id: str) -> dict:
    execute("DELETE FROM sources WHERE id = ?", (source_id,))
    d = data_dir() / "sources" / source_id
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}


class ChatIn(BaseModel):
    message: str


@router.post("/notebooks/{notebook_id}/chat")
async def chat(notebook_id: str, body: ChatIn) -> StreamingResponse:
    return StreamingResponse(stream_chat(notebook_id, body.message),
                             media_type="application/x-ndjson")


class ResearchIn(BaseModel):
    query: str


@router.post("/notebooks/{notebook_id}/research")
async def research(notebook_id: str, body: ResearchIn) -> dict:
    if query_one("SELECT id FROM notebooks WHERE id = ?", (notebook_id,)) is None:
        raise HTTPException(404, "notebook not found")
    from .research import run_research

    async def work(ctx):
        await run_research(ctx, notebook_id, body.query)

    return {"job_id": start_job("research", work, notebook_id=notebook_id)}
