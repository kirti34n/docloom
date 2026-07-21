"""Regression tests for the ultracode html-markdown audit round: footnote
url/date escaping+scheme-filtering, image/diagram alt full-escaping, GFM
strikethrough/ampersand-entity escaping in _esc_md, and code-span flanking-
space preservation."""

from __future__ import annotations

import html
import re

from markdown_it import MarkdownIt

from docloom import Document, Image, Paragraph, Source, Span, render
from docloom.render.markdown import _code_span, _esc_md


def test_markdown_footnote_url_and_date_are_escaped_and_scheme_filtered(tmp_path):
    # a hostile Source.url/date once reached the footnote raw (no escaping, no
    # scheme check), unlike html.py which escapes+scheme-filters the same
    # fields. An <img onerror=...> in url, and a javascript: link smuggled
    # through date, must both be neutralized in the rendered markdown.
    doc = Document(
        title="T",
        blocks=[Paragraph(text=[Span(text="claim", cite="s1")])],
        sources=[
            Source(
                id="s1",
                title="Title",
                url="<img src=x onerror=alert(1)>",
                date="[click](javascript:alert(1))",
            )
        ],
    )
    text = render(doc, "md", tmp_path / "footnote.md").read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if ln.startswith("[^1]:"))
    assert "<img src=x onerror=alert(1)>" not in line  # raw HTML must not survive
    assert "\\<img" in line  # escaped instead
    assert "\\[click\\]" in line  # date's markdown link syntax neutralized

    # html=True (raw HTML passthrough) mirrors many downstream renderers
    # (GitHub, docs sites); with the bug, the raw "<img ...>" text passed
    # through untouched and became a live tag. The escaped form must not.
    md = MarkdownIt("commonmark", {"html": True})
    rendered_html = md.render(text)
    assert "<img" not in rendered_html  # no live img tag anywhere in the doc
    assert "&lt;img" in rendered_html  # the escaped form survives, inert


def test_markdown_footnote_safe_url_becomes_a_working_link(tmp_path):
    doc = Document(
        title="T",
        blocks=[Paragraph(text=[Span(text="claim", cite="s2")])],
        sources=[Source(id="s2", title="Good", url="https://example.com/a", date="2026")],
    )
    text = render(doc, "md", tmp_path / "footnote_ok.md").read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if ln.startswith("[^1]:"))
    assert "[https://example.com/a](https://example.com/a)" in line


def test_markdown_image_alt_preserves_words_with_inline_specials(tmp_path):
    # a partial 3-char alt escape (only \, [, ]) let backticks/angle-brackets
    # open inline markup, dropping enclosed authored words from the rendered
    # alt attribute. The full _esc_md escaper must keep every word.
    img = tmp_path / "pic.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    md_engine = MarkdownIt("commonmark")

    doc = Document(title="T", blocks=[Image(path=str(img), alt="The <Order> service flow")])
    text = render(doc, "md", tmp_path / "image1.md").read_text(encoding="utf-8")
    rendered = md_engine.render(text)
    m = re.search(r'<img[^>]*alt="([^"]*)"', rendered)
    assert m is not None
    assert "Order" in html.unescape(m.group(1))

    doc2 = Document(title="T", blocks=[Image(path=str(img), alt="The `orders` table schema")])
    text2 = render(doc2, "md", tmp_path / "image2.md").read_text(encoding="utf-8")
    rendered2 = md_engine.render(text2)
    m2 = re.search(r'<img[^>]*alt="([^"]*)"', rendered2)
    assert m2 is not None
    assert "orders" in html.unescape(m2.group(1))


def test_markdown_image_alt_trailing_backslash_still_does_not_escape_bracket(tmp_path):
    # existing behavior for the prior fix must not regress under the new
    # full escaper: a trailing backslash must double, not escape the "]".
    img = tmp_path / "pic.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    doc = Document(title="T", blocks=[Image(path=str(img), alt="C:\\")])
    text = render(doc, "md", tmp_path / "image3.md").read_text(encoding="utf-8")
    assert "![C:\\](" not in text
    assert "![C:\\\\](" in text


def test_esc_md_neutralizes_gfm_strikethrough():
    # _esc_md did not escape '~', so authored "~~text~~" rendered as GFM
    # strikethrough (a silent visual change html.py does not have).
    gfm = MarkdownIt().enable(["strikethrough"])
    escaped = _esc_md("he said ~~never~~ mind")
    rendered = gfm.render(escaped)
    assert "<s>" not in rendered
    assert "never" in rendered


def test_esc_md_escapes_ampersand_so_entities_do_not_decode():
    # _esc_md did not escape '&', so a literal "&amp;" in authored text was
    # decoded by the markdown parser into "&", silently changing the text.
    cm = MarkdownIt("commonmark")
    escaped = _esc_md("use &amp; for the ampersand")
    rendered_text = re.sub("<[^>]+>", "", cm.render(escaped)).strip()
    assert html.unescape(rendered_text) == "use &amp; for the ampersand"


def test_code_span_preserves_flanking_spaces():
    # CommonMark strips one leading+trailing space from a code span whose
    # content begins AND ends with a space (and isn't all-space); _code_span
    # must pad so the round trip preserves the authored content exactly.
    cm = MarkdownIt("commonmark")
    for raw in ("  x  ", " a "):
        emitted = _code_span(raw)
        tokens = cm.parseInline(emitted, {})[0].children
        read_back = "".join(t.content for t in tokens if t.type == "code_inline")
        assert read_back == raw

    for raw in ("`x`", "a ", " a", " ", "  ", "   ", "  `y`  "):
        emitted = _code_span(raw)
        tokens = cm.parseInline(emitted, {})[0].children
        read_back = "".join(t.content for t in tokens if t.type == "code_inline")
        assert read_back == raw
