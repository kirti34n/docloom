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
# Diagram/DiagramNode/DiagramEdge/DiagramGroup are not yet re-exported from
# docloom/__init__.py (that file is out of scope for this change; see the
# handoff note), so these come from the ir submodule directly.
from docloom.ir import Diagram, DiagramEdge, DiagramGroup, DiagramNode


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


# ------------------------------------------------------------- diagram/* rules
#
# docs/diagram-plan.md section 6. llm_schema() strips minLength/maxLength/
# pattern (see llm.py), so every diagram length limit below is enforceable
# only as a lint rule, never as a Pydantic field constraint.


def _star_diagram(n_leaves):
    """One hub node fanning out to n_leaves leaves: depth is always 2
    regardless of n_leaves, isolating the node-count threshold of
    diagram/too-dense from the depth threshold."""
    nodes = [DiagramNode(id="hub", label="Hub")] + [
        DiagramNode(id=f"leaf{i}", label=f"Leaf {i}") for i in range(n_leaves)
    ]
    edges = [DiagramEdge(source="hub", target=f"leaf{i}") for i in range(n_leaves)]
    return Diagram(caption="A hub fans out to its leaves.", nodes=nodes, edges=edges)


def _chain_diagram(n_nodes):
    """A straight n_nodes-long path: depth equals n_nodes, isolating the
    depth threshold of diagram/too-dense from the node-count threshold."""
    nodes = [DiagramNode(id=f"n{i}", label=f"Node {i}") for i in range(n_nodes)]
    edges = [
        DiagramEdge(source=f"n{i}", target=f"n{i + 1}") for i in range(n_nodes - 1)
    ]
    return Diagram(caption="A single chain of steps.", nodes=nodes, edges=edges)


def _too_dense(findings):
    return [f for f in findings if f.rule == "diagram/too-dense"]


# --------------------------------------------------------------- diagram/empty


def test_diagram_with_no_nodes_flags_diagram_empty():
    doc = Document(title="T", blocks=[Diagram(caption="c")])
    findings = lint(doc)
    assert "diagram/empty" in _rules(findings)
    assert has_errors(findings)


def test_diagram_with_nodes_does_not_flag_diagram_empty():
    doc = Document(title="T", blocks=[
        Diagram(caption="c", nodes=[DiagramNode(id="a", label="A")]),
    ])
    findings = lint(doc)
    assert "diagram/empty" not in _rules(findings)


# ---------------------------------------------------------- diagram/duplicate-id


def test_duplicate_node_id_flags_diagram_duplicate_id():
    doc = Document(title="T", blocks=[Diagram(caption="c", nodes=[
        DiagramNode(id="a", label="A"),
        DiagramNode(id="a", label="A again"),
    ])])
    findings = lint(doc)
    assert "diagram/duplicate-id" in _rules(findings)
    assert has_errors(findings)


def test_duplicate_group_id_flags_diagram_duplicate_id():
    doc = Document(title="T", blocks=[Diagram(caption="c",
        groups=[DiagramGroup(id="g", label="G1"), DiagramGroup(id="g", label="G2")],
        nodes=[DiagramNode(id="a", label="A", group="g")],
    )])
    findings = lint(doc)
    assert "diagram/duplicate-id" in _rules(findings)
    assert has_errors(findings)


def test_unique_node_and_group_ids_do_not_flag_diagram_duplicate_id():
    doc = Document(title="T", blocks=[Diagram(caption="c",
        groups=[DiagramGroup(id="g", label="G")],
        nodes=[DiagramNode(id="a", label="A", group="g"),
               DiagramNode(id="b", label="B", group="g")],
    )])
    findings = lint(doc)
    assert "diagram/duplicate-id" not in _rules(findings)


# ----------------------------------------------------------- diagram/dangling-edge


def test_edge_to_unknown_node_flags_dangling_edge():
    doc = Document(title="T", blocks=[Diagram(caption="c",
        nodes=[DiagramNode(id="a", label="A")],
        edges=[DiagramEdge(source="a", target="ghost")],
    )])
    findings = lint(doc)
    assert "diagram/dangling-edge" in _rules(findings)
    assert has_errors(findings)


