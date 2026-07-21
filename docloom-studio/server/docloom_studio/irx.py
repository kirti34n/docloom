"""Studio artifact envelope ↔ docloom IR: the single down-conversion point.

Deck/doc/sheet payload: {"ir": <docloom Document>, "theme_name": str,
"brand_kit_id": str|None}. Image paths inside the IR may use asset://{id};
bake() resolves them (and artifact renders) to real files before export.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from docloom import Document, Theme, ensure_ids
from docloom.render import raster

from .settings import data_dir

log = logging.getLogger("docloom_studio.irx")

# Temp PNGs created by the mkstemp fallback below (used only when the
# artifacts dir itself can't be written to). They can't be deleted right
# after creation -- the caller still needs to read the file -- so they're
# tracked here and swept on interpreter exit instead of leaking forever.
_TEMP_RENDER_FILES: set[str] = set()


def _cleanup_temp_renders() -> None:
    for p in _TEMP_RENDER_FILES:
        try:
            Path(p).unlink()
        except OSError:
            pass


atexit.register(_cleanup_temp_renders)

THEME_DIR = Path(__file__).parent / "themes"

# matches diagram_pptx.py's DIAGRAM_RASTER_PX: wide enough to stay legible
# when a slide-width diagram/infographic gets scaled down on the page.
ARTIFACT_RASTER_PX = 1600

DOCLOOM_TOKENS = ("primary", "accent", "background", "surface",
                  "text", "muted", "font_heading", "font_body",
                  "font_heading_src", "font_body_src")


def studio_theme(name: str) -> dict[str, Any]:
    base = THEME_DIR.resolve()
    path = (base / f"{name}.json").resolve()
    if path.parent != base or not path.is_file():
        path = base / "paper.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["name"] = path.stem
    return data


def to_docloom_theme(theme_json: dict[str, Any]) -> Theme:
    return Theme(**{k: theme_json[k] for k in DOCLOOM_TOKENS if k in theme_json})


def load_document(payload: dict[str, Any]) -> Document:
    if "ir" not in payload:
        raise HTTPException(400, "artifact payload has no document IR, not exportable or not ready yet")
    return ensure_ids(Document.model_validate(payload["ir"]))


def _resolve_path(path: str | None, user_id: str | None) -> str | None:
    if not path or not path.startswith("asset://"):
        return None  # untrusted literal path -> dropped, never handed to a renderer
    if user_id is None:
        return None
    asset_id = path.removeprefix("asset://")
    from .db import query_one

    row = query_one(
        "SELECT filename FROM assets WHERE id = ? AND user_id = ?", (asset_id, user_id))
    if row is None:
        return None
    return str(data_dir() / "assets" / asset_id / row["filename"])


def _svg_content_hash(svg: Path) -> str:
    """Same pattern as docloom.ir.diagram_hash: a short, stable content
    stamp used purely as a cache key, not for security."""
    return hashlib.sha1(svg.read_bytes()).hexdigest()[:12]


def _resolve_artifact_render(artifact_id: str) -> str | None:
    """The best available baked render for a diagram/infographic Artifact,
    keyed off the *current* render.svg content so an edit can never export
    the previous version's picture.

    render.svg is rewritten on every editor save, but render.png is only
    rewritten when the caller also re-renders one server-side (see
    InfographicEditor.tsx, which posts {svg} alone on every edit) -- so a
    fixed-name render.png can silently go stale relative to render.svg.
    To make that impossible: once render.svg exists, the content hash of its
    *current* bytes is the cache key. A previously-cached render.{hash}.png
    for that exact content is reused; otherwise the fixed-name render.png is
    trusted only if it is at least as new as render.svg (i.e. it was written
    for this content, as happens when a browser save posts svg+png
    together); otherwise it is regenerated from the current render.svg and
    cached under the new hash, so stale content can never be served.

    Ownership is checked by the caller before this runs. A missing
    render.png/render.svg used to mean a silently empty slot in every export
    (docloom[diagrams]/resvg not installed, or the browser never saved a
    render) -- this is the one place that closes that hole, so a genuine gap
    must be loud rather than swallowed."""
    adir = data_dir() / "artifacts" / artifact_id
    png = adir / "render.png"
    svg = adir / "render.svg"

    if not svg.is_file():
        # No source of truth to validate freshness against -- trust
        # whatever render.png exists (e.g. a render saved without an
        # accompanying svg).
        if png.is_file():
            return str(png)
        log.warning(
            "artifact %s has neither render.png nor render.svg; it will "
            "export as an empty slot", artifact_id)
        return None

    content_hash = _svg_content_hash(svg)
    cached = adir / f"render.{content_hash}.png"
    if cached.is_file():
        return str(cached)

    if png.is_file() and png.stat().st_mtime >= svg.stat().st_mtime:
        return str(png)

    data = raster.svg_file_to_png(svg, width=ARTIFACT_RASTER_PX)
    if not data:
        log.warning(
            "artifact %s has render.svg but no usable up-to-date render.png, "
            "and server-side rasterization failed or is unavailable "
            "(pip install \"docloom[pdf,diagrams]\"); it will export as "
            "an empty slot", artifact_id)
        return None
    try:
        cached.write_bytes(data)
    except OSError:
        log.warning(
            "rasterized artifact %s but could not cache it to %s; "
            "falling back to a temp file for this export", artifact_id, cached)
        fd, tmp_path = tempfile.mkstemp(suffix=".png", prefix="docloom-render-")
        with open(fd, "wb") as f:
            f.write(data)
        _TEMP_RENDER_FILES.add(tmp_path)
        return tmp_path
    return str(cached)


def _owns_artifact(user_id: str, artifact_id: str) -> bool:
    from .db import query_one

    return query_one(
        "SELECT 1 FROM artifacts a JOIN notebooks n ON n.id = a.notebook_id "
        "JOIN workspaces w ON w.id = n.workspace_id "
        "WHERE a.id = ? AND w.user_id = ?", (artifact_id, user_id)) is not None


def bake(doc: Document, user_id: str | None = None) -> Document:
    """Resolve asset:// refs and artifact render paths to real files, in a
    deep copy. Renderers skip anything that still resolves to nothing. Every
    resolution is scoped to user_id (the exporting user); pass None only when
    no user context exists, which resolves nothing rather than leaking
    another tenant's files."""
    doc = Document.model_validate(doc.model_dump())

    def fix_image(img: Any) -> None:
        img.path = _resolve_path(img.path, user_id)

    def fix_blocks(blocks: list[Any]) -> None:
        for b in blocks:
            kind = type(b).__name__
            if kind == "Image":
                fix_image(b)
            elif kind in ("Chart", "Artifact"):
                b.path = _resolve_path(b.path, user_id)
                if kind == "Artifact" and not b.path and b.artifact_id:
                    if user_id is not None and _owns_artifact(user_id, b.artifact_id):
                        b.path = _resolve_artifact_render(b.artifact_id)
                    else:
                        # Same security behaviour as before (never resolve a
                        # render the exporting user doesn't own) -- just
                        # make the resulting blank slot observable, instead
                        # of failing this one branch silently.
                        log.warning(
                            "artifact %s not resolved: exporting user does "
                            "not own it (or no user context); it will "
                            "export as an empty slot", b.artifact_id)

    if doc.logo is not None:
        fix_image(doc.logo)
    fix_blocks(doc.blocks)
    for slide in doc.slides:
        if slide.image is not None:
            fix_image(slide.image)
        fix_blocks(slide.blocks)
        fix_blocks(slide.right)
    return doc
