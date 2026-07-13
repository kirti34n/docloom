"""Stage A: blank/empty-content fixes and authoring-quality guards added to
generate.py (CONTRACT C4, plus the C5 anti-placeholder/empty-body wiring
that rides along with it).

- _sheet_content_errors (the per-sheet lint_fn in the split fallback, and
  folded into the one-shot SheetDoc lint_fn too) now requires columns AND
  rows, not just columns, so a header-only sheet is retried instead of
  shipped as a blank sheet.
- _slide_errors / _slide_content_errors (and the doc equivalent,
  _section_content_errors) flag placeholder text ("TODO", "lorem ipsum",
  "TBD", ...) and empty-body slides/sections, so filler or blank content is
  retried instead of shipped.
- the sheet one-shot path now catches GenerationFailed as well as
  TruncatedOutput around the one-shot call, so a result that keeps coming
  back empty after every retry still falls back to the split-by-sheet path
  instead of failing the whole job.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile

# isolate the app data dir before anything touches the DB
os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-stageA-"))

import pytest  # noqa: E402

from docloom import Column, Sheet, Slide  # noqa: E402
from docloom_studio import generate as gen  # noqa: E402
from docloom_studio.db import execute, init_db, new_id, now, query_one  # noqa: E402
from docloom_studio.generate import (  # noqa: E402
    DocSection, SheetOutline, SheetOutlineItem,
)
from docloom_studio.providers import GenerationFailed  # noqa: E402


@pytest.fixture(autouse=True)
def _db():
    init_db()


def _notebook() -> str:
    """A notebook owned by a fresh user's workspace (route auth scoping needs it)."""
    uid = new_id()
    execute("INSERT INTO users (id, email, password_hash, created) VALUES (?, ?, ?, ?)",
            (uid, f"{uid}@t.local", "x", now()))
    wid = new_id()
    execute("INSERT INTO workspaces (id, user_id, name, created) VALUES (?, ?, ?, ?)",
            (wid, uid, "w", now()))
    nb = new_id()
    execute("INSERT INTO notebooks (id, name, workspace_id, created, updated) "
            "VALUES (?, ?, ?, ?, ?)", (nb, "nb", wid, now(), now()))
    return nb


class FakeCtx:
    def __init__(self):
        self.events = []

    def emit(self, stage, status="running", detail="", data=None):
        self.events.append((stage, status, detail, data))


# ============================================================ 1. sheet lint


def test_sheet_content_errors_flags_empty_rows():
    """A header-only sheet (columns present, zero data rows) must fail the
    lint used both by the one-shot path and the per-sheet fallback loop,
    not ship silently as a blank sheet."""
    empty = Sheet(name="Q1", columns=[Column(header="Metric"), Column(header="Value")],
                 rows=[])
    errors = gen._sheet_content_errors(empty)
    assert errors
    assert "row" in errors[0]


def test_sheet_content_errors_passes_real_data():
    real = Sheet(name="Q1", columns=[Column(header="Metric"), Column(header="Value")],
                rows=[["Revenue", 1000]])
    assert gen._sheet_content_errors(real) == []


def test_sheet_content_errors_flags_placeholder_cell():
    placeholder = Sheet(name="Q1", columns=[Column(header="Metric"), Column(header="Value")],
                        rows=[["TBD", 0]])
    errors = gen._sheet_content_errors(placeholder)
    assert errors and "placeholder" in errors[0]


# =========================================================== 2. slide/doc lint


def test_slide_errors_flags_placeholder_body():
    slide = Slide(layout="content", title="A real takeaway sentence goes here",
                 blocks=[{"type": "paragraph", "text": "TODO: fill this in later"}])
    errors = gen._slide_errors("Deck", slide, set())
    assert any("placeholder" in e for e in errors)


def test_slide_errors_flags_empty_content_slide():
    slide = Slide(layout="content", title="A real takeaway sentence goes here")
    errors = gen._slide_errors("Deck", slide, set())
    assert any("no content" in e for e in errors)