def test_edge_from_unknown_node_flags_dangling_edge():
    doc = Document(title="T", blocks=[Diagram(caption="c",
        nodes=[DiagramNode(id="a", label="A")],
        edges=[DiagramEdge(source="ghost", target="a")],
    )])
    findings = lint(doc)
    assert "diagram/dangling-edge" in _rules(findings)
    assert has_errors(findings)


def test_edge_between_known_nodes_does_not_flag_dangling_edge():
    doc = Document(title="T", blocks=[Diagram(caption="c",
        nodes=[DiagramNode(id="a", label="A"), DiagramNode(id="b", label="B")],
        edges=[DiagramEdge(source="a", target="b")],
    )])
    findings = lint(doc)
    assert "diagram/dangling-edge" not in _rules(findings)


# ----------------------------------------------------------- diagram/unknown-group


def test_node_referencing_unknown_group_flags_unknown_group():
    doc = Document(title="T", blocks=[Diagram(caption="c",
        nodes=[DiagramNode(id="a", label="A", group="ghost")],
    )])
    findings = lint(doc)
    assert "diagram/unknown-group" in _rules(findings)
    assert has_errors(findings)


def test_node_referencing_real_group_does_not_flag_unknown_group():
    doc = Document(title="T", blocks=[Diagram(caption="c",
        groups=[DiagramGroup(id="g", label="G")],
        nodes=[DiagramNode(id="a", label="A", group="g")],
    )])
    findings = lint(doc)
    assert "diagram/unknown-group" not in _rules(findings)


# ------------------------------------------------------------- diagram/empty-group


def test_group_with_no_members_flags_empty_group():
    doc = Document(title="T", blocks=[Diagram(caption="c",
        groups=[DiagramGroup(id="g", label="G")],
        nodes=[DiagramNode(id="a", label="A")],
    )])
    findings = lint(doc)
    assert "diagram/empty-group" in _rules(findings)
    assert not has_errors(findings)


def test_group_with_a_member_does_not_flag_empty_group():
    doc = Document(title="T", blocks=[Diagram(caption="c",
        groups=[DiagramGroup(id="g", label="G")],
        nodes=[DiagramNode(id="a", label="A", group="g")],
    )])
    findings = lint(doc)
    assert "diagram/empty-group" not in _rules(findings)


# ----------------------------------------------------------------- diagram/self-loop


def test_self_loop_edge_flags_self_loop():
    doc = Document(title="T", blocks=[Diagram(caption="c",
        nodes=[DiagramNode(id="a", label="A")],
        edges=[DiagramEdge(source="a", target="a")],
    )])
    findings = lint(doc)
    assert "diagram/self-loop" in _rules(findings)
    assert not has_errors(findings)


def test_non_self_loop_edge_does_not_flag_self_loop():
    doc = Document(title="T", blocks=[Diagram(caption="c",
        nodes=[DiagramNode(id="a", label="A"), DiagramNode(id="b", label="B")],
        edges=[DiagramEdge(source="a", target="b")],
    )])
    findings = lint(doc)
    assert "diagram/self-loop" not in _rules(findings)


# ----------------------------------------------------------- diagram/disconnected-node


def test_node_with_no_edges_flags_disconnected_node():
    doc = Document(title="T", blocks=[Diagram(caption="c",
        nodes=[DiagramNode(id="a", label="A"), DiagramNode(id="b", label="B")],
    )])
    findings = lint(doc)
    assert "diagram/disconnected-node" in _rules(findings)
    assert not has_errors(findings)


def test_node_with_an_edge_does_not_flag_disconnected_node():
    doc = Document(title="T", blocks=[Diagram(caption="c",
        nodes=[DiagramNode(id="a", label="A"), DiagramNode(id="b", label="B")],
        edges=[DiagramEdge(source="a", target="b")],
    )])
    findings = lint(doc)
    assert "diagram/disconnected-node" not in _rules(findings)


# ---------------------------------------------------------- diagram/label-too-long


def test_node_label_over_40_chars_flags_label_too_long():
    doc = Document(title="T", blocks=[Diagram(caption="c",
        nodes=[DiagramNode(id="a", label="x" * 41)],
    )])
    findings = lint(doc)
    assert "diagram/label-too-long" in _rules(findings)
    assert not has_errors(findings)


