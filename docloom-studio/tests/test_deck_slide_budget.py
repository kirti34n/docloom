"""Fit-by-budget: a slide that needs autofit shrink-to-fit is already a bad
slide, so per-slot capacity budgets (bullets/slide, chars/bullet, chars/title)
are enforced deterministically against the PARSED slide rather than relying
on advisory prompt text or JSON-schema constraints (docloom's llm_schema()
strips minLength/maxLength/pattern before the schema reaches the model, see
llm.py), and fed back through generate_validated's existing lint_fn retry
loop so only the offending slide is re-asked."""

import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-budget-"))

import asyncio  # noqa: E402

import pytest  # noqa: E402
from docloom.ir import BulletList, ListItem, Slide  # noqa: E402

from docloom_studio import generate as gen  # noqa: E402
from docloom_studio import providers as providers_mod  # noqa: E402
from docloom_studio.generate import (  # noqa: E402
    MAX_BULLET_CHARS, MAX_BULLETS_PER_SLIDE, MAX_TITLE_CHARS, _budget_errors,
    _slide_errors,
)
from docloom_studio.providers import ProviderConfig, generate_validated  # noqa: E402


def _bullets(*texts: str) -> BulletList:
    return BulletList(items=[ListItem(text=t) for t in texts])


def test_clean_slide_has_no_budget_errors():
    slide = Slide(layout="content", title="A short, punchy takeaway",
                  blocks=[_bullets("Short item one", "Short item two")])
    assert _budget_errors(slide) == []


def test_title_over_budget_is_flagged():
    long_title = "x" * (MAX_TITLE_CHARS + 1)
    slide = Slide(layout="content", title=long_title)
    errors = _budget_errors(slide)
    assert any("title" in e and str(MAX_TITLE_CHARS) in e for e in errors)


def test_title_at_exactly_budget_is_not_flagged():
    slide = Slide(layout="content", title="x" * MAX_TITLE_CHARS)
    assert _budget_errors(slide) == []


def test_too_many_bullets_is_flagged():
    slide = Slide(layout="content", title="ok",
                  blocks=[_bullets(*[f"item {i}" for i in range(MAX_BULLETS_PER_SLIDE + 1)])])
    errors = _budget_errors(slide)
    assert any("item" in e and str(MAX_BULLETS_PER_SLIDE) in e for e in errors)


def test_bullet_text_over_budget_is_flagged():
    slide = Slide(layout="content", title="ok",
                  blocks=[_bullets("y" * (MAX_BULLET_CHARS + 1))])
    errors = _budget_errors(slide)
    assert any(str(MAX_BULLET_CHARS) in e for e in errors)


def test_bullets_within_budget_across_both_columns_are_fine():
    slide = Slide(layout="two_column", title="ok",
                  blocks=[_bullets("left one", "left two")],
                  right=[_bullets("right one")])
    assert _budget_errors(slide) == []


def test_two_column_bullets_are_summed_per_slide_not_per_list():
    """docloom's own deck/too-many-bullets rule (lint.py:616-624) sums bullet
    items across the WHOLE slide, not per list. A regression once counted
    per-list here instead, which would MISS a two_column slide whose two
    lists are each comfortably under budget alone but tip the slide over in
    total (HIGH-2)."""
    half = MAX_BULLETS_PER_SLIDE // 2 + 1
    assert half <= MAX_BULLETS_PER_SLIDE  # each column alone is NOT over budget
    slide = Slide(layout="two_column", title="ok",
                  blocks=[_bullets(*[f"left {i}" for i in range(half)])],
                  right=[_bullets(*[f"right {i}" for i in range(half)])])
    errors = _budget_errors(slide)
    assert any("bullet" in e and str(MAX_BULLETS_PER_SLIDE) in e for e in errors)


