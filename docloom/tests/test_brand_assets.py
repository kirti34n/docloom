"""Brand assets across formats: a document logo renders into HTML/DOCX headers,
custom brand fonts embed as @font-face in HTML, and PDF/typst source carries
the logo + font resolution."""

import base64
from pathlib import Path

from docloom import render
from docloom.ir import Document, Image
from docloom.render.html import to_html
from docloom.render.typst import to_typst
from docloom.theme import Theme

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mP8"
    "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

# a tiny but structurally-valid WOFF file header ("wOFF" signature) is enough
# to prove embedding; the bytes don't need to be a loadable font for HTML.
_FONT = b"wOFF" + b"\x00" * 60


def _png(tmp_path: Path) -> str:
    p = tmp_path / "logo.png"
    p.write_bytes(_PNG)
    return str(p)


def _real_png(tmp_path: Path) -> str:
    """A genuinely-decodable PNG (python-docx's image reader is stricter than
    python-pptx's and rejects the 1x1 stub)."""
    from PIL import Image as PILImage

    p = tmp_path / "real.png"
    PILImage.new("RGB", (48, 24), (10, 120, 200)).save(p)
    return str(p)


def _font(tmp_path: Path, name="brand.woff2") -> str:
    p = tmp_path / name
    p.write_bytes(_FONT)
    return str(p)


def test_html_embeds_logo_and_font(tmp_path):
    theme = Theme(font_body="BrandSans", font_body_src=_font(tmp_path))
    doc = Document(title="Report", logo=Image(path=_png(tmp_path)),
                   blocks=[{"type": "paragraph", "text": "hi"}])
    html = to_html(doc, theme)
    assert "@font-face" in html
    assert '"BrandSans"' in html
    assert "data:font/woff2;base64," in html
    assert 'class="brand-logo"' in html
    assert "data:image/png;base64," in html


def test_html_no_font_face_without_src(tmp_path):
    html = to_html(Document(title="Plain", blocks=[{"type": "paragraph", "text": "x"}]),
                   Theme())
    assert "@font-face" not in html


def test_html_missing_font_file_falls_back(tmp_path):
    theme = Theme(font_body="Ghost", font_body_src=str(tmp_path / "nope.woff2"))
    html = to_html(Document(title="T", blocks=[{"type": "paragraph", "text": "x"}]), theme)
    assert "@font-face" not in html  # unreadable src is silently skipped


def test_typst_source_carries_logo_placeholder_resolved(tmp_path):
    # to_typst always emits the placeholder; render() resolves it. Here we just
    # assert the placeholder exists so render() has something to swap.
    src = to_typst(Document(title="T", logo=Image(path=_png(tmp_path)),
                            blocks=[{"type": "paragraph", "text": "x"}]), Theme())
    assert "__DOCLOOM_LOGO__" in src


def test_docx_with_logo_renders(tmp_path):
    doc = Document(title="Branded", logo=Image(path=_real_png(tmp_path)),
                   blocks=[{"type": "paragraph", "text": "body"}])
    out = render(doc, "docx", tmp_path / "out.docx")
    assert out.is_file()
    # the logo image part lands in the package
    import zipfile
    with zipfile.ZipFile(out) as z:
        assert any(n.startswith("word/media/") for n in z.namelist())
