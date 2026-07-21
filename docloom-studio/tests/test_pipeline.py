"""M1 pipeline logic, deterministic (stubbed provider — no live model)."""

import asyncio
import json
import os
import tempfile
import zipfile

import pytest

# isolate the app data dir before anything touches the DB
os.environ["DOCLOOM_STUDIO_HOME"] = tempfile.mkdtemp(prefix="docloom-studio-test-")

from docloom import Document, Slide  # noqa: E402
from docloom_studio import generate as gen  # noqa: E402
from docloom_studio.db import init_db, new_id, now, execute, query_one  # noqa: E402
from docloom_studio.generate import Outline, OutlineItem  # noqa: E402


class FakeCtx:
    def __init__(self):
        self.events = []

    def emit(self, stage, status="running", detail="", data=None):
        self.events.append((stage, status, data))


@pytest.fixture(autouse=True)
def _db():
    init_db()


def _notebook() -> str:
    """A notebook owned by a fresh user's workspace (route auth scoping needs it)."""
    uid = new_id()
    execute("INSERT INTO users (id, email, password_hash, created) VALUES (?, ?, ?, ?)",
            (uid, f"{uid}@test.local", "x", now()))
    wid = new_id()
    execute("INSERT INTO workspaces (id, user_id, name, created) VALUES (?, ?, ?, ?)",
            (wid, uid, "test-ws", now()))
    nb = new_id()
    execute("INSERT INTO notebooks (id, name, workspace_id, created, updated) "
            "VALUES (?, ?, ?, ?, ?)", (nb, "test", wid, now(), now()))
    return nb


def _owner(notebook_id: str) -> dict:
    """The {'id': user_id} that owns a notebook — for calling authed route handlers."""
    row = query_one(
        "SELECT w.user_id FROM notebooks n JOIN workspaces w ON w.id = n.workspace_id "
        "WHERE n.id = ?", (notebook_id,))
    return {"id": row["user_id"]}


def test_deck_pipeline_assembles_and_saves(monkeypatch):
    outline = Outline(deck_title="Async Standups",
                      slides=[OutlineItem(title="The problem", layout="content",
                                          intent="meetings interrupt flow"),
                              OutlineItem(title="Compared", layout="two_column",
                                          intent="sync vs async"),
                              OutlineItem(title="Ship it", layout="quote",
                                          intent="one takeaway")])
    slide_bodies = {
        "The problem": Slide(layout="content", title="The problem",
                             blocks=[{"type": "bullets",
                                      "items": [{"text": "Context-switch tax"}]}]),
        "Compared": Slide(layout="two_column", title="Compared",
                          blocks=[{"type": "paragraph", "text": "Sync"}],
                          right=[{"type": "paragraph", "text": "Async"}]),
        "Ship it": Slide(layout="quote",
                         blocks=[{"type": "quote", "text": "Make it boring."}]),
    }

    async def fake_generate_validated(cfg, messages, schema, parse, lint_fn=None,
                                      max_rounds=3, on_round=None):
        user = messages[-1]["content"]
        if "outline" in messages[0]["content"].lower() and "Draft slide" not in user:
            return outline
        for title, slide in slide_bodies.items():
            if f'"{title}"' in user:
                return slide
        return Slide(layout="content", title="fallback")

    monkeypatch.setattr(gen, "generate_validated", fake_generate_validated)

    nb = _notebook()
    artifact_id = gen.create_artifact(nb, "deck")
    ctx = FakeCtx()
    asyncio.run(gen.run_deck_pipeline(ctx, nb, artifact_id,
                                      "why async standups win"))

    # title slide + 3 outline slides
    stages = [e[0] for e in ctx.events]
    assert stages.count("slide") >= 3  # at least the done events
    save = [e for e in ctx.events if e[0] == "save" and e[1] == "done"]
    assert save and save[0][2]["title"] == "Async Standups"


