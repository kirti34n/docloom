"""Render dispatch: one Document IR in, native files out."""

from __future__ import annotations

import importlib
import re
from pathlib import Path

from ..ir import Document
from ..theme import DEFAULT, Theme

# fmt -> (module under docloom.render, extension)
FORMATS: dict[str, tuple[str, str]] = {
    "pptx": ("pptx", ".pptx"),
    "docx": ("docx", ".docx"),
    "xlsx": ("xlsx", ".xlsx"),
    "pdf": ("typst", ".pdf"),
    "typ": ("typst", ".typ"),
    "html": ("html", ".html"),
    "md": ("markdown", ".md"),
}


class RenderError(Exception):
    pass


_MAX_SLUG = 80


def slug(title: str) -> str:
    # \w keeps unicode letters so non-Latin titles get distinct filenames
    s = re.sub(r"[^\w]+", "-", title).strip("-_").lower()
    if len(s) > _MAX_SLUG:
        s = s[:_MAX_SLUG].rstrip("-_")
    return s or "document"


def render(
    doc: Document,
    fmt: str,
    out_path: str | Path | None = None,
    theme: Theme | None = None,
) -> Path:
    """Render `doc` to `fmt`. Returns the written file's path.

    Every renderer module exposes  render(doc, theme, out_path) -> Path
    (the typst module additionally distinguishes .typ source from .pdf).
    """
    if fmt not in FORMATS:
        raise RenderError(f"unknown format {fmt!r}; expected one of {sorted(FORMATS)}")
    module_name, ext = FORMATS[fmt]
    out = Path(out_path) if out_path else Path(slug(doc.title) + ext)
    # A trailing slash signals directory intent even if the directory does
    # not exist yet, so out.is_dir() alone would miss it and write a file
    # with no extension in the directory's place.
    looks_like_dir = out.is_dir() or (
        out_path is not None and str(out_path).endswith(("/", "\\"))
    )
    if looks_like_dir:
        out = out / (slug(doc.title) + ext)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise RenderError(f"cannot create output directory {out.parent}: {e}") from e

    module = importlib.import_module(f".{module_name}", __package__)
    try:
        if fmt == "typ":
            out.write_text(module.to_typst(doc, theme or DEFAULT), encoding="utf-8")
            return out
        return module.render(doc, theme or DEFAULT, out)
    except OSError as e:
        raise RenderError(f"cannot write output file {out}: {e}") from e
