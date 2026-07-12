"""Artifact routes: create-with-generation, read, autosave, export, jobs.
All scoped to the current user's notebooks/artifacts."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from docloom import FORMATS, lint, render
from docloom.render import RenderError, slug
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from .auth import current_user, require_artifact, require_notebook
from .db import execute, now, query_all, query_one
from .generate import (
    create_artifact, repair_diagram, run_deck_pipeline, run_diagram_pipeline,
    run_doc_pipeline, run_infographic_pipeline, run_podcast_pipeline,
    run_sheet_pipeline, save_artifact,
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
async def generate_artifact(
    notebook_id: str, body: GenerateRequest, user: dict = Depends(current_user)
) -> dict:
    require_notebook(user["id"], notebook_id)
    pipelines = {"deck": run_deck_pipeline, "doc": run_doc_pipeline,
                 "sheet": run_sheet_pipeline, "diagram": run_diagram_pipeline,
                 "infographic": run_infographic_pipeline,
                 "podcast": run_podcast_pipeline}
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


def set_artifact_status(artifact_id: str, status: str) -> None:
    """Update an artifact's build status ('building' | 'ready' | 'failed').
    Called by the generation pipeline, not a route."""
    execute("UPDATE artifacts SET status = ? WHERE id = ?", (status, artifact_id))


@router.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str, user: dict = Depends(current_user)) -> dict:
    require_artifact(user["id"], artifact_id)
    row = _artifact_row(artifact_id)
    return {"id": row["id"], "notebook_id": row["notebook_id"],
            "kind": row["kind"], "title": row["title"],
            "version": row["version"], "status": row["status"],
            "payload": json.loads(row["payload_json"])}


@router.delete("/artifacts/{artifact_id}")
async def delete_artifact(artifact_id: str, user: dict = Depends(current_user)) -> dict:
    require_artifact(user["id"], artifact_id)
    execute("DELETE FROM artifact_versions WHERE artifact_id = ?", (artifact_id,))
    execute("DELETE FROM artifacts WHERE id = ?", (artifact_id,))
    for d in (data_dir() / "artifacts" / artifact_id, data_dir() / "exports" / artifact_id):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}


class IrUpdate(BaseModel):
    payload: dict


@router.put("/artifacts/{artifact_id}/ir")
async def update_ir(
    artifact_id: str, body: IrUpdate, user: dict = Depends(current_user)
) -> dict:
    require_artifact(user["id"], artifact_id)
    doc = load_document(body.payload)  # validates
    version = save_artifact(artifact_id, title=doc.title, payload=body.payload)
    findings = lint(doc)
    return {"version": version,
            "findings": [f.model_dump() for f in findings]}


@router.get("/artifacts/{artifact_id}/versions")
async def versions(artifact_id: str, user: dict = Depends(current_user)) -> list[dict]:
    require_artifact(user["id"], artifact_id)
    return [dict(r) for r in query_all(
        "SELECT version, created FROM artifact_versions WHERE artifact_id = ? "
        "ORDER BY version DESC", (artifact_id,)
    )]


class RevertRequest(BaseModel):
    version: int


@router.post("/artifacts/{artifact_id}/revert")
async def revert(
    artifact_id: str, body: RevertRequest, user: dict = Depends(current_user)
) -> dict:
    require_artifact(user["id"], artifact_id)
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
async def export_artifact(
    artifact_id: str, body: ExportRequest, user: dict = Depends(current_user)
) -> dict:
    require_artifact(user["id"], artifact_id)
    row = _artifact_row(artifact_id)
    if body.format not in FORMATS:
        raise HTTPException(400, f"unknown format {body.format!r}")
    payload = json.loads(row["payload_json"])
    doc = bake(load_document(payload))
    from .assets import apply_brand, brand_logo_image
    from docloom.ir import Image as IRImage

    # Stamp the active brand logo on every slide / report header, unless the
    # document already carries its own logo.
    if doc.logo is None:
        logo = brand_logo_image(user["id"])
        if logo:
            doc.logo = IRImage(**logo)
    try:
        theme = to_docloom_theme(
            apply_brand(studio_theme(payload.get("theme_name", "paper")), user["id"]))
    except (ValueError, TypeError) as e:
        # a malformed brand color/font saved via PUT /brand-kit would otherwise
        # 500 every export; surface it as an actionable client error instead.
        raise HTTPException(422, detail="brand kit has an invalid color or "
                            "font; fix it in the brand kit and try again") from e
    findings = lint(doc, theme)
    errors = [f.model_dump() for f in findings if f.severity == "error"]
    if errors:
        raise HTTPException(422, detail={"findings": errors})
    filename = f"{slug(doc.title)}-v{row['version']}{FORMATS[body.format][1]}"
    export_dir = data_dir() / "exports" / artifact_id
    export_dir.mkdir(parents=True, exist_ok=True)
    out = export_dir / filename
    try:
        render(doc, body.format, out, theme)
    except RenderError as e:
        raise HTTPException(422, str(e))
    return {"url": f"/api/artifacts/{artifact_id}/exports/{filename}", "filename": filename}


@router.get("/artifacts/{artifact_id}/exports/{filename}")
async def download_export(
    artifact_id: str, filename: str, user: dict = Depends(current_user)
) -> FileResponse:
    # Scoped per artifact (not a shared filename namespace) so exports cannot
    # collide with, or be downloaded by, a different artifact's owner.
    require_artifact(user["id"], artifact_id)
    export_dir = (data_dir() / "exports" / artifact_id).resolve()
    path = (export_dir / filename).resolve()
    if not path.is_file() or path.parent != export_dir:
        raise HTTPException(404, "export not found")
    return FileResponse(path, filename=filename)


@router.get("/artifacts/{artifact_id}/media")
async def artifact_media(
    artifact_id: str, path: str, user: dict = Depends(current_user)
) -> FileResponse:
    """Serve a file referenced by this artifact's own IR (e.g. a diagram or
    infographic render), confined strictly to the artifact's own directory.

    Replaces the old /api/files?path=... route, which resolved a client-
    supplied path against the whole shared data directory with no per-
    resource or per-tenant scoping at all: any logged-in user could pass
    path=studio.db (or another user's source/export path) and get it back."""
    require_artifact(user["id"], artifact_id)
    root = (data_dir() / "artifacts" / artifact_id).resolve()
    candidate = Path(path)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if root not in resolved.parents or not resolved.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(resolved)


class SavePayload(BaseModel):
    payload: dict


@router.put("/artifacts/{artifact_id}/payload")
async def update_payload(
    artifact_id: str, body: SavePayload, user: dict = Depends(current_user)
) -> dict:
    """Generic payload save for non-Document artifacts (diagram/infographic)."""
    require_artifact(user["id"], artifact_id)
    row = _artifact_row(artifact_id)
    version = save_artifact(artifact_id, row["title"], body.payload)
    return {"version": version}


class RepairRequest(BaseModel):
    src: str
    error: str


@router.post("/artifacts/{artifact_id}/repair")
async def repair(
    artifact_id: str, body: RepairRequest, user: dict = Depends(current_user)
) -> dict:
    require_artifact(user["id"], artifact_id)
    fixed = await repair_diagram(body.src, body.error, user["id"])
    return {"source": fixed}


class RendersRequest(BaseModel):
    svg: str | None = None
    png_base64: str | None = None


@router.post("/artifacts/{artifact_id}/renders")
async def save_renders(
    artifact_id: str, body: RendersRequest, user: dict = Depends(current_user)
) -> dict:
    """Persist browser-rendered SVG/PNG for an artifact (diagram/infographic)."""
    import base64
    import binascii

    require_artifact(user["id"], artifact_id)
    png_bytes = None
    if body.png_base64:
        try:
            png_bytes = base64.b64decode(body.png_base64, validate=True)
        except (binascii.Error, ValueError) as e:
            raise HTTPException(400, f"invalid png_base64: {e}")
    adir = data_dir() / "artifacts" / artifact_id
    adir.mkdir(parents=True, exist_ok=True)
    if body.svg:
        (adir / "render.svg").write_text(body.svg, encoding="utf-8")
    if png_bytes is not None:
        (adir / "render.png").write_bytes(png_bytes)
    return {"ok": True}


# GET serves the render; HEAD lets the editor probe whether one exists yet
# (to enable/disable the download buttons) without transferring the bytes.
@router.api_route("/artifacts/{artifact_id}/render.{ext}", methods=["GET", "HEAD"])
async def get_render(
    artifact_id: str, ext: str, user: dict = Depends(current_user)
) -> FileResponse:
    require_artifact(user["id"], artifact_id)
    path = data_dir() / "artifacts" / artifact_id / f"render.{ext}"
    if not path.is_file():
        raise HTTPException(404, "no render yet")
    return FileResponse(path)


@router.get("/artifacts/{artifact_id}/audio.{ext}")
async def get_audio(
    artifact_id: str, ext: str, user: dict = Depends(current_user)
) -> FileResponse:
    """Serve a podcast's synthesized audio. Starlette's FileResponse handles
    HTTP Range requests, so the player can seek/scrub."""
    require_artifact(user["id"], artifact_id)
    path = data_dir() / "artifacts" / artifact_id / f"audio.{ext}"
    if not path.is_file():
        raise HTTPException(404, "no audio yet")
    return FileResponse(path, media_type=f"audio/{ext}")


class AudioRequest(BaseModel):
    script: dict


@router.post("/artifacts/{artifact_id}/audio")
async def regenerate_audio(
    artifact_id: str, body: AudioRequest, user: dict = Depends(current_user)
) -> dict:
    """(Re)synthesize a podcast's audio from the (possibly edited) script and
    persist it. TTS is optional: if no backend is installed we return 503 with
    an actionable message, and the transcript is unaffected."""
    from .settings import get_setting
    from .tts import TtsError, synthesize_podcast

    require_artifact(user["id"], artifact_id)
    row = _artifact_row(artifact_id)
    out = data_dir() / "artifacts" / artifact_id / "audio.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        duration = await synthesize_podcast(
            body.script, out, get_setting("provider.tts", user["id"])
        )
    except TtsError as e:
        raise HTTPException(503, str(e)) from e
    payload = json.loads(row["payload_json"])
    payload["script"] = body.script
    payload["audio_path"] = f"artifacts/{artifact_id}/audio.wav"
    payload["duration_s"] = duration
    save_artifact(artifact_id, row["title"], payload)
    return {"audio_path": payload["audio_path"], "duration_s": duration}


def _require_job(user_id: str, job_id: str) -> dict:
    """Job state, but only if the job's notebook belongs to the user."""
    state = job_state(job_id)
    if state is None:
        raise HTTPException(404, "job not found")
    row = query_one("SELECT notebook_id FROM jobs WHERE id = ?", (job_id,))
    if row is not None and row["notebook_id"]:
        require_notebook(user_id, row["notebook_id"])  # 404 if not owned
    return state


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, user: dict = Depends(current_user)) -> dict:
    return _require_job(user["id"], job_id)


@router.get("/jobs/{job_id}/events")
async def job_events(
    job_id: str, user: dict = Depends(current_user)
) -> StreamingResponse:
    _require_job(user["id"], job_id)
    return StreamingResponse(sse_events(job_id), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@router.post("/jobs/{job_id}/cancel")
async def job_cancel(job_id: str, user: dict = Depends(current_user)) -> dict:
    _require_job(user["id"], job_id)
    return {"cancelled": cancel_job(job_id)}