def test_node_label_at_40_chars_does_not_flag_label_too_long():
    doc = Document(title="T", blocks=[Diagram(caption="c",
        nodes=[DiagramNode(id="a", label="x" * 40)],
    )])
    findings = lint(doc)
    assert "diagram/label-too-long" not in _rules(findings)


def test_node_sublabel_over_40_chars_flags_label_too_long():
    doc = Document(title="T", blocks=[Diagram(caption="c",
        nodes=[DiagramNode(id="a", label="A", sublabel="x" * 41)],
    )])
    findings = lint(doc)
    assert "diagram/label-too-long" in _rules(findings)


def test_node_tag_over_12_chars_flags_label_too_long():
    doc = Document(title="T", blocks=[Diagram(caption="c",
        nodes=[DiagramNode(id="a", label="A", tag="x" * 13)],
    )])
    findings = lint(doc)
    assert "diagram/label-too-long" in _rules(findings)


def test_edge_label_over_30_chars_flags_label_too_long():
    doc = Document(title="T", blocks=[Diagram(caption="c",
        nodes=[DiagramNode(id="a", label="A"), DiagramNode(id="b", label="B")],
        edges=[DiagramEdge(source="a", target="b", label="x" * 31)],
    )])
    findings = lint(doc)
    assert "diagram/label-too-long" in _rules(findings)


def test_group_label_over_40_chars_flags_label_too_long():
    doc = Document(title="T", blocks=[Diagram(caption="c",
        groups=[DiagramGroup(id="g", label="x" * 41)],
        nodes=[DiagramNode(id="a", label="A", group="g")],
    )])
    findings = lint(doc)
    assert "diagram/label-too-long" in _rules(findings)


def test_short_labels_do_not_flag_label_too_long():
    doc = Document(title="T", blocks=[Diagram(caption="c",
        groups=[DiagramGroup(id="g", label="Core")],
        nodes=[DiagramNode(id="a", label="API", sublabel="FastAPI", tag="v2",
                            group="g")],
        edges=[],
    )])
    findings = lint(doc)
    assert "diagram/label-too-long" not in _rules(findings)


# -------------------------------------------------------------- diagram/too-dense
#
# REPIN NOTE (docs/diagram-status.md finding 6, fixed here): diagram/too-dense
# used to escalate to severity="error" past DIAGRAM_MAX_NODES_ERROR=14 /
# DIAGRAM_MAX_DEPTH_ERROR=7, and an "error" finding makes has_errors() true,
# which makes `docloom render` refuse the WHOLE deck (exit 2, no output at
# all, not even --diagram-sources sidecars) over one crowded diagram. Five
# independent, ordinary 13-14 node reference-architecture bake-off specs
# measured real depths of 5, 6, 8, 8, 10 through this exact rule -- three of
# five would have hard-blocked their deck under the old depth>7 error
# threshold, for a diagram no denser than a normal AWS reference
# architecture. The four tests below that used to assert severity=="error"
# and has_errors(findings) are true are repinned to assert severity==
# "warning" and NOT has_errors(findings): diagram density is now always a
# non-blocking warning (see the DIAGRAM_MAX_*_DENSE comment in lint.py for
# the full reasoning), and a fifth test is added to lock in the "never
# blocks a render, no matter how dense" contract directly.


def test_many_nodes_shallow_depth_flags_too_dense_soft_warning():
    doc = Document(title="T", blocks=[_star_diagram(8)])  # 9 nodes, depth 2
    findings = lint(doc)
    dense = _too_dense(findings)
    assert dense and dense[0].severity == "warning"
    assert not has_errors(findings)


def test_many_nodes_flags_too_dense_at_the_stronger_tier_but_still_warning():
    doc = Document(title="T", blocks=[_star_diagram(14)])  # 15 nodes, depth 2
    findings = lint(doc)
    dense = _too_dense(findings)
    assert dense and dense[0].severity == "warning"
    assert "very dense" in dense[0].message
    assert not has_errors(findings)


