"""Regression tests for the typst renderer's image-format, malformed-SVG,
image-path-collision, and chart-data-table fixes.

- typst embeds a raster by the DECODED content format (PIL), not the file
  extension, and passes that format explicitly to typst's `#image(...,
  format:)`, so a mislabeled file (e.g. JPEG bytes saved as figure1.png)
  compiles instead of aborting the whole PDF.
- an SVG file is gated on real XML well-formedness (with an <svg> root), not
  the substring "<svg", so a truncated or entity-broken file is skipped
  instead of aborting the compile.
- render()'s image-ref rewriting is a single left-to-right pass over the
  original .typ source, so a generated local name (imgN.ext) can't collide
  with and clobber a real authored relative path.
- the Chart data-table fallback formats values through chart_svg's
  finite-guard/formatter (matches docx.py), so non-finite values render as a
  blank cell instead of literal "nan"/"inf", and large magnitudes render
  with thousands separators instead of lossy scientific notation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image as PILImage

from docloom import Chart, Document, Image as ImageBlock, Paragraph, Series, Theme, render
from docloom.render.typst import _embeddable, to_typst

# ------------------------------------------------------- image format/content


def test_typst_image_format_matches_decoded_content_not_extension(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    PILImage.new("RGB", (80, 50)).save("figure1.png", "JPEG")
    typ = to_typst(
        Document(title="t", blocks=[ImageBlock(path="figure1.png")]), Theme()
    )
    assert 'format: "jpg"' in typ


def test_typst_pdf_compiles_mislabeled_raster(tmp_path):
    pytest.importorskip("typst")
    p = tmp_path / "figure1.png"
    PILImage.new("RGB", (80, 50)).save(str(p), "JPEG")
    doc = Document(
        title="t",
        blocks=[
            Paragraph(text="summary"),
            ImageBlock(path=str(p)),
            Paragraph(text="conclusion"),
        ],
    )
    out = render(doc, "pdf", tmp_path / "o.pdf")
    assert out.read_bytes()[:4] == b"%PDF"


# --------------------------------------------------------------- malformed svg


def test_svg_gate_rejects_truncated_but_svg_tagged_file(tmp_path):
    bad = tmp_path / "bad.svg"
    bad.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="60">'
        '<rect width="100"',
        encoding="utf-8",
    )
    good = tmp_path / "good.svg"
    good.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
        '<rect width="10" height="10"/></svg>',
        encoding="utf-8",
    )
    assert _embeddable(bad) is False
    assert _embeddable(good) is True


def test_typst_pdf_skips_malformed_svg_instead_of_aborting(tmp_path):
    pytest.importorskip("typst")
    bad = tmp_path / "bad.svg"
    bad.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="60">'
        '<rect width="100"',
        encoding="utf-8",
    )
    doc = Document(
        title="t",
        blocks=[
            Paragraph(text="before"),
            ImageBlock(path=str(bad)),
            Paragraph(text="after"),
        ],
    )
    out = render(doc, "pdf", tmp_path / "o.pdf")
    assert out.read_bytes()[:4] == b"%PDF"


# ---------------------------------------------------------- image path collision


def test_typst_image_path_collision_does_not_drop_or_duplicate(tmp_path, monkeypatch):
    pytest.importorskip("typst")
    import typst as typst_pkg

    monkeypatch.chdir(tmp_path)
    (tmp_path / "assets").mkdir()
    PILImage.new("RGB", (10, 10), (0, 0, 255)).save(str(tmp_path / "assets" / "photo.png"))
    PILImage.new("RGB", (10, 10), (255, 0, 0)).save(str(tmp_path / "img0.png"))

    doc = Document(
        title="t",
        blocks=[ImageBlock(path="assets/photo.png"), ImageBlock(path="img0.png")],
    )

    captured: dict[str, str] = {}

    def fake_compile(path, font_paths=None):
        captured["source"] = Path(path).read_text(encoding="utf-8")
        return b"%PDF-stub"

    monkeypatch.setattr(typst_pkg, "compile", fake_compile)

    render(doc, "pdf", tmp_path / "o.pdf")
    src = captured["source"]
    assert src.count('#image("img0.png"') == 1
    assert src.count('#image("img1.png"') == 1


# --------------------------------------------------------- chart table fallback


def test_typst_chart_table_fallback_keeps_large_numbers():
    typ = to_typst(
        Document(
            title="t",
            blocks=[
                Chart(
                    chart="pie",
                    title="Rev",
                    labels=["a", "b"],
                    series=[Series(name="usd", values=[-1234567.0, -1000000.0])],
                ),
            ],
        ),
        Theme(),
    )
    assert "1,234,567" in typ
    assert "1,000,000" in typ
    assert "1.23457" not in typ


def test_typst_chart_table_fallback_blanks_nonfinite_values():
    typ = to_typst(
        Document(
            title="t",
            blocks=[
                Chart(
                    chart="column",
                    title="C",
                    labels=["a", "b"],
                    series=[Series(name="s", values=[float("nan"), float("inf")])],
                ),
            ],
        ),
        Theme(),
    )
    assert "[nan]" not in typ
    assert "[inf]" not in typ
