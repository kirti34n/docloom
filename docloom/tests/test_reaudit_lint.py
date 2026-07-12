"""Re-audit regression tests for the lint severity fixes: layouts that
render fine must not emit a blocking severity="error" finding that would
hard-block the CLI render and the studio export (HTTP 422).

  - hero renders its body in a short bottom caption band, so it keeps the
    half budget, but as a non-blocking warning (not the error that wrongly
    hard-blocked export of an otherwise-fine hero deck).
  - title/section slides render only their title/subtitle, so their
    (ignored) blocks must not fire a deck/overflow error, only the
    existing deck/ignored-blocks warning.
"""

from docloom import Document, Image, Paragraph, Slide, has_errors, lint


def test_hero_slide_with_450_char_body_has_no_overflow_error():
    # ~450 chars is over the half budget (800 // 2 = 400), so hero now emits a
    # non-blocking warning; the point is it must NOT be an error that blocks
    # export the way it used to.
    body = "word " * 90  # 450 chars
    doc = Document(title="T", slides=[Slide(
        layout="hero", title="t", image=Image(query="office"),
        blocks=[Paragraph(text=body)],
    )])
    findings = lint(doc)
    assert not has_errors(findings)
    assert not any(
        f.rule == "deck/overflow" and f.severity == "error" for f in findings
    )


def test_section_slide_over_800_char_blocks_has_no_overflow_error():
    # section renders only title/subtitle; its blocks are ignored, so even
    # 900 chars of (non-rendered) blocks must not block export. The blocks
    # are still surfaced by the correct deck/ignored-blocks warning.
    doc = Document(title="T", slides=[Slide(
        layout="section", title="t",
        blocks=[Paragraph(text="x" * 900)],
    )])
    findings = lint(doc)
    assert not any(
        f.rule == "deck/overflow" and f.severity == "error" for f in findings
    )
    assert any(f.rule == "deck/ignored-blocks" for f in findings)
    assert not has_errors(findings)
