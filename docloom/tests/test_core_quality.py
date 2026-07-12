"""Tests for the height-budget overflow rule, the new/changed lint rules
(stats cap, empty chart, scatter labels, pie message, quote coverage), and
the parser tolerance fixes (case/padding-tolerant type tags, non-greedy
multi-fence extraction)."""

import json

from docloom import (
    BulletList, Chart, Document, Image, ListItem, Paragraph,
    Quote, Series, Slide, Stat, StatRow, lint, parse_llm_output,
)

# --------------------------------------------------------- height overflow


def test_lint_height_overflow_catches_stacked_charts():
    # two charts score ~20 chars combined (well under the 800 char budget)
    # but are 4.5in each: 9in > the 5.48in body, which the old char-only
    # rule could not see
    doc = Document(title="T", slides=[Slide(
        layout="content", title="t",
        blocks=[
            Chart(title="A", labels=["x"], series=[Series(name="s", values=[1.0])]),
            Chart(title="B", labels=["x"], series=[Series(name="s", values=[1.0])]),
        ],
    )])
    findings = [f for f in lint(doc) if f.rule == "deck/overflow"]
    assert findings
    assert any("estimated content height" in f.message for f in findings)
    assert any(f.where == "slides[0]" for f in findings)


def test_lint_height_overflow_stays_quiet_on_reasonable_slide():
    # one chart plus a couple of short bullets comfortably fits the 5.48in
    # body (~5.16in): must not false-positive
    doc = Document(title="T", slides=[Slide(
        layout="content", title="t",
        blocks=[
            Chart(title="A", labels=["x", "y"],
                  series=[Series(name="s", values=[1.0, 2.0])]),
            BulletList(items=[ListItem(text="short point one"),
                               ListItem(text="short point two")]),
        ],
    )])
    assert "deck/overflow" not in {f.rule for f in lint(doc)}


def test_lint_height_overflow_two_column_per_column_budget():
    # two_column halves the width (not the vertical budget), so two stacked
    # charts in the right column alone must overflow that column
    doc = Document(title="T", slides=[Slide(
        layout="two_column", title="t",
        blocks=[Paragraph(text="left column stays short")],
        right=[
            Chart(labels=["x"], series=[Series(name="s", values=[1.0])]),
            Chart(labels=["x"], series=[Series(name="s", values=[1.0])]),
        ],
    )])
    findings = [f for f in lint(doc) if f.rule == "deck/overflow"]
    assert any(f.where == "slides[0].right" for f in findings)
    assert not any(f.where == "slides[0].blocks" for f in findings)


def test_lint_height_overflow_hero_family_narrow_width_still_fixed_height():
    # a chart's height doesn't shrink just because its column is narrower
    # beside an image pane
    doc = Document(title="T", slides=[Slide(
        layout="image_left", title="t", image=Image(query="office"),
        blocks=[
            Chart(labels=["x"], series=[Series(name="s", values=[1.0])]),
            Chart(labels=["x"], series=[Series(name="s", values=[1.0])]),
        ],
    )])
    assert "deck/overflow" in {f.rule for f in lint(doc)}


# ------------------------------------------------------------- stats cap


def test_lint_stats_row_over_cap_warns():
    doc = Document(title="T", blocks=[
        StatRow(items=[Stat(label=f"L{i}", value=str(i)) for i in range(6)]),
    ])
    assert "stats/too-many" in {f.rule for f in lint(doc)}


def test_lint_stats_row_at_cap_is_clean():
    doc = Document(title="T", blocks=[
        StatRow(items=[Stat(label=f"L{i}", value=str(i)) for i in range(5)]),
    ])
    assert "stats/too-many" not in {f.rule for f in lint(doc)}


# ------------------------------------------------------------- empty chart


def test_lint_chart_with_no_data_is_an_error():
    doc = Document(title="T", blocks=[Chart(title="Revenue")])
    findings = [f for f in lint(doc) if f.rule == "chart/empty"]
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert findings[0].message == "chart has no data; fill labels and series"


def test_lint_chart_with_data_has_no_empty_finding():
    doc = Document(title="T", blocks=[
        Chart(labels=["a", "b"], series=[Series(name="s", values=[1.0, 2.0])]),
    ])
    assert "chart/empty" not in {f.rule for f in lint(doc)}


# --------------------------------------------------------- scatter labels


def test_lint_scatter_chart_non_numeric_labels_is_a_warning():
    doc = Document(title="T", blocks=[
        Chart(chart="scatter", labels=["Q1", "Q2"],
              series=[Series(name="s", values=[1.0, 2.0])]),
    ])
    findings = [f for f in lint(doc) if f.rule == "chart/scatter-non-numeric"]
    assert len(findings) == 1
    assert findings[0].severity == "warning"


def test_lint_scatter_chart_numeric_labels_is_clean():
    doc = Document(title="T", blocks=[
        Chart(chart="scatter", labels=["1", "2.5"],
              series=[Series(name="s", values=[1.0, 2.0])]),
    ])
    assert "chart/scatter-non-numeric" not in {f.rule for f in lint(doc)}