def test_slide_errors_passes_real_grounded_content():
    slide = Slide(layout="content", title="Revenue grew 14 percent this quarter",
                 blocks=[{"type": "paragraph", "text": "Real, grounded content here."}])
    assert gen._slide_errors("Deck", slide, set()) == []


def test_section_content_errors_flags_placeholder_and_empty():
    assert gen._section_content_errors(DocSection(blocks=[]))

    placeholder = DocSection(blocks=[{"type": "paragraph",
                                      "text": "Lorem ipsum dolor sit amet"}])
    assert gen._section_content_errors(placeholder)

    real = DocSection(blocks=[{"type": "paragraph", "text": "Real, grounded content."}])
    assert gen._section_content_errors(real) == []


# =============================================== 3. sheet one-shot fallback


def test_sheet_pipeline_falls_back_on_generation_failed_not_only_truncated(monkeypatch):
    """A one-shot SheetDoc call that never produces a passing result within
    its retry budget raises GenerationFailed, not TruncatedOutput. Before
    this fix that propagated straight out of the pipeline and failed the
    whole job; it must now enter the same split-by-sheet fallback a
    truncation does."""
    outline = SheetOutline(title="Recovered Workbook", sheets=[
        SheetOutlineItem(name="Only", intent="the one real sheet")])
    sheet = Sheet(name="Only", columns=[Column(header="Metric")], rows=[["Revenue"]])

    async def fake_gv(cfg, messages, schema, parse, lint_fn=None, max_rounds=3, on_round=None):
        sys = messages[0]["content"]
        user = messages[-1]["content"]
        if "You produce spreadsheets" in sys:  # the one-shot whole-workbook call
            raise GenerationFailed([{"round": 1, "error": "kept returning zero sheets"}])
        if "You plan spreadsheet workbooks" in sys:
            return outline
        assert "You produce ONE sheet" in sys  # a per-sheet call in the split path
        return sheet

    monkeypatch.setattr(gen, "generate_validated", fake_gv)
    nb = _notebook()
    aid = gen.create_artifact(nb, "sheet")
    ctx = FakeCtx()
    asyncio.run(gen.run_sheet_pipeline(ctx, nb, aid, "recover me"))

    payload = json.loads(query_one(
        "SELECT payload_json FROM artifacts WHERE id = ?", (aid,))["payload_json"])
    sheets = payload["ir"]["sheets"]
    assert [s["name"] for s in sheets] == ["Only"]
    assert sheets[0]["rows"] == [["Revenue"]]
    # the job reached save; a GenerationFailed from the one-shot call was
    # never allowed to propagate out of the pipeline and fail the job
    save = [e for e in ctx.events if e[0] == "save" and e[1] == "done"]
    assert save


def test_sheet_pipeline_one_shot_generation_failed_without_outline_still_saves(monkeypatch):
    """If the split fallback's own outline call ALSO fails, the pipeline
    must still degrade to a default outline (C4's outline guard) rather
    than propagate a second failure out of the job."""

    async def fake_gv(cfg, messages, schema, parse, lint_fn=None, max_rounds=3, on_round=None):
        sys = messages[0]["content"]
        if "You produce spreadsheets" in sys:
            raise GenerationFailed([{"round": 1, "error": "kept returning zero sheets"}])
        if "You plan spreadsheet workbooks" in sys:
            raise GenerationFailed([{"round": 1, "error": "bad outline JSON"}])
        assert "You produce ONE sheet" in sys
        return Sheet(name="Data", columns=[Column(header="Metric")], rows=[["x"]])

    monkeypatch.setattr(gen, "generate_validated", fake_gv)
    nb = _notebook()
    aid = gen.create_artifact(nb, "sheet")
    ctx = FakeCtx()
    asyncio.run(gen.run_sheet_pipeline(ctx, nb, aid, "recover me too"))

    payload = json.loads(query_one(
        "SELECT payload_json FROM artifacts WHERE id = ?", (aid,))["payload_json"])
    sheets = payload["ir"]["sheets"]
    assert len(sheets) == 1
    save = [e for e in ctx.events if e[0] == "save" and e[1] == "done"]
    assert save
