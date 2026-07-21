"""Inline diagram blocks in decks and reports. The renderers already support a
Diagram block in a slide (native PPTX shapes) or a report section (rasterized
SVG in DOCX/PDF/HTML); the gap was that neither authoring prompt told the model
the option existed, so generated docs came out all text with no architecture.

With the prompts now offering a "diagram" block, an inline diagram must be
VALIDATED the way the standalone diagram pipeline validates one -- by actually
laying it out (solve()) -- so an unlayoutable diagram (dangling edge, no nodes)
retries with actionable feedback instead of silently exporting a placeholder
box. These tests pin that lint and its retry recovery deterministically, with
no live provider (so no free-tier rate limit)."""

import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-diag-"))

import asyncio  # noqa: E402

from docloom.ir import (  # noqa: E402
    Diagram, DiagramEdge, DiagramNode, Paragraph, Slide, plain,
)

from docloom_studio import providers as providers_mod  # noqa: E402
from docloom_studio.generate import (  # noqa: E402
    DocSection, _diagram_block_errors, _section_errors, _slide_content_errors,
    _slide_hard_errors,
)
from docloom_studio.providers import ProviderConfig, generate_validated  # noqa: E402


def _good() -> Diagram:
    return Diagram(title="Flow",
                   nodes=[DiagramNode(id="a", label="Client"),
                          DiagramNode(id="b", label="API")],
                   edges=[DiagramEdge(source="a", target="b")])


def _dangling() -> Diagram:
    return Diagram(title="Broken",
                   nodes=[DiagramNode(id="a", label="Client")],
                   edges=[DiagramEdge(source="a", target="ghost")])


def test_valid_diagram_block_passes():
    assert _diagram_block_errors([_good()]) == []


def test_dangling_edge_diagram_is_flagged():
    errs = _diagram_block_errors([_dangling()])
    assert errs and "node id" in errs[0]


def test_diagram_with_no_nodes_is_flagged():
    assert _diagram_block_errors([Diagram(title="Empty")])


def test_diagram_only_slide_is_not_empty():
    # a diagram is a real visual: a content slide carrying only a diagram must
    # NOT be flagged as an empty-content slide (it is not a title over blank).
    slide = Slide(layout="content", title="How a request flows", blocks=[_good()])
    assert _slide_content_errors(slide) == []


def test_slide_hard_errors_flags_a_broken_diagram():
    slide = Slide(layout="content", title="How a request flows", blocks=[_dangling()])
    assert _slide_hard_errors("Deck", slide, set())


def test_section_errors_flag_a_broken_diagram():
    section = DocSection(blocks=[Paragraph(text=plain("Intro.")), _dangling()])
    assert _section_errors("Doc", section, set())


def test_section_diagram_gate_is_clean_for_a_valid_diagram():
    section = DocSection(blocks=[Paragraph(text=plain("Intro.")), _good()])
    assert _diagram_block_errors(section.blocks) == []


def test_broken_diagram_slide_retries_until_layoutable():
    """End-to-end through the real retry loop: round 1 emits a diagram with a
    dangling edge (cannot lay out), round 2 emits a valid one. The loop must
    stop at round 2 and return the valid-diagram slide -- proving the diagram
    gate, not a parse error, drove the retry and that a real diagram ships."""
    from docloom.llm import parse_llm_output

    calls = {"n": 0}

    async def fake_complete(cfg, messages, schema=None, temperature=0.4, max_tokens=0):
        calls["n"] += 1
        d = _dangling() if calls["n"] == 1 else _good()
        return Slide(layout="content", title="How a request flows",
                     blocks=[d]).model_dump_json()

    async def run():
        return await generate_validated(
            ProviderConfig(kind="openai", base_url="x", api_key="k", model="m"),
            [{"role": "system", "content": "draft ONE slide"},
             {"role": "user", "content": "go"}],
            schema={},
            parse=lambda t: parse_llm_output(t, Slide),
            lint_fn=lambda s: _slide_hard_errors("Deck", s, set()),
        )

    orig = providers_mod.complete
    providers_mod.complete = fake_complete
    try:
        slide = asyncio.run(run())
    finally:
        providers_mod.complete = orig

    assert calls["n"] == 2
    assert slide.blocks[0].type == "diagram"
    assert _diagram_block_errors(slide.blocks) == []
