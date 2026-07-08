"""Artifact routes: create-with-generation, read, autosave, export, jobs."""

from __future__ import annotations

import json
from pathlib import Path

from docloom import FORMATS, lint, render
from docloom.render import RenderError, slug
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .db import execute, now, query_all, query_one
from .generate import (
    create_artifact, repair_mermaid, run_deck_pipeline, run_diagram_pipeline,
    run_doc_pipeline, run_infographic_pipeline, run_sheet_pipeline, save_artifact,
)
from .irx import bake, load_document, studio_theme, to_docloom_theme
from .jobs import cancel_job, job_state, sse_events, start_job
from .settings import data_dir

router = APIRouter(prefix="/api", tags=["artifacts"])


class GenerateRequest(BaseModel):
    kind: str = "deck"
    prompt: str
    options: dict = {}


@router.post("/notebooks/{notebook_id}/artifacts")
async def generate_artifact(notebook_id: str, body: GenerateRequest) -> dict:
    if query_one("SELECT id FROM notebooks WHERE id = ?", (notebook_id,)) is None:
        raise HTTPException(404, "notebook not found")
    pipelines = {"deck": run_deck_pipeline, "doc": run_doc_pipeline,
                 "sheet": run_sheet_pipeline, "diagram": run_diagram_pipeline,
                 "infographic": run_infographic_pipeline}
    if body.kind not in pipelines:
        raise HTTPException(400, f"unknown kind {body.kind!r}")
    artifact_id = create_artifact(notebook_id, body.kind)
    pipeline = pipelines[body.kind]

    async def work(ctx):
        from .chat import generation_context

        lines, sources = await generation_context(notebook_id, body.prompt)
        await pipeline(ctx, notebook_id, artifact_id, body.prompt, lines, sources)

    job_id = start_job(f"generate:{body.kind}", work,
                       notebook_id=notebook_id, artifact_id=artifact_id)
    return {"job_id": job_id, "artifact_id": artifact_id}


def _artifact_row(artifact_id: str):
    row = query_one("SELECT * FROM artifacts WHERE id = ?", (artifact_id,))
    if row is None:
        raise HTTPException(404, "artifact not found")
    return row


@router.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str) -> dict:
    row = _artifact_row(artifact_id)
    return {"id": row["id"], "notebook_id": row["notebook_id"],
            "kind": row["kind"], "title": row["title"],
            "version": row["version"],
            "payload": json.loads(row["payload_json"])}


class IrUpdate(BaseModel):
    payload: dict


@router.put("/artifacts/{artifact_id}/ir")
async def update_ir(artifact_id: str, body: IrUpdate) -> dict:
    row = _artifact_row(artifact_id)
    doc = load_document(body.payload)  # validates
    version = save_artifact(artifact_id, title=doc.title, payload=body.payload)
    findings = lint(doc)
    return {"version": version,
            "findings": [f.model_dump() for f in findings]}


@router.get("/artifacts/{artifact_id}/versions")
async def versions(artifact_id: str) -> list[dict]:
    return [dict(r) for r in query_all(
        "SELECT version, created FROM artifact_versions WHERE artifact_id = ? "
        "ORDER BY version DESC", (artifact_id,)
    )]


class RevertRequest(BaseModel):
    version: int


@router.post("/artifacts/{artifact_id}/revert")
async def revert(artifact_id: str, body: RevertRequest) -> dict:
    row = query_one(
        "SELECT payload_json FROM artifact_versions WHERE artifact_id = ? "
        "AND version = ?", (artifact_id, body.version))
    if row is None:
        raise HTTPException(404, "version not found")
    payload = json.loads(row["payload_json"])
    doc = load_document(payload)
    version = save_artifact(artifact_id, title=doc.title, payload=payload)
    return {"version": version}


class ExportRequest(BaseModel):
    format: str = "pptx"


@router.post("/artifacts/{artifact_id}/export")
async def export_artifact(artifact_id: str, body: ExportRequest) -> dict:
    row = _artifact_row(artifact_id)
    if body.format not in FORMATS:
        raise HTTPException(400, f"unknown format {body.format!r}")
    payload = json.loads(row["payload_json"])
    doc = bake(load_document(payload))
    from .assets import apply_brand

    theme = to_docloom_theme(apply_brand(studio_theme(payload.get("theme_name", "paper"))))
    findings = lint(doc, theme)
    errors = [f.model_dump() for f in findings if f.severity == "error"]
    if errors:
        raise HTTPException(422, detail={"findings": errors})
    filename = f"{slug(doc.title)}-v{row['version']}{FORMATS[body.format][1]}"
    out = data_dir() / "exports" / filename
    try:
        render(doc, body.format, out, theme)
    except RenderError as e:
        raise HTTPException(422, str(e))
    return {"url": f"/api/exports/{filename}", "filename": filename}


@router.get("/exports/{filename}")
async def download_export(filename: str) -> FileResponse:
    path = (data_dir() / "exports" / filename).resolve()
    if not path.is_file() or path.parent != (data_dir() / "exports").resolve():
        raise HTTPException(404, "export not found")
    return FileResponse(path, filename=filename)


@router.get("/files")
async def serve_file(path: str) -> FileResponse:
    """Serve a local file, confined to the app data directory."""
    root = data_dir().resolve()
    candidate = Path(path)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if root not in resolved.parents or not resolved.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(resolved)


class SavePayload(BaseModel):
    payload: dict


@router.put("/artifacts/{artifact_id}/payload")
async def update_payload(artifact_id: str, body: SavePayload) -> dict:
    """Generic payload save for non-Document artifacts (diagram/infographic)."""
    row = _artifact_row(artifact_id)
    version = save_artifact(artifact_id, row["title"], body.payload)
    return {"version": version}


class RepairRequest(BaseModel):
    src: str
    error: str


@router.post("/artifacts/{artifact_id}/repair")
async def repair(artifact_id: str, body: RepairRequest) -> dict:
    fixed = await repair_mermaid(body.src, body.error)
    return {"mermaid": fixed}


class RendersRequest(BaseModel):
    svg: str | None = None
    png_base64: str | None = None


@router.post("/artifacts/{artifact_id}/renders")
async def save_renders(artifact_id: str, body: RendersRequest) -> dict:
    """Persist browser-rendered SVG/PNG for an artifact (diagram/infographic)."""
    import base64

    _artifact_row(artifact_id)
    adir = data_dir() / "artifacts" / artifact_id
    adir.mkdir(parents=True, exist_ok=True)
    if body.svg:
        (adir / "render.svg").write_text(body.svg, encoding="utf-8")
    if body.png_base64:
        (adir / "render.png").write_bytes(base64.b64decode(body.png_base64))
    return {"ok": True}


@router.get("/artifacts/{artifact_id}/render.{ext}")
async def get_render(artifact_id: str, ext: str) -> FileResponse:
    path = data_dir() / "artifacts" / artifact_id / f"render.{ext}"
    if not path.is_file():
        raise HTTPException(404, "no render yet")
    return FileResponse(path)


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    state = job_state(job_id)
    if state is None:
        raise HTTPException(404, "job not found")
    return state


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    return StreamingResponse(sse_events(job_id), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@router.post("/jobs/{job_id}/cancel")
async def job_cancel(job_id: str) -> dict:
    return {"cancelled": cancel_job(job_id)}
