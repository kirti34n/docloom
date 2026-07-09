"""Source ingestion: parse -> sanitize -> chunk -> embed.

Parsers stay lightweight (pdfplumber/pypdf, python-docx, trafilatura) -- no
torch. Text is sanitized at the boundary (control/bidi/zero-width chars) and
treated as data, never instructions."""

from __future__ import annotations

import asyncio
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


def parse_pptx(path: Path) -> str:
    """Extract text from every shape (and speaker notes) of a .pptx deck."""
    from pptx import Presentation

    out: list[str] = []
    for i, slide in enumerate(Presentation(str(path)).slides, start=1):
        parts: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                parts.append(shape.text_frame.text.strip())
            if shape.has_table:
                for row in shape.table.rows:
                    cells = [c.text.strip() for c in row.cells]
                    if any(cells):
                        parts.append(" | ".join(cells))
        if slide.has_notes_slide:
            notes = (slide.notes_slide.notes_text_frame.text or "").strip()
            if notes:
                parts.append(f"[notes] {notes}")
        if parts:
            out.append(f"Slide {i}\n" + "\n".join(parts))
    return "\n\n".join(out)


def parse_xlsx(path: Path) -> str:
    """Flatten every sheet of a workbook to `col | col | col` rows."""
    from openpyxl import load_workbook

    wb = load_workbook(str(path), read_only=True, data_only=True)
    try:
        out: list[str] = []
        for ws in wb.worksheets:
            rows = [
                " | ".join("" if v is None else str(v) for v in row).rstrip(" |")
                for row in ws.iter_rows(values_only=True)
                if any(v is not None and str(v).strip() for v in row)
            ]
            if rows:
                out.append(f"Sheet: {ws.title}\n" + "\n".join(rows))
        return "\n\n".join(out)
    finally:
        wb.close()


def parse_epub(path: Path) -> str:
    """Extract reading-order text from an EPUB (ebooklib + a tiny HTML strip)."""
    from ebooklib import ITEM_DOCUMENT, epub

    book = epub.read_epub(str(path))
    tag = re.compile(r"<[^>]+>")
    out: list[str] = []
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        html = item.get_content().decode("utf-8", "ignore")
        # drop scripts/styles, then strip tags; entities are left as-is (rare)
        html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
        text = re.sub(r"(?is)<br\s*/?>|</p>|</div>|</h[1-6]>", "\n", html)
        text = tag.sub("", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if text:
            out.append(text)
    return "\n\n".join(out)


def parse_csv(text: str) -> str:
    import csv
    import io

    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample) if sample.strip() else csv.excel
    except csv.Error:
        dialect = csv.excel
    return "\n".join(
        " | ".join(cell.strip() for cell in row)
        for row in csv.reader(io.StringIO(text), dialect)
        if any(cell.strip() for cell in row)
    )


def parse_html(text: str) -> str:
    import trafilatura

    return trafilatura.extract(text, include_comments=False, include_tables=True) or text


def read_text_smart(path: Path) -> str:
    """Read a text file without mojibake: UTF-8 first (the common case), then
    sniff the encoding, then a lossless latin-1 fallback (never raises, never
    inserts U+FFFD replacement chars for western text)."""
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(raw).best()
        if best is not None:
            return str(best)
    except Exception:
        pass
    return raw.decode("latin-1")


_YT = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|embed/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})"
)


def youtube_id(url: str) -> str | None:
    m = _YT.search(url)
    return m.group(1) if m else None


def fetch_youtube(url: str, video_id: str) -> tuple[str, str]:
    """(title, transcript) for a YouTube link. Needs youtube-transcript-api."""
    from youtube_transcript_api import YouTubeTranscriptApi

    segments = YouTubeTranscriptApi.get_transcript(video_id)
    text = " ".join(s["text"].strip() for s in segments if s.get("text", "").strip())
    if not text:
        raise ValueError("no transcript available for this video")
    # cheap title: the page <title>, falling back to the id
    title = f"YouTube {video_id}"
    try:
        with httpx.Client(timeout=10, follow_redirects=True,
                          headers={"User-Agent": "docloom-studio/0.1"}) as client:
            html = client.get(url).text
        m = re.search(r"<title>(.*?)</title>", html, re.S)
        if m:
            title = re.sub(r"\s*-\s*YouTube\s*$", "", m.group(1)).strip() or title
    except Exception:
        pass
    return title, text


def fetch_url(url: str) -> tuple[str, str]:
    """Return (title, main-text) for a web page — or a transcript for YouTube."""
    vid = youtube_id(url)
    if vid:
        return fetch_youtube(url, vid)

    import trafilatura

    with httpx.Client(timeout=20, follow_redirects=True,
                      headers={"User-Agent": "docloom-studio/0.1"}) as client:
        resp = client.get(url)
        resp.raise_for_status()  # 404/403/500 must fail ingestion, not feed error HTML
        html = resp.text
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
            elif ext == ".pptx":
                chunks += chunk_text(sanitize(parse_pptx(p)), source_id)
            elif ext in (".xlsx", ".xlsm"):
                chunks += chunk_text(sanitize(parse_xlsx(p)), source_id)
            elif ext == ".csv":
                chunks += chunk_text(sanitize(parse_csv(read_text_smart(p))), source_id)
            elif ext in (".html", ".htm"):
                chunks += chunk_text(sanitize(parse_html(read_text_smart(p))), source_id)
            elif ext == ".epub":
                chunks += chunk_text(sanitize(parse_epub(p)), source_id)
            else:  # txt, md, and other text — encoding-sniffed
                chunks += chunk_text(sanitize(read_text_smart(p)), source_id)
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
    except asyncio.CancelledError:
        # job cancel or server shutdown: don't leave the source stuck in 'pending'
        meta["error"] = "ingestion cancelled"
        execute("UPDATE sources SET status = 'failed', meta_json = ? WHERE id = ?",
                (json.dumps(meta), source_id))
        raise  # propagate cancellation
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
