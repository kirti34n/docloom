"""Re-audit regression tests for parse_llm_output candidate selection."""

import pytest as _pytest

from docloom import parse_llm_output


def test_parse_llm_output_prefers_last_validating_candidate():
    # live pattern: a local model emits an illustrative example fence first,
    # then the real document fence. The example is a titled mini-document, so
    # it validates too (Document only requires "title"); selecting the first
    # validating candidate silently returned the example and dropped the real
    # document. The real doc comes last, so the last validating one must win.
    example = '```json\n{"title": "Example Title", "blocks": []}\n```'
    real = (
        '```json\n{"title": "REAL Report", "blocks": ['
        '{"type": "paragraph", "text": "real content"}]}\n```'
    )
    text = f"Here is an example:\n\n{example}\n\nNow the real one:\n\n{real}\n"
    doc = parse_llm_output(text)
    assert doc.title == "REAL Report"
    assert doc.blocks  # the real document's content is not discarded
    assert type(doc.blocks[0]).__name__ == "Paragraph"


def test_parse_llm_output_prefers_real_when_example_is_last():
    # the mirror case: real document FIRST, a trailing template/example
    # skeleton LAST. Both validate (Document needs only "title"), so a naive
    # "last validating candidate wins" would return the empty skeleton. The
    # richest candidate (the one with actual content) must win regardless of
    # order.
    real = (
        '```json\n{"title": "REAL Report", "blocks": ['
        '{"type": "paragraph", "text": "real content"}]}\n```'
    )
    example = '```json\n{"title": "Example Title", "blocks": []}\n```'
    text = f"Here is the document:\n\n{real}\n\nFor reference, the schema:\n\n{example}\n"
    doc = parse_llm_output(text)
    assert doc.title == "REAL Report"
    assert doc.blocks
    assert type(doc.blocks[0]).__name__ == "Paragraph"


def test_parse_llm_output_single_fence_still_parses():
    bare = '{"title": "T", "blocks": [{"type": "paragraph", "text": "x"}]}'
    doc = parse_llm_output(f"```json\n{bare}\n```")
    assert doc.title == "T"
    assert type(doc.blocks[0]).__name__ == "Paragraph"


def test_parse_llm_output_unparseable_still_raises():
    with _pytest.raises(Exception):
        parse_llm_output("this is not json at all, no braces here")
