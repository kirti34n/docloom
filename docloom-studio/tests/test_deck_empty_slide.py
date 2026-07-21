"""Empty-content slides must not ship. A provider's structured output
sometimes returns a content-layout slide whose block is structurally present
but carries NO content -- most often a bullets/numbered list with an empty
items[] (observed frequently from Gemini's JSON mode). Such a slide passes a
naive "does a block exist?" check yet renders as a title over blank space.

_slide_content_errors treats that as a hard error, so generate_validated's
retry loop re-asks for the slide with "fill this" feedback instead of silently
shipping a blank. These tests pin BOTH halves of that fix deterministically
(no live provider, so no rate limit): the lint decision, and that the real
retry loop recovers an empty first-round slide into a filled one.
"""

import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-empty-"))

import asyncio  # noqa: E402

from docloom.ir import (  # noqa: E402
    BulletList, Chart, ListItem, NumberedList, Series, Slide,
)

from docloom_studio import generate as gen  # noqa: E402
from docloom_studio import providers as providers_mod  # noqa: E402
from docloom_studio.generate import (  # noqa: E402
    _slide_content_errors, _slide_hard_errors,
)
from docloom_studio.providers import ProviderConfig, generate_validated  # noqa: E402


def _bullets(*texts: str) -> BulletList:
    return BulletList(items=[ListItem(text=t) for t in texts])


# ------------------------------------------------------------------ the lint

def test_empty_bullet_list_on_content_slide_is_flagged():
    # the exact Gemini failure mode: a block IS present, but its items[] is
    # empty, so the slide renders as a bare title over blank space.
    slide = Slide(layout="content", title="A real takeaway sentence here",
                  blocks=[BulletList(items=[])])
    errors = _slide_content_errors(slide)
    assert errors, "an empty-items bullet list must be flagged, not shipped blank"
    assert "empty" in errors[0].lower()


def test_content_slide_with_no_blocks_at_all_is_flagged():
    slide = Slide(layout="content", title="Still needs a body")
    errors = _slide_content_errors(slide)
    assert errors and "no content" in errors[0].lower()


def test_empty_numbered_list_is_also_flagged():
    slide = Slide(layout="content", title="Steps that never arrived",
                  blocks=[NumberedList(items=[])])
    assert _slide_content_errors(slide)


def test_empty_body_across_both_columns_is_flagged():
    slide = Slide(layout="two_column", title="Two empty columns",
                  blocks=[BulletList(items=[])], right=[BulletList(items=[])])
    assert _slide_content_errors(slide)


def test_filled_slide_is_not_flagged():
    slide = Slide(layout="content", title="Support tickets fell 30 percent",
                  blocks=[_bullets("Rollout finished in March", "Volume halved by May")])
    assert _slide_content_errors(slide) == []


def test_visual_only_slide_is_not_flagged():
    # a chart with no title carries no body TEXT, but a standalone visual is
    # real content -- the slide is not a title-over-blank-space, so it must
    # not be flagged as empty even though _blocks_text yields nothing.
    chart = Chart(chart="bar", labels=["Q1", "Q2", "Q3"],
                  series=[Series(name="Signups", values=[10.0, 22.0, 41.0])])
    slide = Slide(layout="content", title="Signups tripled over three quarters",
                  blocks=[chart])
    assert _slide_content_errors(slide) == []


def test_title_layout_with_no_blocks_is_not_flagged():
    # title/section slides are legitimately body-less; only content-bearing
    # layouts require a filled body.
    assert _slide_content_errors(Slide(layout="title", title="The Deck")) == []


def test_section_layout_with_no_blocks_is_not_flagged():
    assert _slide_content_errors(Slide(layout="section", title="Part Two")) == []


def test_empty_content_surfaces_through_slide_hard_errors():
    # _slide_hard_errors is what run_deck_pipeline treats as grounds to
    # retry-or-discard; the empty-content finding must fold into it, not sit
    # only in the standalone helper.
    slide = Slide(layout="content", title="Bare title", blocks=[BulletList(items=[])])
    assert _slide_hard_errors("Deck", slide, set())


# ------------------------------------------------ the retry loop end-to-end

def test_empty_slide_triggers_retry_until_filled():
    """End-to-end through the REAL retry loop (providers.generate_validated),
    with the same lint_fn the per-slide call uses: round 1 returns an empty-
    bullets slide (the Gemini failure mode), round 2 returns a filled one. The
    loop must stop at round 2 and return the FILLED slide -- proving the empty
    slide, not a parse error, drove the retry and that the fix recovers it.

    This is the deterministic stand-in for the live fill-rate check, which the
    free-tier rate limit makes unreliable (a retry can 429 before it lands)."""
    from docloom.llm import parse_llm_output

    calls = {"n": 0}

    async def fake_complete(cfg, messages, schema=None, temperature=0.4, max_tokens=0):
        calls["n"] += 1
        if calls["n"] == 1:
            # structurally valid, parses fine, but the bullet list is empty
            return Slide(layout="content", title="Real takeaway sentence",
                         blocks=[BulletList(items=[])]).model_dump_json()
        return Slide(layout="content", title="Real takeaway sentence",
                     blocks=[_bullets("Grounded point one", "Grounded point two")]
                     ).model_dump_json()

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

    assert calls["n"] == 2, "the empty first-round slide must have driven exactly one retry"
    assert slide.blocks[0].items, "the recovered slide must carry real content"
    assert len(slide.blocks[0].items) == 2