def test_export_pptx_from_artifact(monkeypatch, tmp_path):
    from docloom_studio.artifacts import export_artifact, ExportRequest
    from docloom_studio.generate import save_artifact, create_artifact

    doc = Document(
        title="Deck", slides=[
            Slide(layout="title", title="Deck"),
            Slide(layout="content", title="Points",
                  blocks=[{"type": "bullets", "items": [{"text": "one"}]}]),
        ],
    )
    nb = _notebook()
    aid = create_artifact(nb, "deck")
    save_artifact(aid, "Deck", {"ir": doc.model_dump(exclude_none=True),
                                "theme_name": "slate", "brand_kit_id": None})

    result = asyncio.run(export_artifact(aid, ExportRequest(format="pptx"), user=_owner(nb)))
    from docloom_studio.settings import data_dir
    out = data_dir() / "exports" / aid / result["filename"]
    assert out.is_file()
    with zipfile.ZipFile(out) as z:
        assert "[Content_Types].xml" in z.namelist()


def test_irx_bake_resolves_asset_paths(monkeypatch):
    from docloom_studio.irx import bake, load_document
    from docloom_studio.settings import data_dir

    # an asset on disk, owned by a user
    user_id = new_id()
    asset_id = new_id()
    adir = data_dir() / "assets" / asset_id
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "logo.png").write_bytes(b"\x89PNG\r\n")
    execute("INSERT INTO assets (id, type, filename, tags, created, user_id) "
            "VALUES (?, 'logo', 'logo.png', '', ?, ?)", (asset_id, now(), user_id))

    payload = {"ir": {"title": "T", "blocks": [
        {"type": "image", "path": f"asset://{asset_id}", "alt": "logo"},
    ]}, "theme_name": "paper"}
    doc = load_document(payload)
    baked = bake(doc, user_id)
    assert baked.blocks[0].path.endswith("logo.png")
    assert "asset://" not in baked.blocks[0].path


_MINIMAL_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="60">'
    '<rect width="100" height="60" fill="#fff"/></svg>'
)


def test_irx_bake_rasterizes_artifact_svg_when_png_missing():
    """A diagram/infographic Artifact whose render.png was never saved (the
    resvg-py-missing hole) must not silently resolve to path=None: bake()
    falls back to rasterizing render.svg server-side."""
    from docloom_studio.irx import bake, load_document
    from docloom_studio.generate import create_artifact
    from docloom_studio.settings import data_dir

    nb = _notebook()
    user = _owner(nb)
    aid = create_artifact(nb, "diagram")
    adir = data_dir() / "artifacts" / aid
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "render.svg").write_text(_MINIMAL_SVG, encoding="utf-8")

    payload = {"ir": {"title": "T", "blocks": [
        {"type": "artifact", "kind": "diagram", "artifact_id": aid, "alt": "diagram"},
    ]}, "theme_name": "paper"}
    doc = load_document(payload)
    baked = bake(doc, user["id"])

    path = baked.blocks[0].path
    assert path, "Artifact should have resolved to a rasterized PNG, not None"
    from pathlib import Path
    assert Path(path).is_file()
    assert Path(path).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_irx_bake_invalidates_stale_cached_render_after_svg_edit():
    """MEDIUM-1 regression: InfographicEditor.tsx posts {svg} alone on every
    edit (no png_base64), so render.png is never rewritten by that save --
    it can only be brought up to date by bake()'s own server-side rasterize.
    An edit that changes render.svg's content must not keep serving the
    render.png cached for the *previous* content."""
    from pathlib import Path
    from docloom_studio.irx import bake, load_document
    from docloom_studio.generate import create_artifact
    from docloom_studio.settings import data_dir

    nb = _notebook()
    user = _owner(nb)
    aid = create_artifact(nb, "infographic")
    adir = data_dir() / "artifacts" / aid
    adir.mkdir(parents=True, exist_ok=True)

    payload = {"ir": {"title": "T", "blocks": [
        {"type": "artifact", "kind": "infographic", "artifact_id": aid, "alt": "info"},
    ]}, "theme_name": "paper"}
    doc = load_document(payload)

    # First bake: only render.svg exists -> server rasterizes and caches.
    (adir / "render.svg").write_text(_MINIMAL_SVG, encoding="utf-8")
    first_path = bake(doc, user["id"]).blocks[0].path
    assert first_path and Path(first_path).is_file()
    first_bytes = Path(first_path).read_bytes()

    # Now stamp a stale render.png with older content/mtime than the new
    # render.svg the "editor" is about to save, exactly like a cached render
    # from a previous edit that the infographic editor's svg-only save never
    # touches.
    stale_png = adir / "render.png"
    stale_png.write_bytes(b"\x89PNG\r\n\x1a\nSTALE")
    import os as _os
    import time as _time

    new_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="120">'
        '<rect width="200" height="120" fill="#000"/></svg>'
    )
    # ensure a strictly-later mtime than the stale png even on coarse clocks
    _time.sleep(0.05)
    (adir / "render.svg").write_text(new_svg, encoding="utf-8")
    newer = _time.time() + 1
    _os.utime(adir / "render.svg", (newer, newer))

    second_path = bake(doc, user["id"]).blocks[0].path
    assert second_path, "edited infographic must still resolve to a render"
    second_bytes = Path(second_path).read_bytes()

    assert second_bytes != b"\x89PNG\r\n\x1a\nSTALE", (
        "bake() served the stale cached render.png instead of "
        "re-rendering the edited render.svg")
    # the new render reflects the new (larger) svg, not the first bake's
    assert second_path != first_path or second_bytes != first_bytes


