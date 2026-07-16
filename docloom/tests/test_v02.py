"""Contract tests for the 0.2.0 IR extensions (studio groundwork)."""

import json

from docloom import (
    Artifact, Chart, Diagram, Document, Image, Paragraph, Series, Slide,
    Stat, StatRow, ensure_ids, lint, llm_schema, parse_llm_output,
)


def test_ensure_ids_fills_missing_and_keeps_existing():
    doc = Document(
        title="T",
        blocks=[Paragraph(text="a"), Paragraph(id="keep-me", text="b")],
        slides=[Slide(title="s", blocks=[Paragraph(text="c")])],
    )
    ensure_ids(doc)
    assert doc.blocks[0].id and doc.blocks[1].id == "keep-me"
    assert doc.slides[0].id and doc.slides[0].blocks[0].id
    ids = [doc.blocks[0].id, doc.blocks[1].id, doc.slides[0].id,
           doc.slides[0].blocks[0].id]
    assert len(set(ids)) == len(ids)


def test_old_documents_still_load():
    # 0.1.x shape: Image with required path, five layouts, no ids
    doc = Document.model_validate_json(json.dumps({
        "title": "Old",
        "blocks": [{"type": "image", "path": "x.png", "alt": "a"}],
        "slides": [{"layout": "two_column", "title": "t",
                    "blocks": [{"type": "paragraph", "text": "p"}]}],
    }))
    assert doc.blocks[0].path == "x.png"
    assert doc.slides[0].layout == "two_column"


def test_llm_schema_strips_bookkeeping_but_keeps_source_id():
    # Source.id, DiagramNode.id, and DiagramGroup.id are all legitimately
    # required fields (an LLM must name a node/group to reference it from an
    # edge or a node's `group`), so llm_schema()'s close() correctly leaves
    # "id" in place for all three: it only strips "id" when the field is
    # optional bookkeeping (node in props but not in required). This used
    # to assume "only Source may have a required id"; that assumption went
    # stale the moment DiagramNode/DiagramGroup landed with required ids.
    REQUIRES_ID = {"Source", "DiagramNode", "DiagramGroup"}
    schema = llm_schema()
    text = json.dumps(schema)
    assert '"oneOf"' not in text
    defs = schema["$defs"]
    for name, node in defs.items():
        props = node.get("properties", {})
        required = node.get("required", [])
        if name in REQUIRES_ID:
            assert "id" in props and "id" in required
        else:
            assert "id" not in props, name
        assert "asset_id" not in props, name
        assert "artifact_id" not in props, name
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False, name


def test_llm_schema_accepts_other_models():
    slide_schema = llm_schema(Slide)
    assert "layout" in slide_schema["properties"]
    assert "hero" in json.dumps(slide_schema)


def test_new_block_aliases_parse():
    doc = parse_llm_output(json.dumps({
        "title": "T",
        "blocks": [
            {"type": "kpi", "items": [{"label": "ARR", "value": "$1M"}]},
            {"type": "graph", "chart": "line", "labels": ["a"],
             "series": [{"name": "s", "values": [1.0]}]},
        ],
    }))
    assert type(doc.blocks[0]).__name__ == "StatRow"
    assert type(doc.blocks[1]).__name__ == "Chart"


def test_diagram_block_roundtrips_through_parse_llm_output():
    # Regression for the "diagram" -> "artifact" alias that used to live in
    # _TYPE_ALIASES: it rewrote the tag before validation ever saw it, so
    # parse_llm_output silently returned Artifact(kind='diagram', path=None)
    # and every node/edge/group the model wrote was discarded, with no error
    # and no warning. This must come back as a real Diagram with its
    # structure intact.
    doc = parse_llm_output(json.dumps({
        "title": "T",
        "blocks": [{
            "type": "diagram",
            "title": "Architecture",
            "direction": "LR",
            "nodes": [
                {"id": "a", "label": "Client", "type": "client"},
                {"id": "b", "label": "API", "type": "service"},
            ],
            "edges": [{"source": "a", "target": "b", "label": "request"}],
            "groups": [],
            "caption": "System overview",
            "alt": "A diagram showing client to API",
        }],
    }))
    block = doc.blocks[0]
    assert isinstance(block, Diagram)
    assert [n.id for n in block.nodes] == ["a", "b"]
    assert len(block.edges) == 1
    assert block.edges[0].source == "a" and block.edges[0].target == "b"


def test_lint_new_rules():
    doc = Document(
        title="T",
        blocks=[
            Chart(labels=["a", "b"], series=[Series(name="s", values=[1.0])]),
            Artifact(kind="diagram"),
            Image(alt="empty slot"),
        ],
        slides=[Slide(layout="image_right", title="t",
                      blocks=[Paragraph(text="x")])],
    )
    rules = {f.rule for f in lint(doc)}
    assert "chart/ragged-series" in rules
    assert "artifact/unbound" in rules
    assert "image/unresolved" in rules
    assert "deck/missing-slot-image" in rules


def test_lint_image_layout_half_budget():
    from docloom import BulletList, ListItem

    items = [ListItem(text="b" * 110) for _ in range(5)]  # 550 > 400
    doc = Document(
        title="T",
        slides=[Slide(layout="image_left", title="t",
                      image=Image(query="office"),
                      blocks=[BulletList(items=items)])],
    )
    assert "deck/overflow" in {f.rule for f in lint(doc)}


def test_stats_and_chart_roundtrip(tmp_path):
    doc = Document(
        title="T",
        blocks=[
            StatRow(items=[Stat(label="NRR", value="124%", delta="+6 pts")]),
            Chart(chart="pie", title="Mix", labels=["a", "b"],
                  series=[Series(name="s", values=[60.0, 40.0])]),
        ],
    )
    path = tmp_path / "d.json"
    doc.save(path)
    loaded = Document.load(path)
    assert type(loaded.blocks[0]).__name__ == "StatRow"
    assert loaded.blocks[1].series[0].values == [60.0, 40.0]
