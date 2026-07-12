"""Regression: chunks.jsonl records containing the Unicode line separators
U+0085 (NEL), U+2028 (LINE SEPARATOR), and U+2029 (PARAGRAPH SEPARATOR) must
round-trip through load_chunks. The old load_chunks read the file with
str.splitlines(), which treats all three as line boundaries and split a single
JSON record across physical lines, so json.loads raised JSONDecodeError and the
whole notebook's retrieval/generation broke. sanitize() must also strip these
chars so they never reach embeddings/rendered text going forward."""

import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-uni-"))

import json  # noqa: E402

from docloom_studio.ingest import _source_dir, load_chunks, sanitize  # noqa: E402

NEL = chr(0x85)        # U+0085 NEL
LINE_SEP = chr(0x2028)  # U+2028 LINE SEPARATOR
PARA_SEP = chr(0x2029)  # U+2029 PARAGRAPH SEPARATOR


def test_load_chunks_survives_unicode_separators():
    source_id = "unicode-sep-src"
    chunks = [
        {"text": f"first line{LINE_SEP}second line", "section": "", "page": None,
         "source_id": source_id, "chunk_ix": 0},
        {"text": f"alpha{NEL}beta{PARA_SEP}gamma", "section": "", "page": None,
         "source_id": source_id, "chunk_ix": 1},
    ]
    # write exactly as ingest_source does: json.dumps(ensure_ascii=False) leaves
    # the separators literal, one record per real newline
    (_source_dir(source_id) / "chunks.jsonl").write_text(
        "\n".join(json.dumps(c, ensure_ascii=False) for c in chunks),
        encoding="utf-8",
    )

    loaded = load_chunks(source_id)  # must not raise JSONDecodeError

    assert len(loaded) == 2
    assert loaded[0]["text"] == f"first line{LINE_SEP}second line"
    assert loaded[1]["text"] == f"alpha{NEL}beta{PARA_SEP}gamma"
    # the separators survive the round-trip intact (load_chunks does not strip)
    assert LINE_SEP in loaded[0]["text"]
    assert NEL in loaded[1]["text"] and PARA_SEP in loaded[1]["text"]


def test_sanitize_strips_separator_chars():
    assert sanitize(f"a{NEL}b{LINE_SEP}c{PARA_SEP}d") == "abcd"
    # ordinary newline/tab must be preserved, only the exotic separators go
    assert sanitize("keep\nthis\ttext") == "keep\nthis\ttext"