def test_irx_bake_reuses_cached_render_for_unchanged_svg_content():
    """Once a content hash has been rasterized and cached, re-exporting the
    same (unedited) artifact must reuse the cache rather than re-rasterizing
    or resolving to nothing."""
    from pathlib import Path
    from docloom_studio.irx import bake, load_document
    from docloom_studio.generate import create_artifact
    from docloom_studio.settings import data_dir

    nb = _notebook()
    user = _owner(nb)
    aid = create_artifact(nb, "infographic")
    adir = data_dir() / "artifacts" / aid
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "render.svg").write_text(_MINIMAL_SVG, encoding="utf-8")

    payload = {"ir": {"title": "T", "blocks": [
        {"type": "artifact", "kind": "infographic", "artifact_id": aid, "alt": "info"},
    ]}, "theme_name": "paper"}
    doc = load_document(payload)

    first_path = bake(doc, user["id"]).blocks[0].path
    second_path = bake(doc, user["id"]).blocks[0].path
    assert first_path == second_path
    assert Path(first_path).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_irx_resolve_artifact_render_cleans_up_temp_fallback(monkeypatch, caplog):
    """LOW-2 regression: when caching the rasterized PNG back to disk fails
    (e.g. read-only/full disk), the mkstemp fallback file must be tracked
    for cleanup instead of leaking forever."""
    import logging
    from pathlib import Path
    from docloom_studio import irx
    from docloom_studio.generate import create_artifact
    from docloom_studio.settings import data_dir

    nb = _notebook()
    aid = create_artifact(nb, "infographic")
    adir = data_dir() / "artifacts" / aid
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "render.svg").write_text(_MINIMAL_SVG, encoding="utf-8")

    real_write_bytes = Path.write_bytes

    def _boom(self, data):
        if self.name.startswith("render.") and self.suffix == ".png":
            raise OSError("simulated read-only artifacts dir")
        return real_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", _boom)

    irx._TEMP_RENDER_FILES.clear()
    with caplog.at_level(logging.WARNING, logger="docloom_studio.irx"):
        path = irx._resolve_artifact_render(aid)

    assert path is not None
    assert Path(path).is_file()
    assert path in irx._TEMP_RENDER_FILES

    irx._cleanup_temp_renders()
    assert not Path(path).exists(), "temp render fallback file was never cleaned up"


