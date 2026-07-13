"""Stage A authoring-quality lint rules (research-notebooklm-quality.md
section 6): title-is-takeaway (deck/weak-title), deck block-variety
(deck/monotone), anti-placeholder (content/placeholder), chart/visual
labeling (chart/unlabeled, visual/unlabeled), and report exec-summary-first
(doc/no-summary).

Every rule here is severity="warning": it must surface for authors to fix,
but never make has_errors() true or block export on its own.
"""

from docloom import (
    Artifact, BulletList, Chart, Document, Heading, Image, ListItem,
    Paragraph, Series, Slide, Stat, StatRow, Table, has_errors, lint,
)


def _rules(findings):
    return {f.rule for f in findings}


def _content_slide(title, block):
    return Slide(layout="content", title=title, blocks=[block])


# ------------------------------------------------------------ deck/weak-title


def test_banned_topic_label_title_flags_weak_title_on_a_content_slide():
    doc = Document(title="T", slides=[Slide(
        layout="content", title="Overview",
        blocks=[Paragraph(text="Some supporting detail for the slide.")],
    )])
    findings = lint(doc)
    assert "deck/weak-title" in _rules(findings)
    assert not has_errors(findings)


def test_verbless_noun_phrase_title_flags_weak_title():
    doc = Document(title="T", slides=[Slide(
        layout="content", title="Q3 Metrics",
        blocks=[Paragraph(text="Some supporting detail for the slide.")],
    )])
    findings = lint(doc)
    assert "deck/weak-title" in _rules(findings)
    assert not has_errors(findings)


def test_takeaway_sentence_title_does_not_flag_weak_title():
    doc = Document(title="T", slides=[Slide(
        layout="content",
        title="Revenue grew 14 percent as APAC demand accelerated",
        blocks=[Paragraph(text="Some supporting detail for the slide.")],
    )])
    findings = lint(doc)
    assert "deck/weak-title" not in _rules(findings)
    assert not has_errors(findings)


def test_title_and_section_layout_slides_are_exempt_from_weak_title():
    # cover/divider slides legitimately carry a short label (the deck name,
    # a part title): they are not held to the action-title standard.
    doc = Document(title="T", slides=[
        Slide(layout="title", title="Q3 2026 Business Review"),
        Slide(layout="section", title="Financials"),
    ])
    findings = lint(doc)
    assert "deck/weak-title" not in _rules(findings)
    assert not has_errors(findings)


def test_banned_label_report_heading_flags_weak_title():
    doc = Document(title="T", blocks=[
        Heading(level=1, text="Executive Summary"),
        Paragraph(text="Revenue grew 14 percent in Q3."),
        Heading(level=1, text="Results"),
        Paragraph(text="Detail on the quarter follows."),
        # a conventional report topic heading (not a generic banned label) must
        # NOT be flagged; only exact generic labels like "Results" are
        Heading(level=2, text="Risks"),
        Paragraph(text="Key risks to the forecast."),
    ])
    findings = lint(doc)
    # report headings use doc/weak-heading (not the slide deck/weak-title), and
    # only the exact generic label "Results" fires it, not "Risks"
    weak = [f for f in findings if f.rule == "doc/weak-heading"]
    assert any("Results" in f.message for f in weak)
    assert not any("Risks" in f.message for f in weak)
    assert "deck/weak-title" not in _rules(findings)
    assert not has_errors(findings)


def test_takeaway_report_heading_does_not_flag_weak_title():
    doc = Document(title="T", blocks=[
        Heading(level=1, text="Executive Summary"),
        Paragraph(text="Revenue grew 14 percent in Q3."),
        Heading(level=1, text="Revenue growth accelerated across every region"),
        Paragraph(text="Detail on the quarter follows."),
    ])
    findings = lint(doc)
    assert "deck/weak-title" not in _rules(findings)
    assert "doc/weak-heading" not in _rules(findings)
    assert not has_errors(findings)


# -------------------------------------------------------------- deck/monotone


def test_low_block_variety_across_six_slides_flags_monotone():
    slides = [
        _content_slide(
            f"Point number {i} lands on the reader plainly",
            BulletList(items=[ListItem(text="a supporting fact")])
            if i % 2 == 0 else Paragraph(text="a supporting paragraph"),
        )
        for i in range(6)
    ]
    doc = Document(title="T", slides=slides)
    findings = lint(doc)
    assert "deck/monotone" in _rules(findings)
    assert not has_errors(findings)


