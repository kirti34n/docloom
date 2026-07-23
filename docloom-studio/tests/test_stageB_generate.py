"""Stage B: deck image generation (CONTRACT C7) and infographic hard char
caps (CONTRACT C8), both owned in generate.py.

- _infographic_errors is the lint_fn generate_validated's retry loop
  actually calls; it must flag an over-long label/desc/title and a bad item
  count. docloom.llm.llm_schema strips Field(max_length=...) before the
  model ever sees a schema-level cap, so this lint_fn is the only thing that
  enforces it (see the long comment above IG_ITEMS_MIN in generate.py).
- _clamp_text is the deterministic backstop applied after generation, in
  case the model still ships an over-length value once every lint retry is
  spent.
- _resolve_deck_images is now async (the C7 signature change) and awaited at
  its one call site in run_deck_pipeline. With AI image generation disabled
  (the default), an unmatched hero/image_left/image_right slot must be left
  exactly as authored: no crash, no path, the function itself still a plain
  awaitable. With generation enabled, a successful call fills the slot the
  same way an asset-library match would, and a provider failure is swallowed
  per slot (ctx sees "image"/"skipped"), never sinking the deck.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-stageB-generate-"))

import pytest  # noqa: E402

from docloom import Document, Slide  # noqa: E402
from docloom_studio import assets as assets_mod  # noqa: E402
from docloom_studio import generate as gen  # noqa: E402
from docloom_studio import providers as providers_mod  # noqa: E402
from docloom_studio.db import execute, init_db, new_id, now  # noqa: E402
from docloom_studio.generate import InfographicItem, InfographicSpec  # noqa: E402
from docloom_studio.settings import set_setting  # noqa: E402


@pytest.fixture(autouse=True)
def _db():
    init_db()


def _user() -> str:
    uid = new_id()
    execute("INSERT INTO users (id, email, password_hash, created) VALUES (?, ?, ?, ?)",
            (uid, f"{uid}@t.local", "x", now()))
    return uid


def _notebook(user_id: str) -> str:
    wid = new_id()
    execute("INSERT INTO workspaces (id, user_id, name, created) VALUES (?, ?, ?, ?)",
            (wid, user_id, "w", now()))
    nb = new_id()
    execute("INSERT INTO notebooks (id, name, workspace_id, created, updated) "
            "VALUES (?, ?, ?, ?, ?)", (nb, "nb", wid, now(), now()))
    return nb


def _enable_image_gen(user_id: str) -> None:
    set_setting("provider.image", {
        "kind": "gemini", "base_url": "https://generativelanguage.googleapis.com",
        "api_key": "", "model": "gemini-2.5-flash-image", "enabled": True,
    }, user_id)


class FakeCtx:
    def __init__(self):
        self.events = []

    def emit(self, stage, status="running", detail="", data=None):
        self.events.append((stage, status, detail, data))


async def _no_stock_photo(query, user_id):
    """Deterministic stand-in for assets.resolve_stock_photo: always misses,
    like every stock search does when nothing matches -- and touches no
    network at all, so tests stay fast and offline."""
    return None


# ================================================== 1. infographic hard caps


def test_infographic_errors_ignores_length_so_generation_never_hardfails():
    # Over-long title/label/desc must NOT be a hard lint error: that could
    # exhaust every retry and fail the whole generation. _clamp_text (applied
    # after generate_validated) trims them deterministically instead, so the
    # lint_fn only enforces item count (the one thing the clamp cannot fix).
    spec = InfographicSpec(title="T" * 60, items=[
        InfographicItem(label="A" * 40, desc="D" * 120),
        InfographicItem(label="short", desc="ok"),
        InfographicItem(label="short", desc="ok"),
    ])
    assert gen._infographic_errors(spec) == []
    # the deterministic backstop keeps every value within its card budget
    assert len(gen._clamp_text(spec.title, gen.IG_TITLE_MAX)) <= gen.IG_TITLE_MAX
    assert len(gen._clamp_text(spec.items[0].label, gen.IG_LABEL_MAX)) <= gen.IG_LABEL_MAX
    assert len(gen._clamp_text(spec.items[0].desc, gen.IG_DESC_MAX)) <= gen.IG_DESC_MAX


def test_infographic_errors_flags_bad_item_count():
    too_few = InfographicSpec(title="T", items=[InfographicItem(label="a", desc="")])
    too_many = InfographicSpec(title="T", items=[
        InfographicItem(label=str(i), desc="") for i in range(9)])
    assert any("items" in e for e in gen._infographic_errors(too_few))
    assert any("items" in e for e in gen._infographic_errors(too_many))


def test_infographic_errors_passes_a_valid_spec():
    spec = InfographicSpec(title="A short punchy title", items=[
        InfographicItem(label="Plan", desc="Set the roadmap"),
        InfographicItem(label="Build", desc="Ship the first version"),
        InfographicItem(label="Measure", desc="Check it actually worked"),
    ])
    assert gen._infographic_errors(spec) == []


def test_clamp_text_leaves_short_text_untouched():
    assert gen._clamp_text("short label", 24) == "short label"


def test_clamp_text_cuts_at_a_word_boundary():
    text = "Continuous Integration and Delivery Pipeline"
    clamped = gen._clamp_text(text, 24)
    assert len(clamped) <= 24
    assert clamped == "Continuous Integration"  # cut before "and", not mid-word


def test_clamp_text_hard_cuts_one_long_token():
    clamped = gen._clamp_text("a" * 50, 24)
    assert len(clamped) == 24


# ============================================== 2. deck image slot resolver


def test_resolve_deck_images_is_a_coroutine_function():
    # the C7 signature change: async, and awaited at its one call site
    assert inspect.iscoroutinefunction(gen._resolve_deck_images)


def test_resolve_deck_images_disabled_leaves_unmatched_slot_untouched(monkeypatch):
    """AI image generation is OFF by default (no assets, no provider.image
    override), and the free stock-photo search also misses (mocked here so
    the test never touches the network): an unmatched hero slot must survive
    exactly as authored, no crash, still just the model's own query, and
    _resolve_deck_images must still be a plain awaitable coroutine."""
    monkeypatch.setattr(assets_mod, "resolve_stock_photo",
                        _no_stock_photo, raising=False)
    u = _user()
    doc = Document(title="Deck", slides=[
        Slide(layout="hero", title="A team meeting",
              image={"query": "remote team standup"}),
    ])
    ctx = FakeCtx()

    asyncio.run(gen._resolve_deck_images(doc, u, ctx))

    hero = doc.slides[0]
    assert hero.image is not None
    assert hero.image.query == "remote team standup"
    assert hero.image.path is None  # still unresolved, rendered empty
    assert hero.image.asset_id is None
    # no image event at all: paid generation was never attempted while
    # disabled (the stock-photo fallback is silent, like a tagged-asset miss)
    assert not any(e[0] == "image" for e in ctx.events)


def test_resolve_deck_images_disabled_also_works_with_no_ctx(monkeypatch):
    """_resolve_deck_images must not require a ctx (run_deck_pipeline always
    passes one, but the function itself stays awaitable without it, e.g. for
    direct unit testing)."""
    monkeypatch.setattr(assets_mod, "resolve_stock_photo",
                        _no_stock_photo, raising=False)
    u = _user()
    doc = Document(title="Deck", slides=[
        Slide(layout="image_right", title="Untouched",
              image={"query": "a lighthouse"}),
    ])
    asyncio.run(gen._resolve_deck_images(doc, u))  # no ctx, must not raise
    assert doc.slides[0].image.path is None


def test_resolve_deck_images_still_fills_from_a_matching_asset_when_disabled():
    """Generation being off must not regress the existing asset-match path."""
    from docloom_studio.settings import data_dir

    u = _user()
    aid = new_id()
    adir = data_dir() / "assets" / aid
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    execute("INSERT INTO assets (id, type, filename, tags, user_id, created) "
            "VALUES (?, 'image', 'pic.png', 'remote team', ?, ?)", (aid, u, now()))

    doc = Document(title="Deck", slides=[
        Slide(layout="hero", title="The team", image={"query": "remote team"}),
    ])
    asyncio.run(gen._resolve_deck_images(doc, u))
    assert doc.slides[0].image.path == f"asset://{aid}"


def test_resolve_deck_images_falls_back_to_a_stock_photo_before_paid_gen(monkeypatch):
    """CONTRACT (item 3): the free stock-photo search sits between a
    tagged-asset match and paid Gemini generation. No asset matches here, so
    a stock hit must fill the slot and paid generation must never be
    attempted at all."""
    u = _user()
    _enable_image_gen(u)  # even with generation enabled, stock wins first

    async def fake_stock_photo(query, user_id):
        assert query == "remote team standup"
        assert user_id == u
        return "stock-asset-1"

    def fail_if_called(cfg, prompt, *, aspect_ratio="16:9"):
        raise AssertionError("paid generation must not run when stock photo hit")

    monkeypatch.setattr(assets_mod, "resolve_stock_photo", fake_stock_photo, raising=False)
    monkeypatch.setattr(providers_mod, "generate_image", fail_if_called, raising=False)
    monkeypatch.setattr(providers_mod, "ImageProviderConfig", dict, raising=False)

    doc = Document(title="Deck", slides=[
        Slide(layout="hero", title="A team meeting",
              image={"query": "remote team standup"}),
    ])
    ctx = FakeCtx()
    asyncio.run(gen._resolve_deck_images(doc, u, ctx))

    hero = doc.slides[0]
    assert hero.image.asset_id == "stock-asset-1"
    assert hero.image.path == "asset://stock-asset-1"
    # no paid-generation "image" event: the stock photo filled it first
    assert not any(e[0] == "image" for e in ctx.events)


def test_resolve_deck_images_resolves_inline_image_blocks(monkeypatch):
    """An inline `image` block inside a slide's body (e.g. a two_column pane)
    with a query but no path/asset must be resolved the same way a slide's
    hero slot is -- not left to ship as a permanently empty slot."""
    from docloom import Paragraph

    async def fake_stock_photo(query, user_id):
        return "inline-asset-1" if query == "a lighthouse" else None

    monkeypatch.setattr(assets_mod, "resolve_stock_photo", fake_stock_photo, raising=False)

    u = _user()
    doc = Document(title="Deck", slides=[
        Slide(layout="two_column", title="Compare",
              blocks=[Paragraph(text="left")],
              right=[{"type": "image", "query": "a lighthouse"}]),
    ])
    asyncio.run(gen._resolve_deck_images(doc, u))
    resolved = doc.slides[0].right[0]
    assert resolved.asset_id == "inline-asset-1"
    assert resolved.path == "asset://inline-asset-1"


def test_resolve_deck_images_enabled_generates_and_fills_the_slot(monkeypatch):
    """With generation enabled and no asset or stock-photo match, the
    enriched prompt goes to generate_image, the returned bytes go to
    save_generated_image, and the slot fills exactly like an asset match
    would (asset_id + asset:// path)."""
    u = _user()
    _enable_image_gen(u)

    seen_prompts = []

    async def fake_generate_image(cfg, prompt, *, aspect_ratio="16:9"):
        seen_prompts.append((prompt, aspect_ratio))
        return b"\x89PNG\r\n\x1a\nfake"

    def fake_save_generated_image(user_id, data, *, prompt, ext=".png"):
        assert user_id == u
        assert data == b"\x89PNG\r\n\x1a\nfake"
        return "genimg-1"

    # generate_image/ImageProviderConfig (providers.py) and
    # save_generated_image (assets.py) are CONTRACT C7 items owned by other
    # files; stand in for them here (raising=False: they may not exist yet
    # in a fresh Stage B checkout) so this test exercises generate.py's own
    # wiring in isolation. resolve_stock_photo is mocked to miss so the flow
    # actually reaches paid generation instead of a real network call.
    monkeypatch.setattr(assets_mod, "resolve_stock_photo", _no_stock_photo, raising=False)
    monkeypatch.setattr(providers_mod, "generate_image", fake_generate_image, raising=False)
    monkeypatch.setattr(providers_mod, "ImageProviderConfig", dict, raising=False)
    monkeypatch.setattr(assets_mod, "save_generated_image",
                        fake_save_generated_image, raising=False)

    doc = Document(title="Deck", slides=[
        Slide(layout="hero", title="A team meeting",
              image={"query": "remote team standup"}),
    ])
    ctx = FakeCtx()
    asyncio.run(gen._resolve_deck_images(doc, u, ctx))

    hero = doc.slides[0]
    assert hero.image.asset_id == "genimg-1"
    assert hero.image.path == "asset://genimg-1"
    assert seen_prompts and "remote team standup" in seen_prompts[0][0]
    assert seen_prompts[0][1] == "16:9"  # hero layout's aspect
    stages = [(e[0], e[1]) for e in ctx.events]
    assert ("image", "running") in stages and ("image", "done") in stages


def test_resolve_deck_images_enabled_swallows_an_oversized_image_value_error(monkeypatch):
    """assets.save_generated_image raises a plain ValueError (not a
    ProviderError) when the generated bytes exceed its size cap -- a real,
    content-level rejection, not a network fault. It must be swallowed the
    same as a provider failure, not propagate and sink the whole deck."""
    u = _user()
    _enable_image_gen(u)

    async def fake_generate_image(cfg, prompt, *, aspect_ratio="16:9"):
        return b"\x89PNG\r\n\x1a\nfake"

    def oversized_save_generated_image(user_id, data, *, prompt, ext=".png"):
        raise ValueError("generated image exceeds 50 MB limit")

    monkeypatch.setattr(assets_mod, "resolve_stock_photo", _no_stock_photo, raising=False)
    monkeypatch.setattr(providers_mod, "generate_image", fake_generate_image, raising=False)
    monkeypatch.setattr(providers_mod, "ImageProviderConfig", dict, raising=False)
    monkeypatch.setattr(assets_mod, "save_generated_image",
                        oversized_save_generated_image, raising=False)

    doc = Document(title="Deck", slides=[
        Slide(layout="hero", title="A team meeting", image={"query": "big picture"}),
    ])
    ctx = FakeCtx()
    asyncio.run(gen._resolve_deck_images(doc, u, ctx))  # must not raise

    slot = doc.slides[0]
    assert slot.image.path is None
    assert slot.image.asset_id is None
    stages = [(e[0], e[1]) for e in ctx.events]
    assert ("image", "skipped") in stages


def test_resolve_deck_images_enabled_uses_a_narrower_aspect_for_side_images():
    prompt, aspect = gen._enrich_image_prompt("a lighthouse", "image_left")
    assert aspect == "4:3"
    assert "a lighthouse" in prompt
    assert "no text" in prompt  # style suffix present


def test_resolve_deck_images_enabled_swallows_a_provider_failure(monkeypatch):
    """A generation failure (refusal, timeout, ...) must not raise: the slot
    stays unresolved (renders empty) and the pipeline keeps going, the same
    swallow every per-slide text call in this file already gets."""
    u = _user()
    _enable_image_gen(u)

    async def failing_generate_image(cfg, prompt, *, aspect_ratio="16:9"):
        raise providers_mod.ProviderError("gemini blocked the prompt: SAFETY")

    monkeypatch.setattr(assets_mod, "resolve_stock_photo", _no_stock_photo, raising=False)
    monkeypatch.setattr(providers_mod, "generate_image", failing_generate_image, raising=False)
    monkeypatch.setattr(providers_mod, "ImageProviderConfig", dict, raising=False)

    doc = Document(title="Deck", slides=[
        Slide(layout="image_left", title="Untouched", image={"query": "a lighthouse"}),
    ])
    ctx = FakeCtx()
    asyncio.run(gen._resolve_deck_images(doc, u, ctx))  # must not raise

    slot = doc.slides[0]
    assert slot.image.path is None
    assert slot.image.asset_id is None
    stages = [(e[0], e[1]) for e in ctx.events]
    assert ("image", "skipped") in stages


def test_deck_pipeline_relaxes_image_gate_when_generation_enabled(monkeypatch):
    """CONTRACT C7: image layouts must be offered to the outline/slide model
    even with an empty asset library, as long as AI image generation is
    enabled -- otherwise a user with no uploaded pictures could never get a
    hero/image slide at all."""
    u = _user()
    _enable_image_gen(u)
    nb = _notebook(u)

    seen = {}

    async def fake_gv(cfg, messages, schema, parse, lint_fn=None, max_rounds=3, on_round=None):
        if "Draft slide" not in messages[-1]["content"]:
            seen["outline_system"] = messages[0]["content"]
            return gen.Outline(deck_title="T", slides=[
                gen.OutlineItem(title="One", layout="content", intent="x")])
        seen["slide_system"] = messages[0]["content"]
        return Slide(layout="content", title="One",
                     blocks=[{"type": "paragraph", "text": "hi"}])

    monkeypatch.setattr(gen, "generate_validated", fake_gv)
    aid = gen.create_artifact(nb, "deck")
    asyncio.run(gen.run_deck_pipeline(FakeCtx(), nb, aid, "no assets, AI images on"))

    assert "hero" in seen["outline_system"]  # IMAGE_LAYOUT_HINT, not NO_IMAGE_HINT
    assert "Use only section, content, two_column" not in seen["outline_system"]
    assert "image.query" in seen["slide_system"]  # IMAGE_SLIDE_HINT made it in too


def test_deck_pipeline_softens_image_hint_when_nothing_is_configured(monkeypatch):
    """CONTRACT (item 2/3): with generation OFF and no assets, hero/image_*
    layouts are still ALWAYS offered (a free, keyless Openverse stock-photo
    search can still fill them) -- the old hard ban ("Use only section,
    content, two_column, and quote") is gone. The model instead gets a
    softened, honest caveat that no curated library or paid generation is
    configured, not a restriction on which layouts it may use."""
    u = _user()
    nb = _notebook(u)

    seen = {}

    async def fake_gv(cfg, messages, schema, parse, lint_fn=None, max_rounds=3, on_round=None):
        if "Draft slide" not in messages[-1]["content"]:
            seen["outline_system"] = messages[0]["content"]
            return gen.Outline(deck_title="T", slides=[
                gen.OutlineItem(title="One", layout="content", intent="x")])
        return Slide(layout="content", title="One",
                     blocks=[{"type": "paragraph", "text": "hi"}])

    monkeypatch.setattr(gen, "generate_validated", fake_gv)
    aid = gen.create_artifact(nb, "deck")
    asyncio.run(gen.run_deck_pipeline(FakeCtx(), nb, aid, "no assets, no AI images"))

    assert "hero" in seen["outline_system"]  # still offered, unconditionally
    assert "Use only section, content, two_column" not in seen["outline_system"]
    assert "No curated image library or paid image generation" in seen["outline_system"]
