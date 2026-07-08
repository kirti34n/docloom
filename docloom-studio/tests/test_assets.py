"""M8: asset resolver + brand kit actually feed generation and export."""

import asyncio
import json
import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="dstudio-assets-"))

import pytest  # noqa: E402

from docloom import Slide  # noqa: E402
from docloom_studio import generate as gen  # noqa: E402
from docloom_studio.assets import apply_brand, resolve_image  # noqa: E402
from docloom_studio.db import execute, init_db, new_id, now, query_one  # noqa: E402
from docloom_studio.irx import bake, load_document  # noqa: E402
from docloom_studio.settings import data_dir, set_setting  # noqa: E402


@pytest.fixture(autouse=True)
def _db():
    init_db()
    # children before parents (FKs are on)
    for t in ("artifact_versions", "artifacts", "sources", "assets", "notebooks"):
        execute(f"DELETE FROM {t}")
    set_setting("brand.active", {})


def _notebook() -> str:
    nb = new_id()
    execute("INSERT INTO notebooks (id, name, created, updated) VALUES (?, ?, ?, ?)",
            (nb, "nb", now(), now()))
    return nb


def _image_asset(tags: str) -> str:
    aid = new_id()
    adir = data_dir() / "assets" / aid
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    execute("INSERT INTO assets (id, type, filename, tags, created) "
            "VALUES (?, 'image', 'pic.png', ?, ?)", (aid, tags, now()))
    return aid


def test_resolver_matches_on_tags():
    _image_asset("volcano, lava")
    team = _image_asset("remote, team, collaboration")
    assert resolve_image("a remote team on a call") == team
    assert resolve_image("nothing relevant xyzzy") is None


def test_generation_fills_image_slot_and_bake_resolves(monkeypatch):
    aid = _image_asset("remote, team")
    outline = gen.Outline(deck_title="Async", slides=[
        gen.OutlineItem(title="The team", layout="image_left", intent="who")])

    async def fake_gv(cfg, messages, schema, parse, lint_fn=None, max_rounds=3,
                      on_round=None):
        if "Draft slide" not in messages[-1]["content"]:
            return outline
        return Slide(layout="image_left", title="The team",
                     image={"query": "remote team"},
                     blocks=[{"type": "paragraph", "text": "We are distributed."}])

    monkeypatch.setattr(gen, "generate_validated", fake_gv)

    nb = _notebook()
    art = gen.create_artifact(nb, "deck")

    class Ctx:
        def emit(self, *a, **k):
            pass

    asyncio.run(gen.run_deck_pipeline(Ctx(), nb, art, "async work"))
    payload = json.loads(query_one(
        "SELECT payload_json FROM artifacts WHERE id = ?", (art,))["payload_json"])

    # the image slot was filled from the asset
    img = payload["ir"]["slides"][1]["image"]
    assert img["path"] == f"asset://{aid}"
    # bake resolves it to the real file on disk
    baked = bake(load_document(payload))
    assert baked.slides[1].image.path.endswith("pic.png")
    assert "asset://" not in baked.slides[1].image.path


def test_brand_logo_lands_on_title_slide(monkeypatch):
    logo = _image_asset("logo")
    set_setting("brand.active", {"accent": "#ff0066", "logo_asset_id": logo})
    outline = gen.Outline(deck_title="Branded", slides=[
        gen.OutlineItem(title="One", layout="content", intent="x")])

    async def fake_gv(cfg, messages, schema, parse, lint_fn=None, max_rounds=3,
                      on_round=None):
        if "Draft slide" not in messages[-1]["content"]:
            return outline
        return Slide(layout="content", title="One",
                     blocks=[{"type": "paragraph", "text": "hi"}])

    monkeypatch.setattr(gen, "generate_validated", fake_gv)
    nb = _notebook()
    art = gen.create_artifact(nb, "deck")

    class Ctx:
        def emit(self, *a, **k):
            pass

    asyncio.run(gen.run_deck_pipeline(Ctx(), nb, art, "branded deck"))
    payload = json.loads(query_one(
        "SELECT payload_json FROM artifacts WHERE id = ?", (art,))["payload_json"])
    assert payload["ir"]["slides"][0]["image"]["path"] == f"asset://{logo}"
    # brand accent overrides the theme at export
    assert apply_brand({"primary": "#000"})["accent"] == "#ff0066"
