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

    # an asset on disk
    asset_id = new_id()
    adir = data_dir() / "assets" / asset_id
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "logo.png").write_bytes(b"\x89PNG\r\n")
    execute("INSERT INTO assets (id, type, filename, tags, created) "
            "VALUES (?, 'logo', 'logo.png', '', ?)", (asset_id, now()))

    payload = {"ir": {"title": "T", "blocks": [
        {"type": "image", "path": f"asset://{asset_id}", "alt": "logo"},
    ]}, "theme_name": "paper"}
    doc = load_document(payload)
    baked = bake(doc)
    assert baked.blocks[0].path.endswith("logo.png")
    assert "asset://" not in baked.blocks[0].path


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
