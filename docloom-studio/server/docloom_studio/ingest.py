"""Source ingestion: parse -> sanitize -> chunk -> embed.

Parsers stay lightweight (pdfplumber/pypdf, python-docx, trafilatura) -- no
torch. Text is sanitized at the boundary (control/bidi/zero-width chars) and
treated as data, never instructions."""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

from .db import execute, query_one
from .settings import data_dir

# control chars (minus tab/newline/CR), zero-width chars, bidi overrides, BOM
_UNSAFE = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f"
    "​-‏‪-‮⁦-⁩﻿]"
)

CHUNK_CHARS = 1000
CHUNK_OVERLAP = 150


def sanitize(text: str) -> str:
    return _UNSAFE.sub("", text).replace("\r\n", "\n")


def _source_dir(source_id: str) -> Path:
    d = data_dir() / "sources" / source_id
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------ parsers


def parse_pdf(path: Path) -> list[tuple[int, str]]:
    """Return (page_number, text) pairs."""
    pages: list[tuple[int, str]] = []
    try:
        import pdfplumber

        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                pages.append((i, page.extract_text() or ""))
        if any(t.strip() for _, t in pages):
            return pages
    except Exception:
        pass
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return [(i, (p.extract_text() or "")) for i, p in enumerate(reader.pages, start=1)]


def parse_docx(path: Path) -> str:
    import docx

    doc = docx.Document(str(path))
    return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())


def fetch_url(url: str) -> tuple[str, str]:
    """Return (title, main-text) for a web page."""
    import trafilatura

    with httpx.Client(timeout=20, follow_redirects=True,
                      headers={"User-Agent": "docloom-studio/0.1"}) as client:
        html = client.get(url).text
    text = trafilatura.extract(html, include_comments=False,
                               include_tables=True) or ""
    meta = trafilatura.extract_metadata(html)
    title = (getattr(meta, "title", None) if meta else None) or url
    return title, text


# ------------------------------------------------------------------ chunking


def chunk_text(text: str, source_id: str, page: int | None = None) -> list[dict]:
    """Split into ~CHUNK_CHARS windows with overlap, preferring paragraph
    boundaries. Carries source/page/section metadata."""
    text = text.strip()
    if not text:
        return []
    paras = re.split(r"\n\s*\n", text)
    chunks: list[dict] = []
    buf = ""
    section = ""
    for para in paras:
        para = para.strip()
        if not para:
            continue
        # a short title-case line is treated as a section heading
        if len(para) < 80 and "\n" not in para and para[:1].isupper():
            section = para
        if len(buf) + len(para) + 2 > CHUNK_CHARS and buf:
            chunks.append({"text": buf.strip(), "section": section, "page": page})
            buf = buf[-CHUNK_OVERLAP:] + "\n\n" + para
        else:
            buf = (buf + "\n\n" + para) if buf else para
    if buf.strip():
        chunks.append({"text": buf.strip(), "section": section, "page": page})
    return chunks


# ------------------------------------------------------------------ pipeline


async def ingest_source(source_id: str, ctx=None) -> None:
    """Parse the source, chunk it, embed it. Updates source status."""
    row = query_one("SELECT * FROM sources WHERE id = ?", (source_id,))
    if row is None:
        return
    kind, path, url = row["kind"], row["path"], row["url"]
    meta = json.loads(row["meta_json"])

    try:
        chunks: list[dict] = []
        if kind == "file":
            p = Path(path)
            ext = p.suffix.lower()
            if ext == ".pdf":
                for page_no, page_text in parse_pdf(p):
                    chunks += chunk_text(sanitize(page_text), source_id, page=page_no)
            elif ext == ".docx":
                chunks += chunk_text(sanitize(parse_docx(p)), source_id)
            else:  # txt, md, csv, etc.
                chunks += chunk_text(
                    sanitize(p.read_text(encoding="utf-8", errors="replace")), source_id
                )
        elif kind == "url":
            title, text = fetch_url(url)
            meta["fetched_title"] = title
            chunks += chunk_text(sanitize(text), source_id)
            if not row["title"] or row["title"] == url:
                execute("UPDATE sources SET title = ? WHERE id = ?",
                        (title[:200], source_id))
        elif kind in ("text", "research"):
            chunks += chunk_text(sanitize(meta.get("text", "")), source_id)

        if not chunks:
            raise ValueError("no extractable text")

        for i, c in enumerate(chunks):
            c["source_id"] = source_id
            c["chunk_ix"] = i
        (_source_dir(source_id) / "chunks.jsonl").write_text(
            "\n".join(json.dumps(c, ensure_ascii=False) for c in chunks),
            encoding="utf-8",
        )
        if ctx:
            ctx.emit("chunk", "done", detail=f"{len(chunks)} chunks")

        from .embeddings import embed_source

        await embed_source(source_id, [c["text"] for c in chunks])
        execute("UPDATE sources SET status = 'ready', meta_json = ? WHERE id = ?",
                (json.dumps(meta), source_id))
        if ctx:
            ctx.emit("embed", "done")
    except Exception as e:
        meta["error"] = str(e)[:300]
        execute("UPDATE sources SET status = 'failed', meta_json = ? WHERE id = ?",
                (json.dumps(meta), source_id))
        if ctx:
            ctx.emit("ingest", "failed", detail=str(e)[:200])


def load_chunks(source_id: str) -> list[dict]:
    path = _source_dir(source_id) / "chunks.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
