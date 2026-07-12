"""Re-audit regressions for the Markdown renderer: footnote dedup, image alt
backslash escaping, and caption/subtitle emphasis-boundary whitespace."""

from docloom import Document, Image, Paragraph, Source, Span, Table, render


def test_markdown_footnotes_dedupe_duplicate_source_ids(tmp_path):
    # duplicate source ids are permitted by the IR (only linted); _footnotes_md
    # once emitted a definition per source, so a dupe id produced two "[^1]:"
    # lines and leaked the duplicate title. It must dedupe like the siblings.
    doc = Document(
        title="T",
        blocks=[Paragraph(text=[Span(text="claim", cite="a")])],
        sources=[
            Source(id="a", title="Alpha"),
            Source(id="a", title="AlphaDup"),
            Source(id="b", title="Beta"),
        ],
    )
    text = render(doc, "md", tmp_path / "footnotes.md").read_text(encoding="utf-8")
    assert text.count("[^1]:") == 1  # the first definition for id 'a', not two
    assert "AlphaDup" not in text  # the duplicate's title must not leak


def test_markdown_image_alt_trailing_backslash_does_not_escape_bracket(tmp_path):
    # an alt ending in a backslash (e.g. "C:\\") once emitted `![C:\](path)`,
    # whose trailing "\]" escaped the closing bracket and broke the image. The
    # backslash must be doubled so the bracket closes: `![C:\\](path)`.
    img = tmp_path / "pic.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    doc = Document(title="T", blocks=[Image(path=str(img), alt="C:\\")])
    text = render(doc, "md", tmp_path / "image.md").read_text(encoding="utf-8")
    # the buggy single-backslash-then-bracket form escapes the bracket; gone now.
    # (a naive "\](" check is useless here: the fixed "\\](" contains it as a
    # substring, so we assert the odd-backslash bug form is absent instead.)
    assert "![C:\\](" not in text  # single backslash escaping "]" must not appear
    assert "![C:\\\\](" in text  # doubled so the closing bracket is literal


def test_markdown_table_caption_trailing_space_hoisted_outside_emphasis(tmp_path):
    # a caption with trailing whitespace was emitted as `*revenue *`; per
    # CommonMark a "*" next to whitespace cannot close emphasis, so it rendered
    # literal asterisks. The boundary space must be hoisted outside the markers.
    doc = Document(
        title="T",
        blocks=[Table(header=["h"], rows=[["x"]], caption="revenue ")],
    )
    text = render(doc, "md", tmp_path / "caption.md").read_text(encoding="utf-8")
    assert "*revenue *" not in text  # whitespace hoisted out -> "*revenue* "
