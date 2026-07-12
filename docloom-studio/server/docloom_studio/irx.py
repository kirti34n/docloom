"""Studio artifact envelope ↔ docloom IR: the single down-conversion point.

Deck/doc/sheet payload: {"ir": <docloom Document>, "theme_name": str,
"brand_kit_id": str|None}. Image paths inside the IR may use asset://{id};
bake() resolves them (and artifact renders) to real files before export.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from docloom import Document, Theme, ensure_ids

from .settings import data_dir

THEME_DIR = Path(__file__).parent / "themes"

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


def _resolve_path(path: str | None) -> str | None:
    if path and path.startswith("asset://"):
        asset_id = path.removeprefix("asset://")
        from .db import query_one

        row = query_one("SELECT filename FROM assets WHERE id = ?", (asset_id,))
        if row is None:
            return None
        return str(data_dir() / "assets" / asset_id / row["filename"])
    return path


def bake(doc: Document) -> Document:
    """Resolve asset:// refs and artifact render paths to real files, in a
    deep copy. Renderers skip anything that still resolves to nothing."""
    doc = Document.model_validate(doc.model_dump())

    def fix_image(img: Any) -> None:
        img.path = _resolve_path(img.path)

    def fix_blocks(blocks: list[Any]) -> None:
        for b in blocks:
            kind = type(b).__name__
            if kind == "Image":
                fix_image(b)
            elif kind in ("Chart", "Artifact"):
                b.path = _resolve_path(b.path)
                if kind == "Artifact" and not b.path and b.artifact_id:
                    png = data_dir() / "artifacts" / b.artifact_id / "render.png"
                    if png.is_file():
                        b.path = str(png)

    if doc.logo is not None:
        fix_image(doc.logo)
    fix_blocks(doc.blocks)
    for slide in doc.slides:
        if slide.image is not None:
            fix_image(slide.image)
        fix_blocks(slide.blocks)
        fix_blocks(slide.right)
    return doc
