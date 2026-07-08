"""Asset library + brand kit + the slot resolver.

Uploaded images/logos/fonts live under assets/{id}/. The resolver fills a
deck's image slots from the user's tagged assets (keyword overlap — no
embeddings needed at this scale). The brand kit (accent + logo) is a single
active record in settings that generation and export both read."""

from __future__ import annotations

import json
import re
import shutil

from fastapi import APIRouter, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .db import execute, new_id, now, query_all, query_one, rows_to_dicts
from .settings import data_dir, get_setting, set_setting

router = APIRouter(prefix="/api", tags=["assets"])

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
FONT_EXT = {".ttf", ".otf", ".woff", ".woff2"}


def _asset_dir(asset_id: str):
    d = data_dir() / "assets" / asset_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _font_embeddable(path) -> bool:
    """OS/2 fsType: bit 1 set (0x02) = restricted, do not embed."""
    try:
        from fontTools.ttLib import TTFont

        fs = TTFont(path)["OS/2"].fsType
        return not (fs & 0x02)
    except Exception:
        return True  # unknown → assume ok, UI still warns pptx can't embed


@router.get("/assets")
async def list_assets() -> list[dict]:
    rows = query_all("SELECT id, type, filename, tags, slot_hint, created "
                     "FROM assets ORDER BY created DESC")
    return rows_to_dicts(rows)


@router.post("/assets")
async def upload_asset(
    file: UploadFile, type: str = Form("image"), tags: str = Form("")
) -> dict:
    name = file.filename or "asset"
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if type == "font" and ext not in FONT_EXT:
        raise HTTPException(400, "not a font file")
    if type in ("image", "logo") and ext not in IMAGE_EXT:
        raise HTTPException(400, "not an image file")

    aid = new_id()
    dest = _asset_dir(aid) / name
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    execute(
        "INSERT INTO assets (id, type, filename, tags, created) "
        "VALUES (?, ?, ?, ?, ?)", (aid, type, name, tags, now()),
    )
    warn = None
    if type == "font":
        warn = ("Fonts embed in PDF exports, but PowerPoint stores font names "
                "only — install the font to see it in PPTX."
                if _font_embeddable(dest) else
                "This font's license blocks embedding; it will not be embedded.")
    return {"id": aid, "font_note": warn}


class TagPatch(BaseModel):
    tags: str


@router.patch("/assets/{asset_id}")
async def patch_asset(asset_id: str, body: TagPatch) -> dict:
    execute("UPDATE assets SET tags = ? WHERE id = ?", (body.tags, asset_id))
    return {"ok": True}


@router.delete("/assets/{asset_id}")
async def delete_asset(asset_id: str) -> dict:
    execute("DELETE FROM assets WHERE id = ?", (asset_id,))
    shutil.rmtree(data_dir() / "assets" / asset_id, ignore_errors=True)
    return {"ok": True}


@router.get("/assets/{asset_id}/file")
async def serve_asset(asset_id: str) -> FileResponse:
    row = query_one("SELECT filename FROM assets WHERE id = ?", (asset_id,))
    if row is None:
        raise HTTPException(404, "asset not found")
    path = data_dir() / "assets" / asset_id / row["filename"]
    if not path.is_file():
        raise HTTPException(404, "file missing")
    return FileResponse(path)


# ---------------------------------------------------------------- resolver

_WORD = re.compile(r"[a-z0-9]+")


def _words(s: str) -> set[str]:
    return set(_WORD.findall(s.lower()))


def resolve_image(query: str) -> str | None:
    """Pick the best image asset for a slot query by tag/filename overlap."""
    q = _words(query)
    if not q:
        return None
    best, best_score = None, 0
    for a in rows_to_dicts(query_all(
            "SELECT id, filename, tags FROM assets WHERE type IN ('image','logo')")):
        score = len(q & (_words(a["tags"]) | _words(a["filename"])))
        if score > best_score:
            best, best_score = a["id"], score
    return best


# ----------------------------------------------------------------- brand kit

class BrandKit(BaseModel):
    accent: str | None = None
    logo_asset_id: str | None = None


def active_brand() -> dict:
    return get_setting("brand.active") or {}


@router.get("/brand-kit")
async def get_brand() -> dict:
    return active_brand()


@router.put("/brand-kit")
async def put_brand(body: BrandKit) -> dict:
    set_setting("brand.active", body.model_dump())
    return {"ok": True}


def apply_brand(theme_json: dict) -> dict:
    """Overlay the active brand accent onto a theme's tokens."""
    brand = active_brand()
    accent = brand.get("accent")
    if accent:
        theme_json = {**theme_json, "primary": accent, "accent": accent}
    return theme_json
