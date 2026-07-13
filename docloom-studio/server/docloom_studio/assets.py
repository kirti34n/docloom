"""Asset library + brand kit + the slot resolver.

Uploaded images/logos/fonts live under assets/{id}/. The resolver fills a
deck's image slots from the user's tagged assets (keyword overlap — no
embeddings needed at this scale). The brand kit (accent + logo) is a single
active record in settings that generation and export both read."""

from __future__ import annotations

import json
import re
import shutil

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .auth import current_user
from .db import execute, new_id, now, query_all, query_one, rows_to_dicts
from .settings import data_dir, get_setting, set_setting

router = APIRouter(prefix="/api", tags=["assets"])

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
FONT_EXT = {".ttf", ".otf", ".woff", ".woff2"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB


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
async def list_assets(user: dict = Depends(current_user)) -> list[dict]:
    rows = query_all("SELECT id, type, filename, tags, slot_hint, created "
                     "FROM assets WHERE user_id = ? ORDER BY created DESC",
                     (user["id"],))
    return rows_to_dicts(rows)


@router.post("/assets")
async def upload_asset(
    file: UploadFile, type: str = Form("image"), tags: str = Form(""),
    user: dict = Depends(current_user),
) -> dict:
    # validate type up front so an unknown value (e.g. "icon") cannot slip past
    # the extension gates below and write an arbitrary extension
    if type not in ("image", "logo", "font"):
        raise HTTPException(400, "invalid type")
    # basename only: strip any directory components (both separators) so a
    # crafted filename like "../../evil" cannot escape the asset directory
    name = (file.filename or "asset").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or name in (".", ".."):
        name = "asset"
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if type == "font" and ext not in FONT_EXT:
        raise HTTPException(400, "not a font file")
    if type in ("image", "logo") and ext not in IMAGE_EXT:
        raise HTTPException(400, "not an image file")

    aid = new_id()
    adir = _asset_dir(aid).resolve()
    dest = (adir / name).resolve()
    # basename stripping misses a Windows drive-relative name like "D:evil.exe"
    # (no separator to strip, but it joins onto another drive's root); confirm
    # the resolved dest still sits directly inside this asset's directory
    if dest.parent != adir:
        raise HTTPException(400, "invalid filename")
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
                    shutil.rmtree(_asset_dir(aid), ignore_errors=True)
                    raise HTTPException(
                        413, f"file exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
                f.write(block)
    except HTTPException:
        raise
    execute(
        "INSERT INTO assets (id, type, filename, tags, user_id, created) "
        "VALUES (?, ?, ?, ?, ?, ?)", (aid, type, name, tags, user["id"], now()),
    )
    warn = None
    if type == "font":
        warn = ("Fonts embed in PDF exports, but PowerPoint stores font names "
                "only — install the font to see it in PPTX."
                if _font_embeddable(dest) else
                "This font's license blocks embedding; it will not be embedded.")
    # Auto-bind the first uploaded logo as the active brand logo, so a user
    # doesn't have to separately open the brand kit panel and pick it from a
    # dropdown. Never clobber a logo the user already chose.
    logo_asset_id = None
    if type == "logo":
        brand = active_brand(user["id"])
        if not brand.get("logo_asset_id"):
            logo_asset_id = aid
            set_setting("brand.active", {**brand, "logo_asset_id": aid}, user["id"])
    return {"id": aid, "font_note": warn, "logo_asset_id": logo_asset_id}


def save_generated_image(
    user_id: str, data: bytes, *, prompt: str, ext: str = ".png",
) -> str:
    """Persist AI-generated image bytes (e.g. Nano Banana output) as an owned
    asset, mirroring upload_asset's own insert. Returns the new asset id;
    callers reference the file as asset://{id}, same as an uploaded image."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(
            f"generated image exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit")
    if ext not in IMAGE_EXT:
        ext = ".png"  # keep the filename extension and the bytes' format agreeing
    aid = new_id()
    filename = f"generated{ext}"
    (_asset_dir(aid) / filename).write_bytes(data)
    execute(
        "INSERT INTO assets (id, type, filename, tags, user_id, created) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (aid, "image", filename, (prompt or "").strip()[:300], user_id, now()),
    )
    return aid


class TagPatch(BaseModel):
    tags: str


@router.patch("/assets/{asset_id}")
async def patch_asset(
    asset_id: str, body: TagPatch, user: dict = Depends(current_user)
) -> dict:
    execute("UPDATE assets SET tags = ? WHERE id = ? AND user_id = ?",
            (body.tags, asset_id, user["id"]))
    return {"ok": True}


@router.delete("/assets/{asset_id}")
async def delete_asset(asset_id: str, user: dict = Depends(current_user)) -> dict:
    # Confirm ownership BEFORE touching the disk: the DB delete is scoped by
    # user_id, but an unconditional rmtree would let one user wipe another
    # user's asset files by passing their asset_id (the row survives, but its
    # backing directory is gone -> the owner's asset breaks).
    row = query_one("SELECT id FROM assets WHERE id = ? AND user_id = ?",
                    (asset_id, user["id"]))
    if row is None:
        raise HTTPException(404, "asset not found")
    execute("DELETE FROM assets WHERE id = ? AND user_id = ?", (asset_id, user["id"]))
    shutil.rmtree(data_dir() / "assets" / asset_id, ignore_errors=True)
    # clear any brand-kit reference to the now-deleted asset so a preview does
    # not render a broken image, and a fresh logo upload can auto-bind again
    brand = active_brand(user["id"])
    ref_fields = ("logo_asset_id", "heading_asset_id", "body_asset_id")
    if any(brand.get(f) == asset_id for f in ref_fields):
        set_setting(
            "brand.active",
            {**brand, **{f: None for f in ref_fields if brand.get(f) == asset_id}},
            user["id"],
        )
    return {"ok": True}


@router.get("/assets/{asset_id}/file")
async def serve_asset(asset_id: str, user: dict = Depends(current_user)) -> FileResponse:
    row = query_one("SELECT filename FROM assets WHERE id = ? AND user_id = ?",
                    (asset_id, user["id"]))
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


def resolve_image(query: str, user_id: str | None) -> str | None:
    """Pick the best image asset for a slot query by tag/filename overlap,
    within the given user's asset library."""
    q = _words(query)
    if not q or user_id is None:
        return None
    best, best_score = None, 0
    for a in rows_to_dicts(query_all(
            "SELECT id, filename, tags FROM assets "
            "WHERE type IN ('image','logo') AND user_id = ?", (user_id,))):
        score = len(q & (_words(a["tags"]) | _words(a["filename"])))
        if score > best_score:
            best, best_score = a["id"], score
    return best


# ----------------------------------------------------------------- brand kit

class BrandKit(BaseModel):
    primary: str | None = None
    accent: str | None = None
    logo_asset_id: str | None = None
    # Optional brand fonts. *_family is the font's name (what renderers set on
    # runs / CSS); *_asset_id points at an uploaded font file to embed.
    heading_family: str | None = None
    heading_asset_id: str | None = None
    body_family: str | None = None
    body_asset_id: str | None = None


def active_brand(user_id: str | None) -> dict:
    return get_setting("brand.active", user_id) or {}


def _asset_file(asset_id: str | None, user_id: str | None):
    """Absolute on-disk path for an owned asset, or None."""
    if not asset_id:
        return None
    row = query_one("SELECT filename FROM assets WHERE id = ? AND user_id = ?",
                    (asset_id, user_id))
    if row is None:
        return None
    path = data_dir() / "assets" / asset_id / row["filename"]
    return str(path) if path.is_file() else None


def brand_logo_image(user_id: str | None) -> dict | None:
    """The active brand logo as a docloom Image dict ({"path": ...}), or None
    — used to stamp a logo on every slide / report header at export time."""
    brand = active_brand(user_id)
    path = _asset_file(brand.get("logo_asset_id"), user_id)
    return {"path": path, "alt": "logo"} if path else None


@router.get("/brand-kit")
async def get_brand(user: dict = Depends(current_user)) -> dict:
    return active_brand(user["id"])


@router.put("/brand-kit")
async def put_brand(body: BrandKit, user: dict = Depends(current_user)) -> dict:
    set_setting("brand.active", body.model_dump(), user["id"])
    return {"ok": True}


def apply_brand(theme_json: dict, user_id: str | None) -> dict:
    """Overlay the user's active brand (primary, accent, fonts) onto a theme's
    tokens. Each token is only overridden if the user actually set it, so an
    unset primary keeps the theme's own (a brand accent must never also stand
    in for primary, or every theme collapses to one flat color).

    Font families set the renderer-facing name; a matching uploaded font file
    is threaded through as *_src so self-contained/font-path renderers embed
    the real font (HTML @font-face, PDF font path)."""
    brand = active_brand(user_id)
    primary = brand.get("primary")
    if primary:
        theme_json = {**theme_json, "primary": primary}
    accent = brand.get("accent")
    if accent:
        theme_json = {**theme_json, "accent": accent}
    heading_family = brand.get("heading_family")
    if heading_family:
        theme_json = {**theme_json, "font_heading": heading_family}
    body_family = brand.get("body_family")
    if body_family:
        theme_json = {**theme_json, "font_body": body_family}
    heading_src = _asset_file(brand.get("heading_asset_id"), user_id)
    if heading_src:
        theme_json = {**theme_json, "font_heading_src": heading_src}
    body_src = _asset_file(brand.get("body_asset_id"), user_id)
    if body_src:
        theme_json = {**theme_json, "font_body_src": body_src}
    return theme_json