# ------------------------------------------------------ pie message fixed


def test_lint_pie_multi_series_message_matches_current_renderer_behavior():
    doc = Document(title="T", blocks=[
        Chart(chart="pie", labels=["a", "b"], series=[
            Series(name="s1", values=[1.0, 2.0]),
            Series(name="s2", values=[3.0, 4.0]),
        ]),
    ])
    findings = [f for f in lint(doc) if f.rule == "chart/pie-multi-series"]
    assert len(findings) == 1
    message = findings[0].message
    # current behavior: PPTX falls back to a plain data table (it no longer
    # renders a native chart that silently keeps only the first series)
    assert "data table" in message
    assert "keeps only the first series" not in message
    assert "diverge across formats" not in message


# --------------------------------------------------------- quote coverage


def test_lint_empty_quote_slide_is_flagged():
    doc = Document(title="T", slides=[Slide(layout="quote", title="t")])
    findings = [f for f in lint(doc) if f.rule == "deck/empty-slide"]
    assert len(findings) == 1
    assert "quote" in findings[0].message


def test_lint_quote_slide_without_quote_block_warns():
    doc = Document(title="T", slides=[
        Slide(layout="quote", blocks=[Paragraph(text="not a quote block")]),
    ])
    assert "deck/missing-quote-block" in {f.rule for f in lint(doc)}


def test_lint_quote_slide_with_quote_block_is_clean():
    doc = Document(title="T", slides=[
        Slide(layout="quote", blocks=[Quote(text="hello", attribution="Someone")]),
    ])
    rules = {f.rule for f in lint(doc)}
    assert "deck/missing-quote-block" not in rules
    assert "deck/empty-slide" not in rules


# ------------------------------------------------ parser: type tolerance


def test_parse_llm_output_tolerates_case_and_padding_in_canonical_tags():
    # a canonical tag that only differs by case/whitespace used to be
    # rejected: it isn't in _VALID_TYPES verbatim, and isn't a key in
    # _TYPE_ALIASES either (aliases only map *non-canonical* spellings)
    text = json.dumps({
        "title": "T",
        "blocks": [
            {"type": "Bullets", "items": [{"text": "a"}]},
            {"type": " Table ", "header": [], "rows": []},
            {"type": "PARAGRAPH", "text": "x"},
        ],
    })
    doc = parse_llm_output(text)
    assert [type(b).__name__ for b in doc.blocks] == [
        "BulletList", "Table", "Paragraph",
    ]


def test_parse_llm_output_still_normalizes_real_aliases():
    text = json.dumps({
        "title": "T",
        "blocks": [{"type": "BulletList", "items": [{"text": "a"}]}],
    })
    doc = parse_llm_output(text)
    assert type(doc.blocks[0]).__name__ == "BulletList"


# ------------------------------------------------ parser: fence handling


def test_parse_llm_output_single_fence_with_nested_json_not_truncated():
    # guards against a naive greedy->non-greedy swap under-matching at the
    # first inner "}" instead of the outer document's closing brace
    bare = json.dumps({
        "title": "T",
        "blocks": [
            {"type": "paragraph", "text": "a"},
            {"type": "table", "header": ["h"], "rows": [["1"], ["2"]]},
        ],
    })
    doc = parse_llm_output(f"```json\n{bare}\n```")
    assert doc.title == "T"
    assert len(doc.blocks) == 2
    assert type(doc.blocks[1]).__name__ == "Table"
    assert doc.blocks[1].rows == [["1"], ["2"]]


def test_parse_llm_output_multi_fence_picks_first_validating_candidate():
    # a common local-model pattern: an example fence, then the real document
    # fence. The old greedy regex spanned both fences into one invalid blob
    # (json.loads raised "Extra data"); the fix isolates each fence and
    # returns the first one that actually validates as a Document.
    text = (
        "Sure, here's the shape:\n"
        "```json\n"
        '{"type": "paragraph", "text": "example snippet"}\n'
        "```\n\n"
        "Here is the actual document:\n"
        "```json\n"
        '{"title": "Real", "blocks": [{"type": "paragraph", "text": "x"}]}\n'
        "```\n"
    )
    doc = parse_llm_output(text)
    assert doc.title == "Real"
    assert type(doc.blocks[0]).__name__ == "Paragraph"


def test_parse_llm_output_old_greedy_regex_would_have_mangled_this():
    # direct regression check: the exact text above used to fail outright
    import re

    text = (
        "```json\n"
        '{"type": "paragraph", "text": "example snippet"}\n'
        "```\n\nreal one:\n```json\n"
        '{"title": "Real", "blocks": []}\n'
        "```\n"
    )
    old_pattern = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)
    old_span = old_pattern.search(text.strip()).group(1)
    import pytest as _pytest
    with _pytest.raises(json.JSONDecodeError):
        json.loads(old_span)
    # the fixed parser handles the same text fine
    assert parse_llm_output(text).title == "Real"
