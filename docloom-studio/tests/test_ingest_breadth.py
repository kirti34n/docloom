"""EPUB parsing, YouTube-link detection, and upload guardrails."""

import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-ingb-"))

import io  # noqa: E402
import zipfile  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from docloom_studio import ingest  # noqa: E402
from docloom_studio.db import execute, init_db, new_id, now  # noqa: E402
from docloom_studio.main import app  # noqa: E402


# ---- YouTube detection (pure) --------------------------------------------

@pytest.mark.parametrize("url,vid", [
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ("https://www.youtube.com/shorts/abcdefghijk", "abcdefghijk"),
    ("https://youtube.com/watch?list=x&v=dQw4w9WgXcQ&t=3", "dQw4w9WgXcQ"),
    ("https://example.com/watch?v=nope", None),
])
def test_youtube_id(url, vid):
    assert ingest.youtube_id(url) == vid


# ---- EPUB parsing --------------------------------------------------------

def _make_epub(path: Path) -> None:
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier("id1")
    book.set_title("Test Book")
    book.set_language("en")
    ch = epub.EpubHtml(title="Ch1", file_name="ch1.xhtml")
    ch.content = "<html><body><h1>Chapter 1</h1><p>The quick brown fox.</p>" \
                 "<script>ignore()</script></body></html>"
    book.add_item(ch)
    book.spine = [ch]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(path), book)


def test_parse_epub(tmp_path):
    p = tmp_path / "b.epub"
    _make_epub(p)
    text = ingest.parse_epub(p)
    assert "Chapter 1" in text and "quick brown fox" in text
    assert "ignore()" not in text  # scripts stripped


# ---- upload guardrails ----------------------------------------------------

@pytest.fixture()
def _client_nb():
    init_db()
    with TestClient(app) as client:
        client.post("/api/auth/register",
                    json={"email": f"{new_id()}@t.local", "password": "password123"})
        ws = client.get("/api/workspaces").json()
        nb = client.post("/api/notebooks",
                         json={"name": "n", "workspace_id": ws[0]["id"]}).json()
        yield client, nb["id"]


def test_rejects_unsupported_extension(_client_nb):
    client, nb = _client_nb
    r = client.post(f"/api/notebooks/{nb}/sources/file",
                    files={"file": ("evil.exe", b"MZ\x90", "application/octet-stream")})
    assert r.status_code == 415


def test_rejects_oversized_upload(_client_nb, monkeypatch):
    client, nb = _client_nb
    monkeypatch.setattr("docloom_studio.sources.MAX_UPLOAD_BYTES", 1024)
    big = b"x" * 5000
    r = client.post(f"/api/notebooks/{nb}/sources/file",
                    files={"file": ("big.txt", big, "text/plain")})
    assert r.status_code == 413


def test_accepts_supported_file(_client_nb):
    client, nb = _client_nb
    r = client.post(f"/api/notebooks/{nb}/sources/file",
                    files={"file": ("notes.md", b"# hello\n\nworld", "text/markdown")})
    assert r.status_code == 200
    assert "source_id" in r.json()