def test_irx_bake_logs_when_artifact_render_skipped_for_non_owner(caplog):
    """LOW-3 regression: the ownership guard must not silently short-circuit
    to path=None with no observable trace -- it should log, same as the
    other empty-slot cases, without weakening the ownership check itself."""
    import logging
    from docloom_studio.irx import bake, load_document
    from docloom_studio.generate import create_artifact
    from docloom_studio.settings import data_dir

    owner_nb = _notebook()
    owner = _owner(owner_nb)
    aid = create_artifact(owner_nb, "diagram")
    adir = data_dir() / "artifacts" / aid
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "render.png").write_bytes(b"\x89PNG\r\n\x1a\n OWNER_ONLY")

    other_nb = _notebook()
    other_user = _owner(other_nb)
    assert other_user["id"] != owner["id"]

    payload = {"ir": {"title": "T", "blocks": [
        {"type": "artifact", "kind": "diagram", "artifact_id": aid, "alt": "diagram"},
    ]}, "theme_name": "paper"}
    doc = load_document(payload)

    with caplog.at_level(logging.WARNING, logger="docloom_studio.irx"):
        baked = bake(doc, other_user["id"])

    assert baked.blocks[0].path is None  # security behaviour unchanged
    assert any(aid in r.message and "not own it" in r.message
               for r in caplog.records), "non-owner render skip must be logged"


def test_irx_bake_warns_when_artifact_has_no_render(caplog):
    """When neither render.png nor render.svg exist, bake() must log a
    warning instead of silently emitting path=None (the silent-blank-export
    hole this closes)."""
    import logging
    from docloom_studio.irx import bake, load_document
    from docloom_studio.generate import create_artifact

    nb = _notebook()
    user = _owner(nb)
    aid = create_artifact(nb, "diagram")  # no renders/ dir at all for this artifact

    payload = {"ir": {"title": "T", "blocks": [
        {"type": "artifact", "kind": "diagram", "artifact_id": aid, "alt": "diagram"},
    ]}, "theme_name": "paper"}
    doc = load_document(payload)
    with caplog.at_level(logging.WARNING, logger="docloom_studio.irx"):
        baked = bake(doc, user["id"])

    assert baked.blocks[0].path is None
    assert any(aid in r.message and "empty slot" in r.message for r in caplog.records)


def test_export_refuses_on_lint_errors():
    from docloom_studio.artifacts import export_artifact, ExportRequest
    from docloom_studio.generate import save_artifact, create_artifact
    from fastapi import HTTPException

    # a chart with mismatched series → chart/ragged-series (error)
    doc = Document(title="Bad", blocks=[
        {"type": "chart", "labels": ["a", "b"],
         "series": [{"name": "s", "values": [1.0]}]},
    ])
    nb = _notebook()
    aid = create_artifact(nb, "doc")
    save_artifact(aid, "Bad", {"ir": doc.model_dump(exclude_none=True),
                               "theme_name": "paper"})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(export_artifact(aid, ExportRequest(format="pptx"), user=_owner(nb)))
    assert exc.value.status_code == 422