def test_deep_chain_flags_too_dense_soft_warning():
    doc = Document(title="T", blocks=[_chain_diagram(6)])  # depth 6
    findings = lint(doc)
    dense = _too_dense(findings)
    assert dense and dense[0].severity == "warning"
    assert not has_errors(findings)


def test_deep_chain_at_the_old_error_threshold_is_now_only_a_soft_warning():
    # depth 8 used to be > the old DIAGRAM_MAX_DEPTH_ERROR of 7 and errored;
    # the bake-off evidence above shows depth 8 is ordinary, so it now sits
    # below the raised DIAGRAM_MAX_DEPTH_DENSE (12) and gets only the soft
    # (first-tier) warning message, same as test_deep_chain_flags_too_dense_
    # soft_warning above.
    doc = Document(title="T", blocks=[_chain_diagram(8)])  # depth 8
    findings = lint(doc)
    dense = _too_dense(findings)
    assert dense and dense[0].severity == "warning"
    assert "very dense" not in dense[0].message
    assert not has_errors(findings)


def test_very_deep_chain_flags_too_dense_at_the_stronger_tier_but_still_warning():
    doc = Document(title="T", blocks=[_chain_diagram(13)])  # depth 13
    findings = lint(doc)
    dense = _too_dense(findings)
    assert dense and dense[0].severity == "warning"
    assert "very dense" in dense[0].message
    assert not has_errors(findings)


def test_small_shallow_diagram_does_not_flag_too_dense():
    doc = Document(title="T", blocks=[_star_diagram(4)])  # 5 nodes, depth 2
    findings = lint(doc)
    assert "diagram/too-dense" not in _rules(findings)
    assert not has_errors(findings)


def test_diagram_too_dense_never_blocks_a_render_no_matter_how_dense():
    # docs/diagram-status.md finding 6: severity="error" here used to make
    # `docloom render` refuse the entire deck over one crowded diagram. This
    # locks in that no diagram, however dense, can push has_errors() true
    # through diagram/too-dense alone -- an extreme 40-node/depth-40 chain
    # still gets only a (strongly worded) warning.
    doc = Document(title="T", blocks=[_chain_diagram(40)])
    findings = lint(doc)
    dense = _too_dense(findings)
    assert dense and dense[0].severity == "warning"
    assert not has_errors(findings)


# ----------------------------------------------------------- diagram/crowded-slide


def _diagram_slide(*extra_blocks):
    d = Diagram(caption="c",
                nodes=[DiagramNode(id="a", label="A"), DiagramNode(id="b", label="B")],
                edges=[DiagramEdge(source="a", target="b")])
    return Slide(layout="content", title="System overview shows the architecture",
                 blocks=[d, *extra_blocks])


def test_diagram_with_two_other_blocks_flags_crowded_slide():
    doc = Document(title="T", slides=[_diagram_slide(
        Paragraph(text="Some context."),
        BulletList(items=[ListItem(text="a supporting point")]),
    )])
    findings = lint(doc)
    assert "diagram/crowded-slide" in _rules(findings)
    assert not has_errors(findings)


def test_diagram_alone_on_a_slide_does_not_flag_crowded_slide():
    doc = Document(title="T", slides=[_diagram_slide()])
    findings = lint(doc)
    assert "diagram/crowded-slide" not in _rules(findings)


def test_diagram_with_one_other_block_does_not_flag_crowded_slide():
    doc = Document(title="T", slides=[_diagram_slide(Paragraph(text="Some context."))])
    findings = lint(doc)
    assert "diagram/crowded-slide" not in _rules(findings)


# ------------------------------------------------------- comprehensive good diagram


def test_well_formed_diagram_triggers_none_of_the_diagram_rules():
    d = Diagram(
        title="Service topology", caption="Two services talk over one edge.",
        groups=[DiagramGroup(id="g", label="Core services")],
        nodes=[
            DiagramNode(id="a", label="API", type="service", group="g"),
            DiagramNode(id="b", label="Database", type="store", group="g"),
        ],
        edges=[DiagramEdge(source="a", target="b", label="writes")],
    )
    doc = Document(title="T", blocks=[d])
    findings = lint(doc)
    assert not {f.rule for f in findings if f.rule.startswith("diagram/")}
    assert "visual/unlabeled" not in _rules(findings)
    assert not has_errors(findings)