def test_three_consecutive_bullet_only_slides_flags_monotone():
    # only 4 slides (below the >=6 variety-count gate) isolates this to the
    # consecutive-run condition alone
    doc = Document(title="T", slides=[
        _content_slide(
            "Segment revenue is broken out below by region",
            Table(header=["Region", "Revenue"], rows=[["APAC", "10"]],
                  caption="APAC led every region."),
        ),
        _content_slide("First supporting point stands on its own",
                        BulletList(items=[ListItem(text="point one")])),
        _content_slide("Second supporting point stands on its own",
                        BulletList(items=[ListItem(text="point two")])),
        _content_slide("Third supporting point stands on its own",
                        BulletList(items=[ListItem(text="point three")])),
    ])
    findings = lint(doc)
    assert "deck/monotone" in _rules(findings)
    assert not has_errors(findings)


def test_varied_blocks_and_no_bullet_run_does_not_flag_monotone():
    doc = Document(title="T", slides=[
        Slide(layout="title", title="Q3 2026 Business Review"),
        _content_slide(
            "Revenue grew 14 percent as APAC demand accelerated",
            BulletList(items=[ListItem(text="APAC bookings nearly doubled")]),
        ),
        _content_slide(
            "Enterprise deal count rose while SMB softened slightly",
            Table(header=["Segment", "Q2", "Q3"],
                  rows=[["Enterprise", "12", "19"]],
                  caption="Enterprise outgrew SMB in the quarter."),
        ),
        _content_slide(
            "Gross margin climbed steadily every month this quarter",
            Chart(chart="line", title="Gross margin trend",
                  labels=["Jan", "Feb", "Mar"],
                  series=[Series(name="Margin", values=[62.0, 63.5, 64.0])],
                  caption="Margin climbed each month."),
        ),
        _content_slide(
            "Headcount growth slowed to make room for margin gains",
            StatRow(items=[Stat(label="Headcount", value="482", delta="+2%")]),
        ),
        Slide(layout="section", title="Looking ahead"),
    ])
    findings = lint(doc)
    assert "deck/monotone" not in _rules(findings)
    assert not has_errors(findings)


# ---------------------------------------------------------- content/placeholder


def test_lorem_ipsum_flags_placeholder():
    doc = Document(title="T", blocks=[
        Paragraph(text="Lorem ipsum dolor sit amet."),
    ])
    findings = lint(doc)
    assert "content/placeholder" in _rules(findings)
    assert not has_errors(findings)


def test_todo_marker_flags_placeholder():
    doc = Document(title="T", blocks=[
        Paragraph(text="TODO: add the real market analysis here"),
    ])
    findings = lint(doc)
    assert "content/placeholder" in _rules(findings)
    assert not has_errors(findings)


def test_empty_brackets_flag_placeholder():
    doc = Document(title="T", blocks=[
        Paragraph(text="Revenue was [ ] this quarter."),
    ])
    findings = lint(doc)
    assert "content/placeholder" in _rules(findings)
    assert not has_errors(findings)


def test_insert_here_flags_placeholder():
    doc = Document(title="T", blocks=[
        Paragraph(text="insert the client name here before sending"),
    ])
    findings = lint(doc)
    assert "content/placeholder" in _rules(findings)
    assert not has_errors(findings)


def test_placeholder_slide_title_is_flagged_too():
    doc = Document(title="T", slides=[Slide(
        layout="content", title="TBD",
        blocks=[Paragraph(text="Some supporting detail for the slide.")],
    )])
    findings = lint(doc)
    assert "content/placeholder" in _rules(findings)
    assert not has_errors(findings)


def test_real_content_does_not_flag_placeholder():
    doc = Document(title="T", blocks=[
        Paragraph(text="Revenue grew 14 percent in Q3, driven by APAC demand."),
    ])
    findings = lint(doc)
    assert "content/placeholder" not in _rules(findings)
    assert not has_errors(findings)


# ---------------------------------------------- chart/unlabeled, visual/unlabeled


def test_chart_missing_title_and_caption_flags_chart_unlabeled():
    doc = Document(title="T", blocks=[Chart(
        chart="bar", labels=["Q1", "Q2"],
        series=[Series(name="Revenue", values=[1, 2])],
    )])
    findings = lint(doc)
    assert "chart/unlabeled" in _rules(findings)
    assert not has_errors(findings)


def test_labeled_chart_does_not_flag_chart_unlabeled():
    doc = Document(title="T", blocks=[Chart(
        chart="bar", title="Quarterly revenue by segment",
        labels=["Q1", "Q2"], series=[Series(name="Revenue", values=[1, 2])],
        caption="Revenue grew steadily across both quarters.",
    )])
    findings = lint(doc)
    assert "chart/unlabeled" not in _rules(findings)
    assert not has_errors(findings)


def test_image_without_caption_flags_visual_unlabeled():
    doc = Document(title="T", blocks=[Image(query="team meeting")])
    findings = lint(doc)
    assert "visual/unlabeled" in _rules(findings)
    assert not has_errors(findings)


def test_image_with_caption_does_not_flag_visual_unlabeled():
    doc = Document(title="T", blocks=[Image(
        query="team meeting",
        caption="The leadership team reviews Q3 results.",
    )])
    findings = lint(doc)
    assert "visual/unlabeled" not in _rules(findings)
    assert not has_errors(findings)