def test_doc_pipeline_assembles_blocks(monkeypatch, tmp_path):
    from docloom_studio.generate import (
        DocOutline, DocOutlineItem, DocSection, run_doc_pipeline, create_artifact)
    from docloom import Document

    outline = DocOutline(doc_title="Async Report", sections=[
        DocOutlineItem(heading="Why", intent="motivation"),
        DocOutlineItem(heading="How", intent="mechanics")])

    async def fake_gv(cfg, messages, schema, parse, lint_fn=None, max_rounds=3, on_round=None):
        if "section" not in messages[0]["content"].lower() or "Section:" not in messages[-1]["content"]:
            return outline
        return DocSection(blocks=[{"type": "paragraph", "text": "Body text here."}])

    monkeypatch.setattr(gen, "generate_validated", fake_gv)
    nb = _notebook()
    aid = create_artifact(nb, "doc")
    asyncio.run(run_doc_pipeline(FakeCtx(), nb, aid, "async report"))

    payload = json.loads(query_one("SELECT payload_json FROM artifacts WHERE id = ?", (aid,))["payload_json"])
    blocks = payload["ir"]["blocks"]
    kinds = [b["type"] for b in blocks]
    assert kinds.count("heading") == 2 and "paragraph" in kinds
    # exports to docx
    from docloom_studio.artifacts import export_artifact, ExportRequest
    r = asyncio.run(export_artifact(aid, ExportRequest(format="docx"), user=_owner(nb)))
    from docloom_studio.settings import data_dir
    assert (data_dir() / "exports" / aid / r["filename"]).is_file()


def test_sheet_pipeline_assembles_and_exports(monkeypatch):
    from docloom_studio.generate import SheetDoc, run_sheet_pipeline, create_artifact
    from docloom import Sheet, Column

    doc = SheetDoc(title="Budget", sheets=[Sheet(
        name="Q1", columns=[Column(header="Item"), Column(header="Cost", format="$#,##0")],
        rows=[["Setup", 5000], ["Total", {"formula": "=SUM(B1:B1)"}]])])

    async def fake_gv(cfg, messages, schema, parse, lint_fn=None, max_rounds=3, on_round=None):
        return doc

    monkeypatch.setattr(gen, "generate_validated", fake_gv)
    nb = _notebook()
    aid = create_artifact(nb, "sheet")
    ctx = FakeCtx()
    asyncio.run(run_sheet_pipeline(ctx, nb, aid, "budget"))

    # the single-shot path must terminate its "sheet" stage, or the build UI
    # shows the unit spinning until navigation
    assert ("sheet", "done") in [(e[0], e[1]) for e in ctx.events]

    from docloom_studio.artifacts import export_artifact, ExportRequest
    import zipfile
    r = asyncio.run(export_artifact(aid, ExportRequest(format="xlsx"), user=_owner(nb)))
    from docloom_studio.settings import data_dir
    out = data_dir() / "exports" / aid / r["filename"]
    with zipfile.ZipFile(out) as z:
        assert "xl/workbook.xml" in z.namelist()


def test_podcast_pipeline_generates_script(monkeypatch):
    from docloom_studio.generate import (
        PodcastScript, PodcastTurn, create_artifact, run_podcast_pipeline)

    script = PodcastScript(title="Async Work", turns=[
        PodcastTurn(speaker="A", text="Welcome to the show on async standups."),
        PodcastTurn(speaker="B", text="They cut interruptions dramatically."),
        PodcastTurn(speaker="A", text="How so?"),
        PodcastTurn(speaker="B", text="No fixed meeting time to context-switch for."),
        PodcastTurn(speaker="A", text="Makes sense."),
        PodcastTurn(speaker="B", text="Write updates when it suits you."),
    ])

    async def fake_gv(cfg, messages, schema, parse, lint_fn=None, max_rounds=3,
                      on_round=None):
        return script

    monkeypatch.setattr(gen, "generate_validated", fake_gv)
    nb = _notebook()
    aid = create_artifact(nb, "podcast")
    asyncio.run(run_podcast_pipeline(FakeCtx(), nb, aid, "async standups"))

    payload = json.loads(query_one(
        "SELECT payload_json FROM artifacts WHERE id = ?", (aid,))["payload_json"])
    assert payload["script"]["title"] == "Async Work"
    assert len(payload["script"]["turns"]) == 6
    # Kokoro isn't installed here -> audio is skipped, transcript still saved
    assert payload["audio_path"] is None
