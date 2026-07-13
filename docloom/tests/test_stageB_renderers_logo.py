"""Stage B (CONTRACT C6): report-header logo consistency across renderers.

Before this: html.py rendered the brand logo top-left, in normal document
flow, before <h1>; markdown.py emitted no logo at all. Both must now carry
the logo near the title, and html's must sit in a right-aligned flex slot
consistent with docx/typst (which already right-align)."""

import base64
import re
from pathlib import Path

from docloom import Document, Image, render

# a minimal valid 1x1 PNG (same fixture used by test_pptx_logo.py)
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mP8"
    "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _logo_path(tmp_path: Path) -> str:
    p = tmp_path / "logo.png"
    p.write_bytes(_PNG)
    return str(p)


def test_html_logo_is_right_aligned_not_left_in_flow(tmp_path):
    doc = Document(title="Branded", logo=Image(path=_logo_path(tmp_path), alt="Acme"))
    out = render(doc, "html", tmp_path / "out.html")
    text = out.read_text(encoding="utf-8")

    assert 'class="brand-logo"' in text
    assert "data:image/png;base64," in text  # embedded, self-contained like the rest of html.py

    # the header must be a real flex row with the logo pushed to the far
    # end, not the old block-flow left placement
    css = text.split("<style>", 1)[1].split("</style>", 1)[0]
    assert "header{display:flex" in css.replace(" ", "")
    assert "justify-content:space-between" in css.replace(" ", "")

    # the logo must come after the title in source order (inside <header>,
    # after the title/subtitle/meta wrapper closes) so space-between renders
    # it top-right instead of the old placement directly before <h1>
    header = text.split("<header>", 1)[1].split("</header>", 1)[0]
    assert header.index("<h1>") < header.index('class="brand-logo"')


def test_html_without_logo_has_no_brand_logo_markup(tmp_path):
    doc = Document(title="Plain")
    out = render(doc, "html", tmp_path / "plain.html")
    text = out.read_text(encoding="utf-8")
    # the CSS rule for .brand-logo is static (always emitted); what must be
    # absent is an actual <img> tag when the document carries no logo
    body = text.split("</style>", 1)[1]
    assert '<img class="brand-logo"' not in body
    assert "<h1>Plain</h1>" in body


def test_markdown_references_the_logo_near_the_title(tmp_path):
    doc = Document(title="Branded", logo=Image(path=_logo_path(tmp_path), alt="Acme"))
    out = render(doc, "md", tmp_path / "out.md")
    text = out.read_text(encoding="utf-8")

    assert "![Acme]" in text  # markdown.py used to drop the logo entirely
    logo_pos = text.index("![Acme]")
    title_pos = text.index("# Branded")
    assert logo_pos < title_pos  # near (here: just above) the title

    # the reference must point at a real, copied file next to the .md (the
    # same asset-copier convention every other image in this renderer uses),
    # not a dangling path back to the generating machine's filesystem
    ref = text[logo_pos:title_pos]
    m = re.search(r"\((\S+?)\)", ref)
    assert m, f"no markdown image target found in {ref!r}"
    ref_path = m.group(1).strip("<>")
    assert (out.parent / ref_path).is_file()


def test_markdown_without_logo_emits_no_image_reference(tmp_path):
    doc = Document(title="Plain")
    out = render(doc, "md", tmp_path / "plain.md")
    text = out.read_text(encoding="utf-8")
    assert "![" not in text.split("\n", 1)[0]
    assert text.startswith("# Plain")


def test_markdown_logo_without_assets_mode_falls_back_to_raw_path(tmp_path):
    """render(..., assets=False) is used elsewhere in this renderer to skip
    the copy-next-to-output step; the logo must follow the same convention
    as every other image (raw path, uncopied) instead of silently
    disappearing."""
    from docloom.render.markdown import render as render_md
    from docloom.theme import DEFAULT

    logo = _logo_path(tmp_path)
    doc = Document(title="Branded", logo=Image(path=logo, alt="Acme"))
    out = render_md(doc, DEFAULT, tmp_path / "noassets.md", assets=False)
    text = out.read_text(encoding="utf-8")
    assert f"![Acme]({logo})" in text