def test_table_without_caption_flags_visual_unlabeled():
    doc = Document(title="T", blocks=[
        Table(header=["A", "B"], rows=[["1", "2"]]),
    ])
    findings = lint(doc)
    assert "visual/unlabeled" in _rules(findings)
    assert not has_errors(findings)


def test_table_with_caption_does_not_flag_visual_unlabeled():
    doc = Document(title="T", blocks=[
        Table(header=["A", "B"], rows=[["1", "2"]], caption="A versus B."),
    ])
    findings = lint(doc)
    assert "visual/unlabeled" not in _rules(findings)
    assert not has_errors(findings)


def test_artifact_without_caption_flags_visual_unlabeled():
    doc = Document(title="T", blocks=[
        Artifact(kind="diagram", artifact_id="abc123", path="diagram.svg"),
    ])
    findings = lint(doc)
    assert "visual/unlabeled" in _rules(findings)
    assert not has_errors(findings)


# --------------------------------------------------------------- doc/no-summary


def test_report_not_opening_with_exec_summary_flags_no_summary():
    doc = Document(title="T", blocks=[
        Heading(level=1, text="Introduction"),
        Paragraph(text="This report covers the quarter."),
        Heading(level=1, text="Market Analysis"),
        Paragraph(text="The market grew steadily."),
    ])
    findings = lint(doc)
    assert "doc/no-summary" in _rules(findings)
    assert not has_errors(findings)


def test_report_opening_with_exec_summary_does_not_flag_no_summary():
    doc = Document(title="T", blocks=[
        Heading(level=1, text="Executive Summary"),
        Paragraph(text="Revenue grew 14 percent in Q3."),
        Heading(level=1, text="Revenue growth accelerated across every region"),
        Paragraph(text="Detail on the quarter follows."),
    ])
    findings = lint(doc)
    assert "doc/no-summary" not in _rules(findings)
    assert not has_errors(findings)


def test_single_heading_report_does_not_flag_no_summary():
    # fewer than 2 sections: too little structure to judge "missing a summary"
    doc = Document(title="T", blocks=[
        Heading(level=1, text="Random Topic"),
        Paragraph(text="Just one section so far."),
    ])
    findings = lint(doc)
    assert "doc/no-summary" not in _rules(findings)
    assert not has_errors(findings)


# ------------------------------------------------------- comprehensive good doc


def test_well_formed_deck_and_report_trigger_none_of_the_new_warnings():
    doc = Document(
        title="Q3 2026 Business Review",
        blocks=[
            Heading(level=1, text="Executive Summary"),
            Paragraph(text="Revenue grew 14 percent in Q3, led by APAC "
                            "expansion and stronger renewal rates."),
            Heading(level=1, text="Revenue growth accelerated across every region"),
            Paragraph(text="Q3 revenue increased broadly, with APAC leading "
                            "and EMEA close behind."),
            Chart(chart="bar", title="Quarterly revenue by region",
                  labels=["Q1", "Q2", "Q3"],
                  series=[Series(name="APAC", values=[10, 12, 15]),
                          Series(name="EMEA", values=[8, 9, 10])],
                  caption="APAC overtook EMEA as the fastest-growing region "
                          "in Q3."),
        ],
        slides=[
            Slide(layout="title", title="Q3 2026 Business Review"),
            _content_slide(
                "Revenue grew 14 percent as APAC demand accelerated",
                BulletList(items=[
                    ListItem(text="APAC bookings nearly doubled"),
                    ListItem(text="EMEA held steady despite FX headwinds"),
                ]),
            ),
            _content_slide(
                "Enterprise deal count rose while SMB softened slightly",
                Table(header=["Segment", "Q2", "Q3"],
                      rows=[["Enterprise", "12", "19"], ["SMB", "30", "28"]],
                      caption="Enterprise deals grew while SMB slipped."),
            ),
            _content_slide(
                "Gross margin climbed steadily every month this quarter",
                Chart(chart="line", title="Gross margin trend",
                      labels=["Jan", "Feb", "Mar"],
                      series=[Series(name="Margin", values=[62.0, 63.5, 64.0])],
                      caption="Margin climbed each month."),
            ),
            _content_slide(
                "Headcount growth slowed to make room for margin gains",
                StatRow(items=[
                    Stat(label="Headcount", value="482", delta="+2% QoQ"),
                    Stat(label="Attrition", value="4.1%", delta="-0.6pt"),
                ]),
            ),
            Slide(layout="section", title="Looking ahead"),
        ],
    )
    findings = lint(doc)
    new_rules = {
        "deck/weak-title", "deck/monotone", "content/placeholder",
        "chart/unlabeled", "visual/unlabeled", "doc/no-summary",
    }
    assert not (new_rules & _rules(findings))
    assert not has_errors(findings)
