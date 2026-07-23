"""Stage C: outline authoring variety + content router + free stock photos.

- _outline_errors is the outline lint_fn generate_validated's retry loop
  actually calls (extends the old count-only check): a wall of 'content'
  slides, a long deck with no section break, zero structural-variety
  slides, or a numbers-heavy deck with no visual must all come back as
  deterministic errors so the model gets asked to fix them, the same way
  _budget_errors reshapes a per-slide retry.
- _visual_router_hint / _visual_mismatch_errors are the deterministic content
  router: a textual nudge keyed off OutlineItem.visual, and the per-slide
  lint check that a slide claiming visual='X' actually carries an X block.
- assets.resolve_stock_photo (Openverse/Pexels) is unit tested with the
  network calls themselves stood in for -- no live HTTP in this suite.

None of this depends on a live LLM provider or live network.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-stageC-"))

import pytest  # noqa: E402

from docloom import (  # noqa: E402
    Chart, Diagram, Image, Paragraph, Slide, StatRow, Table,
)
from docloom_studio import assets as assets_mod  # noqa: E402
from docloom_studio import generate as gen  # noqa: E402
from docloom_studio.db import init_db  # noqa: E402


@pytest.fixture(autouse=True)
def _db():
    init_db()


# ============================================================ 1. _outline_errors


def _outline(layouts, visuals=None, intents=None):
    visuals = visuals or ["none"] * len(layouts)
    intents = intents or ["x"] * len(layouts)
    return gen.Outline(deck_title="T", slides=[
        gen.OutlineItem(title=f"S{i}", layout=layout, visual=visual, intent=intent)
        for i, (layout, visual, intent) in enumerate(zip(layouts, visuals, intents))
    ])


def test_outline_errors_flags_bad_slide_count():
    too_few = _outline(["content", "content"])
    too_many = _outline(["content"] * 15)
    assert any("3 and 14" in e for e in gen._outline_errors(too_few))
    assert any("3 and 14" in e for e in gen._outline_errors(too_many))


def test_outline_errors_flags_over_60pct_content():
    # 4/5 = 80% content, over the 60% cap
    o = _outline(["content", "content", "content", "content", "section"])
    errs = gen._outline_errors(o)
    assert any("60%" in e for e in errs)


def test_outline_errors_allows_exactly_60pct_content():
    # 3/5 = 60%, at the boundary, must NOT trip the >60% rule
    o = _outline(["content", "content", "content", "two_column", "section"])
    errs = gen._outline_errors(o)
    assert not any("60%" in e for e in errs)


def test_outline_errors_flags_six_plus_slides_with_no_section_break():
    o = _outline(["content", "two_column", "content", "quote", "content", "content"])
    errs = gen._outline_errors(o)
    assert any("section" in e and "chapter break" in e for e in errs)


def test_outline_errors_allows_five_slides_with_no_section_break():
    # the section-break rule only applies at 6+ slides
    o = _outline(["content", "two_column", "content", "quote", "content"])
    errs = gen._outline_errors(o)
    assert not any("chapter break" in e for e in errs)


def test_outline_errors_flags_zero_structural_variety():
    o = _outline(["content", "content", "content"])
    errs = gen._outline_errors(o)
    assert any("two_column, quote, or section" in e for e in errs)


def test_outline_errors_two_column_alone_satisfies_structural_variety():
    o = _outline(["content", "content", "two_column"])
    errs = gen._outline_errors(o)
    assert not any("two_column, quote, or section" in e for e in errs)


def test_outline_errors_flags_numeric_evidence_with_no_visual_on_long_deck():
    intents = ["revenue grew 42 percent"] + ["x"] * 7
    o = _outline(
        ["content"] * 4 + ["two_column", "section", "content", "content"],
        visuals=["none"] * 8, intents=intents,
    )
    errs = gen._outline_errors(o)
    assert any("numbers" in e for e in errs)


def test_outline_errors_numeric_evidence_satisfied_by_a_visual():
    intents = ["revenue grew 42 percent"] + ["x"] * 7
    o = _outline(
        ["content"] * 4 + ["two_column", "section", "content", "content"],
        visuals=["stats"] + ["none"] * 7, intents=intents,
    )
    errs = gen._outline_errors(o)
    assert not any("numbers" in e for e in errs)


def test_outline_errors_numeric_rule_does_not_apply_below_eight_slides():
    intents = ["revenue grew 42 percent"] + ["x"] * 4
    o = _outline(["content"] * 3 + ["two_column", "section"],
                 visuals=["none"] * 5, intents=intents)
    errs = gen._outline_errors(o)
    assert not any("numbers" in e for e in errs)


def test_outline_errors_passes_a_well_mixed_outline():
    o = _outline(
        ["section", "content", "two_column", "content", "quote",
         "content", "two_column", "content"],
        visuals=["none", "stats", "none", "chart", "none", "none", "none", "none"],
        intents=["x"] * 8,
    )
    assert gen._outline_errors(o) == []


def test_default_outline_itself_passes_outline_errors():
    """The deterministic fallback outline (used when the outline call fails
    every retry) must not itself trip the very lint it is a fallback for."""
    outline = gen._default_outline("a report about widget sales")
    assert gen._outline_errors(outline) == []
    assert len(outline.slides) >= 3


def test_default_outline_is_varied_not_flat_content():
    outline = gen._default_outline("topic")
    layouts = {s.layout for s in outline.slides}
    assert layouts != {"content"}  # not the old flat 4x'content' shape
    assert "section" in layouts or "two_column" in layouts


# =================================================== 2. content router hints


def test_visual_router_hint_covers_every_outline_visual():
    for visual in ("chart", "stats", "table", "diagram", "image", "none"):
        hint = gen._visual_router_hint(visual)
        assert hint and "Content router" in hint


def test_visual_router_hint_stats_mentions_big_number_and_row():
    hint = gen._visual_router_hint("stats")
    assert "EXACTLY ONE" in hint
    assert "2-4" in hint


def test_visual_router_hint_none_mentions_grid_and_timeline():
    hint = gen._visual_router_hint("none")
    assert "timeline" in hint
    assert "grid" in hint


def test_visual_router_hint_falls_back_for_unknown_value():
    assert gen._visual_router_hint("bogus") == gen._visual_router_hint("none")


# ============================================== 3. per-slide visual mismatch


def test_visual_mismatch_none_never_flags():
    slide = Slide(layout="content", title="T", blocks=[Paragraph(text="hi")])
    assert gen._visual_mismatch_errors("none", slide) == []


def test_visual_mismatch_flags_missing_chart():
    slide = Slide(layout="content", title="T", blocks=[Paragraph(text="hi")])
    assert any("chart" in e for e in gen._visual_mismatch_errors("chart", slide))


def test_visual_mismatch_passes_when_chart_present():
    slide = Slide(layout="content", title="T", blocks=[
        Chart(chart="bar", labels=["x", "y"],
              series=[{"name": "A", "values": [1, 2]}]),
    ])
    assert gen._visual_mismatch_errors("chart", slide) == []


def test_visual_mismatch_flags_missing_stats():
    slide = Slide(layout="content", title="T", blocks=[Paragraph(text="hi")])
    assert any("stats" in e for e in gen._visual_mismatch_errors("stats", slide))


def test_visual_mismatch_passes_when_stats_present_in_right_column():
    # two_column: the visual can legitimately land in the right column
    slide = Slide(layout="two_column", title="T",
                 blocks=[Paragraph(text="left")],
                 right=[StatRow(items=[{"label": "Revenue", "value": "$1M"}])])
    assert gen._visual_mismatch_errors("stats", slide) == []


def test_visual_mismatch_flags_missing_table():
    slide = Slide(layout="content", title="T", blocks=[Paragraph(text="hi")])
    assert any("table" in e for e in gen._visual_mismatch_errors("table", slide))


def test_visual_mismatch_passes_when_table_present():
    slide = Slide(layout="content", title="T", blocks=[
        Table(header=["A"], rows=[["1"]])])
    assert gen._visual_mismatch_errors("table", slide) == []


def test_visual_mismatch_flags_missing_diagram():
    slide = Slide(layout="content", title="T", blocks=[Paragraph(text="hi")])
    assert any("diagram" in e for e in gen._visual_mismatch_errors("diagram", slide))


def test_visual_mismatch_passes_when_diagram_present():
    slide = Slide(layout="content", title="T", blocks=[
        Diagram(title="Flow", nodes=[{"id": "a", "label": "A"}], edges=[])])
    assert gen._visual_mismatch_errors("diagram", slide) == []


def test_visual_mismatch_flags_missing_image():
    slide = Slide(layout="hero", title="T")
    assert any("image" in e for e in gen._visual_mismatch_errors("image", slide))


def test_visual_mismatch_passes_when_slide_image_set():
    slide = Slide(layout="hero", title="T", image={"query": "a lighthouse"})
    assert gen._visual_mismatch_errors("image", slide) == []


def test_visual_mismatch_passes_when_inline_image_block_present():
    slide = Slide(layout="two_column", title="T",
                  blocks=[Paragraph(text="left")],
                  right=[Image(query="a lighthouse")])
    assert gen._visual_mismatch_errors("image", slide) == []


# ========================================= 4. per-slide _lint_fn integration


def test_deck_pipeline_retries_a_slide_missing_its_outlined_visual(monkeypatch):
    """End-to-end (with generate_validated stubbed): a slide outlined with
    visual='stats' but drafted with only a paragraph must be retried; once
    the model fixes it with a StatRow, the fixed slide ships."""
    outline = gen.Outline(deck_title="Deck", slides=[
        gen.OutlineItem(title="Only", layout="content", visual="stats",
                       intent="one key number"),
    ])
    attempts = {"n": 0}

    async def fake_gv(cfg, messages, schema, parse, lint_fn=None, max_rounds=3, on_round=None):
        if "Draft slide" not in messages[-1]["content"]:
            return outline
        attempts["n"] += 1
        if attempts["n"] == 1:
            bad = Slide(layout="content", title="Only",
                       blocks=[Paragraph(text="Revenue is high.")])
            assert lint_fn is not None and any(
                "stats" in e for e in lint_fn(bad))
            good = Slide(layout="content", title="Only",
                        blocks=[StatRow(items=[{"label": "Revenue", "value": "$1M"}])])
            assert lint_fn(good) == []
            return good
        raise AssertionError("should not need a second real round")

    monkeypatch.setattr(gen, "generate_validated", fake_gv)

    from docloom_studio.db import execute, new_id, now

    uid = new_id()
    execute("INSERT INTO users (id, email, password_hash, created) VALUES (?, ?, ?, ?)",
            (uid, f"{uid}@t.local", "x", now()))
    wid = new_id()
    execute("INSERT INTO workspaces (id, user_id, name, created) VALUES (?, ?, ?, ?)",
            (wid, uid, "w", now()))
    nb = new_id()
    execute("INSERT INTO notebooks (id, name, workspace_id, created, updated) "
            "VALUES (?, ?, ?, ?, ?)", (nb, "nb", wid, now(), now()))

    class Ctx:
        def emit(self, *a, **k):
            pass

    aid = gen.create_artifact(nb, "deck")
    asyncio.run(gen.run_deck_pipeline(Ctx(), nb, aid, "topic"))


# ==================================================== 5. free stock photos


def test_resolve_stock_photo_prefers_pexels_when_key_configured(monkeypatch):
    from docloom_studio.db import execute, new_id, now
    from docloom_studio.settings import set_setting

    uid = new_id()
    execute("INSERT INTO users (id, email, password_hash, created) VALUES (?, ?, ?, ?)",
            (uid, f"{uid}@t.local", "x", now()))
    set_setting("assets.pexels_key", "test-key-123", uid)

    calls = []

    async def fake_pexels(query, api_key):
        calls.append(("pexels", query, api_key))
        return b"pexels-bytes"

    async def fake_openverse(query):
        calls.append(("openverse", query))
        return b"openverse-bytes"

    monkeypatch.setattr(assets_mod, "_fetch_pexels", fake_pexels)
    monkeypatch.setattr(assets_mod, "_fetch_openverse", fake_openverse)

    aid = asyncio.run(assets_mod.resolve_stock_photo("a lighthouse", uid))
    assert calls == [("pexels", "a lighthouse", "test-key-123")]
    assert aid is not None

    row = assets_mod.query_one(
        "SELECT tags FROM assets WHERE id = ?", (aid,))
    assert row["tags"] == "a lighthouse"


def test_resolve_stock_photo_falls_back_to_openverse_without_a_key(monkeypatch):
    from docloom_studio.db import execute, new_id, now

    uid = new_id()
    execute("INSERT INTO users (id, email, password_hash, created) VALUES (?, ?, ?, ?)",
            (uid, f"{uid}@t.local", "x", now()))

    calls = []

    async def fake_pexels(query, api_key):
        raise AssertionError("must not call pexels without a key")

    async def fake_openverse(query):
        calls.append(query)
        return b"openverse-bytes"

    monkeypatch.setattr(assets_mod, "_fetch_pexels", fake_pexels)
    monkeypatch.setattr(assets_mod, "_fetch_openverse", fake_openverse)

    aid = asyncio.run(assets_mod.resolve_stock_photo("a lighthouse", uid))
    assert calls == ["a lighthouse"]
    assert aid is not None


def test_fetch_openverse_swallows_any_exception(monkeypatch):
    """_fetch_openverse itself is the real swallow boundary: any network/HTTP
    failure (timeout, DNS failure, non-200, malformed JSON) must degrade to
    None, never raise, so resolve_stock_photo never sinks a deck job."""
    class ExplodingClient:
        async def __aenter__(self):
            raise ConnectionError("simulated DNS failure")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(assets_mod.httpx, "AsyncClient", lambda **k: ExplodingClient())
    result = asyncio.run(assets_mod._fetch_openverse("a lighthouse"))
    assert result is None


def test_fetch_pexels_swallows_any_exception(monkeypatch):
    class ExplodingClient:
        async def __aenter__(self):
            raise ConnectionError("simulated DNS failure")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(assets_mod.httpx, "AsyncClient", lambda **k: ExplodingClient())
    result = asyncio.run(assets_mod._fetch_pexels("a lighthouse", "key"))
    assert result is None


def test_resolve_stock_photo_returns_none_when_nothing_found(monkeypatch):
    from docloom_studio.db import execute, new_id, now

    uid = new_id()
    execute("INSERT INTO users (id, email, password_hash, created) VALUES (?, ?, ?, ?)",
            (uid, f"{uid}@t.local", "x", now()))

    async def empty_openverse(query):
        return None

    monkeypatch.setattr(assets_mod, "_fetch_openverse", empty_openverse)
    assert asyncio.run(assets_mod.resolve_stock_photo("nothing", uid)) is None


def test_resolve_stock_photo_returns_none_for_empty_query():
    assert asyncio.run(assets_mod.resolve_stock_photo("", "some-user")) is None


# ================================================ 6. has_images considers pexels


def test_deck_pipeline_offers_images_without_ban_when_pexels_key_configured(monkeypatch):
    """assets.pexels_key alone (no assets, no AI generation) must count
    toward has_images -- previously dead code, now wired -- so the deck gets
    the plain IMAGE_LAYOUT_HINT with no NO_IMAGE_HINT caveat appended."""
    from docloom_studio.db import execute, new_id, now
    from docloom_studio.settings import set_setting

    uid = new_id()
    execute("INSERT INTO users (id, email, password_hash, created) VALUES (?, ?, ?, ?)",
            (uid, f"{uid}@t.local", "x", now()))
    wid = new_id()
    execute("INSERT INTO workspaces (id, user_id, name, created) VALUES (?, ?, ?, ?)",
            (wid, uid, "w", now()))
    nb = new_id()
    execute("INSERT INTO notebooks (id, name, workspace_id, created, updated) "
            "VALUES (?, ?, ?, ?, ?)", (nb, "nb", wid, now(), now()))
    set_setting("assets.pexels_key", "a-real-key", uid)

    seen = {}

    async def fake_gv(cfg, messages, schema, parse, lint_fn=None, max_rounds=3, on_round=None):
        if "Draft slide" not in messages[-1]["content"]:
            seen["outline_system"] = messages[0]["content"]
            return gen.Outline(deck_title="T", slides=[
                gen.OutlineItem(title="One", layout="content", intent="x")])
        return Slide(layout="content", title="One",
                     blocks=[Paragraph(text="hi")])

    monkeypatch.setattr(gen, "generate_validated", fake_gv)
    aid = gen.create_artifact(nb, "deck")

    class Ctx:
        def emit(self, *a, **k):
            pass

    asyncio.run(gen.run_deck_pipeline(Ctx(), nb, aid, "pexels configured"))
    assert "hero" in seen["outline_system"]
    assert "No curated image library" not in seen["outline_system"]
