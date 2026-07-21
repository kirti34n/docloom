"""Regression tests for the ir-theme area (AREA: ir-theme).

1. Theme.font_heading/font_body sanitize forbidden control chars/surrogates
   at construction (they now use SafeStr), instead of letting them reach and
   crash lxml/utf-8 inside the pptx/docx/xlsx renderers.
2. render.slug() bounds its output length so a long Document.title cannot
   push the derived output path past Windows MAX_PATH, and render() surfaces
   any residual OSError while writing the output file as a RenderError
   instead of a bare OSError/FileNotFoundError.
"""

from __future__ import annotations

import sys

import pytest

from docloom import Document, RenderError, Theme, render
from docloom.ir import Column, Paragraph, Sheet, Slide
from docloom.render import slug


def test_theme_font_control_chars(tmp_path):
    assert Theme(font_body="Ar\x00ial").font_body == "Arial"
    assert Theme(font_heading="A\ud800B").font_heading == "AB"

    th = Theme(font_heading="H\x00d", font_body="A\ud800B")
    doc = Document(
        title="D",
        blocks=[Paragraph(text="x")],
        slides=[Slide(title="S", blocks=[Paragraph(text="x")])],
        sheets=[Sheet(name="S", columns=[Column(header="a")], rows=[["1"]])],
    )
    for fmt in ("pptx", "docx", "xlsx"):
        out = render(doc, fmt, tmp_path / f"o.{fmt}", th)
        assert out.is_file()


def test_slug_bounded_and_long_title_renders(tmp_path, monkeypatch):
    assert len(slug("A" * 300)) <= 80

    monkeypatch.chdir(tmp_path)
    p = render(Document(title="A" * 300, blocks=[]), "md")
    assert p.is_file()

    if sys.platform == "win32":
        over = tmp_path / ("x" * 250 + ".md")
        with pytest.raises(RenderError):
            render(Document(title="t", blocks=[]), "md", over)