def test_slide_errors_folds_in_budget_errors():
    # _slide_errors is the lint_fn actually wired into the per-slide
    # generate_validated call in run_deck_pipeline; a budget violation must
    # surface through it exactly like a docloom lint finding or the
    # placeholder-text check.
    slide = Slide(layout="content", title="x" * (MAX_TITLE_CHARS + 5))
    errors = _slide_errors("Deck", slide, set())
    assert any(str(MAX_TITLE_CHARS) in e for e in errors)


def test_budget_only_failure_can_recover_last_hard_ok_slide_via_closure():
    """Regression for HIGH-1 (content loss): generate_validated only ever
    returns the parsed object on a fully clean round, or raises
    GenerationFailed -- carrying round diagnostics only, never the object --
    once retries are exhausted. run_deck_pipeline recovers real content
    instead of discarding it by having its lint_fn capture the last
    hard-error-free parse as a side effect; this test exercises that exact
    mechanism against the real retry loop, for a slide whose ONLY problem is
    a budget overflow the model never fixes."""
    from docloom.llm import parse_llm_output

    from docloom_studio.generate import _slide_hard_errors
    from docloom_studio.providers import GenerationFailed

    over_budget_slide = Slide(
        layout="content", title="ok",
        blocks=[_bullets(*[f"item {i}" for i in range(MAX_BULLETS_PER_SLIDE + 1)])])

    async def fake_complete(cfg, messages, schema=None, temperature=0.4, max_tokens=0):
        # every round returns the SAME over-budget (but otherwise valid) slide
        return over_budget_slide.model_dump_json()

    last_hard_ok: dict[str, Slide] = {}

    def lint_fn(s):
        hard = _slide_hard_errors("Deck", s, set())
        if not hard:
            last_hard_ok["slide"] = s
        return hard + _budget_errors(s)

    async def run():
        return await generate_validated(
            ProviderConfig(kind="openai", base_url="x", api_key="k", model="m"),
            [{"role": "system", "content": "draft ONE slide"},
             {"role": "user", "content": "go"}],
            schema={},
            parse=lambda t: parse_llm_output(t, Slide),
            lint_fn=lint_fn,
        )

    orig = providers_mod.complete
    providers_mod.complete = fake_complete
    try:
        with pytest.raises(GenerationFailed):
            asyncio.run(run())
    finally:
        providers_mod.complete = orig

    # despite exhausting every retry round, real content was captured -- this
    # is exactly what a discard-into-empty-skeleton fallback would lose
    assert "slide" in last_hard_ok
    assert len(last_hard_ok["slide"].blocks[0].items) == MAX_BULLETS_PER_SLIDE + 1


def test_over_budget_slide_triggers_exactly_one_retry_of_that_slide():
    """End-to-end through the real retry loop (providers.generate_validated):
    round 1 returns an over-budget slide, round 2 returns a fixed one -- the
    loop must stop at round 2, proving the budget violation (not a parse
    error) is what drove the retry."""
    calls = {"n": 0}

    async def fake_complete(cfg, messages, schema=None, temperature=0.4, max_tokens=0):
        calls["n"] += 1
        if calls["n"] == 1:
            return Slide(layout="content", title="ok",
                        blocks=[_bullets(*[f"i{i}" for i in range(MAX_BULLETS_PER_SLIDE + 2)])]
                        ).model_dump_json()
        return Slide(layout="content", title="ok",
                    blocks=[_bullets("fits fine")]).model_dump_json()

    from docloom.llm import parse_llm_output

    async def run():
        return await generate_validated(
            ProviderConfig(kind="openai", base_url="x", api_key="k", model="m"),
            [{"role": "system", "content": "draft ONE slide"},
             {"role": "user", "content": "go"}],
            schema={},
            parse=lambda t: parse_llm_output(t, Slide),
            lint_fn=lambda s: gen._budget_errors(s),
        )

    orig = providers_mod.complete
    providers_mod.complete = fake_complete
    try:
        slide = asyncio.run(run())
    finally:
        providers_mod.complete = orig

    assert calls["n"] == 2
    assert len(slide.blocks[0].items) == 1
