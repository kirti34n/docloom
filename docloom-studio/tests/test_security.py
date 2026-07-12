"""Security regression tests: the served-media route is confined to the
owning artifact's own directory (not the whole shared data dir), upload
guardrails (path traversal + size cap) on the asset library, the brand kit no
longer collapses primary/accent into one color, the session cookie's secure
flag, and the workspace_id migration backfill for pre-auth installs."""

import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-sec-"))

import io  # noqa: E402
import sqlite3  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from docloom_studio import db  # noqa: E402
from docloom_studio.db import execute, init_db, new_id, now, query_one  # noqa: E402
from docloom_studio.ingest import _source_dir  # noqa: E402
from docloom_studio.main import app  # noqa: E402
from docloom_studio.settings import data_dir  # noqa: E402


@pytest.fixture(autouse=True)
def _db():
    init_db()
    for t in ("chat_messages", "artifact_versions", "artifacts", "sources",
              "notebooks", "assets", "user_settings", "auth_sessions",
              "workspaces", "users"):
        execute(f"DELETE FROM {t}")


def _register(email: str) -> TestClient:
    c = TestClient(app)
    c.post("/api/auth/register", json={"email": email, "password": "password1"})
    return c


def _notebook(client: TestClient) -> str:
    wid = client.get("/api/workspaces").json()[0]["id"]
    return client.post(
        "/api/notebooks", json={"name": "n", "workspace_id": wid}).json()["id"]


def _artifact_row(notebook_id: str, kind: str = "diagram") -> str:
    aid = new_id()
    execute("INSERT INTO artifacts (id, notebook_id, kind, title, version, "
            "payload_json, created, updated) VALUES (?, ?, ?, 'T', 1, '{}', ?, ?)",
            (aid, notebook_id, kind, now(), now()))
    return aid


# ---- the old unscoped /api/files route is gone ----------------------------

def test_old_files_route_is_gone():
    a = _register("files-a@ex.com")
    assert a.get("/api/files", params={"path": "studio.db"}).status_code == 404
    assert a.get("/api/files", params={"path": "../studio.db"}).status_code == 404


# ---- new media route: scoped strictly to the owning artifact's directory --

def test_media_route_serves_owned_file_but_not_cross_tenant():
    a = _register("media-a@ex.com")
    b = _register("media-b@ex.com")
    aid = _artifact_row(_notebook(a))
    adir = data_dir() / "artifacts" / aid
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "render.png").write_bytes(b"\x89PNG\r\n\x1a\n fake-render")

    r = a.get(f"/api/artifacts/{aid}/media", params={"path": "render.png"})
    assert r.status_code == 200
    assert r.content == b"\x89PNG\r\n\x1a\n fake-render"

    assert b.get(f"/api/artifacts/{aid}/media",
                 params={"path": "render.png"}).status_code == 404  # other tenant
    assert TestClient(app).get(
        f"/api/artifacts/{aid}/media", params={"path": "render.png"}).status_code == 401  # anon


def test_media_route_cannot_escape_its_artifact_directory_to_studio_db():
    assert (data_dir() / "studio.db").is_file()  # otherwise this test proves nothing
    a = _register("escape-a@ex.com")
    aid = _artifact_row(_notebook(a))

    for traversal in ("../../studio.db", "../../../studio.db", "..\\..\\studio.db"):
        r = a.get(f"/api/artifacts/{aid}/media", params={"path": traversal})
        assert r.status_code == 404, traversal


def test_media_route_rejects_an_absolute_path():
    a = _register("abs-a@ex.com")
    aid = _artifact_row(_notebook(a))
    abs_path = str((data_dir() / "studio.db").resolve())
    assert a.get(f"/api/artifacts/{aid}/media",
                params={"path": abs_path}).status_code == 404


def test_media_route_cannot_reach_another_users_source_file():
    a = _register("reach-a@ex.com")
    b = _register("reach-b@ex.com")
    sid = new_id()
    execute("INSERT INTO sources (id, notebook_id, kind, title, status, "
            "context_mode, meta_json, created) VALUES (?, ?, 'file', 'secret', 'ready', "
            "'full', '{}', ?)", (sid, _notebook(b), now()))
    (_source_dir(sid) / "secret.txt").write_text("B's private data", encoding="utf-8")

    aid = _artifact_row(_notebook(a))
    r = a.get(f"/api/artifacts/{aid}/media",
             params={"path": f"../../sources/{sid}/secret.txt"})
    assert r.status_code == 404


# ---- asset upload guardrails ----------------------------------------------

def test_asset_upload_sanitizes_traversal_filename():
    a = _register("upload-a@ex.com")
    evil = "../../../evil.png"
    png = b"\x89PNG\r\n\x1a\n"
    r = a.post("/api/assets", files={"file": (evil, io.BytesIO(png), "image/png")},
              data={"type": "image"})
    assert r.status_code == 200, r.text
    aid = r.json()["id"]

    adir = data_dir() / "assets" / aid
    names = [p.name for p in adir.iterdir()]
    assert names == ["evil.png"]  # basename only, no path components survived
    assert not (data_dir() / "evil.png").exists()  # nothing escaped upward


def test_delete_asset_cannot_wipe_another_users_files():
    """One user deleting another user's asset id must not touch the owner's
    row OR its on-disk files (the DB delete is scoped, but the rmtree must be
    gated on ownership too)."""
    a = _register("delasset-a@ex.com")
    b = _register("delasset-b@ex.com")
    png = b"\x89PNG\r\n\x1a\n"
    aid = a.post("/api/assets", files={"file": ("a.png", io.BytesIO(png), "image/png")},
                 data={"type": "image"}).json()["id"]
    adir = data_dir() / "assets" / aid
    assert (adir / "a.png").exists()

    # attacker (b) tries to delete a's asset
    assert b.delete(f"/api/assets/{aid}").status_code == 404
    assert (adir / "a.png").exists()  # files untouched
    assert a.get(f"/api/assets/{aid}/file").status_code == 200  # still serves

    # owner can delete their own
    assert a.delete(f"/api/assets/{aid}").status_code == 200
    assert not adir.exists()


def test_asset_upload_rejects_oversized_file(monkeypatch):
    monkeypatch.setattr("docloom_studio.assets.MAX_UPLOAD_BYTES", 1024)
    a = _register("bigasset-a@ex.com")
    big = b"x" * 5000
    r = a.post("/api/assets", files={"file": ("big.png", big, "image/png")},
              data={"type": "image"})
    assert r.status_code == 413


# ---- brand kit: primary and accent are independent (finding #6) -----------

def test_brand_kit_primary_and_accent_apply_independently():
    from docloom_studio.assets import apply_brand

    a = _register("brand-a@ex.com")
    assert a.put("/api/brand-kit",
                json={"primary": "#111111", "accent": "#222222"}).status_code == 200
    got = a.get("/api/brand-kit").json()
    assert got["primary"] == "#111111"
    assert got["accent"] == "#222222"  # neither field clobbered the other

    uid = a.get("/api/auth/me").json()["id"]
    themed = apply_brand({"primary": "#000000", "accent": "#000000"}, uid)
    assert themed["primary"] == "#111111"
    assert themed["accent"] == "#222222"


def test_brand_kit_accent_alone_does_not_touch_primary():
    from docloom_studio.assets import apply_brand

    a = _register("brand-b@ex.com")
    a.put("/api/brand-kit", json={"accent": "#ff0066"})
    uid = a.get("/api/auth/me").json()["id"]
    # only accent was set; primary must keep the theme's own value, not flatten to it
    themed = apply_brand({"primary": "#abcdef", "accent": "#000000"}, uid)
    assert themed["primary"] == "#abcdef"
    assert themed["accent"] == "#ff0066"


# ---- session cookie secure flag --------------------------------------------

def test_session_cookie_secure_flag_follows_request_scheme():
    http_client = TestClient(app)
    r_http = http_client.post(
        "/api/auth/register", json={"email": "cookie-http@ex.com", "password": "password1"})
    assert "secure" not in r_http.headers.get("set-cookie", "").lower()

    https_client = TestClient(app, base_url="https://testserver")
    r_https = https_client.post(
        "/api/auth/register", json={"email": "cookie-https@ex.com", "password": "password1"})
    assert "secure" in r_https.headers.get("set-cookie", "").lower()


# ---- artifact status field + delete route ----------------------------------

def test_artifact_status_field_in_responses_and_settable():
    from docloom_studio.artifacts import set_artifact_status

    a = _register("status-a@ex.com")
    nb = _notebook(a)
    aid = _artifact_row(nb, kind="deck")

    assert a.get(f"/api/artifacts/{aid}").json()["status"] == "ready"  # DB default

    set_artifact_status(aid, "building")
    assert a.get(f"/api/artifacts/{aid}").json()["status"] == "building"
    nb_view = a.get(f"/api/notebooks/{nb}").json()
    assert nb_view["artifacts"][0]["status"] == "building"


def test_delete_artifact_removes_row_versions_and_exports_dir():
    a = _register("del-a@ex.com")
    b = _register("del-b@ex.com")
    aid = _artifact_row(_notebook(a), kind="deck")
    execute("INSERT INTO artifact_versions (artifact_id, version, payload_json, created) "
            "VALUES (?, 1, '{}', ?)", (aid, now()))
    export_dir = data_dir() / "exports" / aid
    export_dir.mkdir(parents=True, exist_ok=True)
    (export_dir / "deck-v1.pptx").write_bytes(b"fake-pptx")

    assert b.delete(f"/api/artifacts/{aid}").status_code == 404  # not the owner

    assert a.delete(f"/api/artifacts/{aid}").status_code == 200
    assert query_one("SELECT 1 FROM artifacts WHERE id = ?", (aid,)) is None
    assert query_one(
        "SELECT 1 FROM artifact_versions WHERE artifact_id = ?", (aid,)) is None
    assert not export_dir.exists()


# ---- suggested-questions route: owner-scoped wiring ------------------------

def test_suggested_questions_route_is_owner_scoped(monkeypatch):
    import docloom_studio.generate as generate_module

    async def fake_suggest(notebook_id, user_id):
        return ["Q1?", "Q2?", "Q3?"]

    monkeypatch.setattr(generate_module, "suggest_questions", fake_suggest, raising=False)

    a = _register("sugg-a@ex.com")
    b = _register("sugg-b@ex.com")
    nb = _notebook(a)

    assert TestClient(app).get(
        f"/api/notebooks/{nb}/suggested-questions").status_code == 401
    assert b.get(f"/api/notebooks/{nb}/suggested-questions").status_code == 404
    r = a.get(f"/api/notebooks/{nb}/suggested-questions")
    assert r.status_code == 200
    assert r.json() == {"questions": ["Q1?", "Q2?", "Q3?"]}


# ---- workspace_id backfill on upgrade from a pre-auth (v1) database -------

def test_workspace_id_backfill_on_upgrade(monkeypatch, tmp_path):
    """Simulate a pre-auth install: only the v1 schema exists, with a
    notebook that predates workspace_id entirely. Upgrading must adopt it
    into a (rescue) workspace instead of stranding it behind the join every
    auth-scoped query does."""
    monkeypatch.setenv("DOCLOOM_STUDIO_HOME", str(tmp_path))
    conn = sqlite3.connect(tmp_path / "studio.db")
    for stmt in db._split_statements(db.MIGRATIONS[0]):
        conn.execute(stmt)
    conn.execute(
        "INSERT INTO notebooks (id, name, created, updated) VALUES (?, ?, ?, ?)",
        ("legacy-nb", "Legacy notebook", db.now(), db.now()))
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()

    db.init_db()  # runs v2..latest against the legacy db

    row = db.query_one("SELECT workspace_id FROM notebooks WHERE id = ?", ("legacy-nb",))
    assert row["workspace_id"] is not None

    uid = db.owner_of_notebook("legacy-nb")  # the exact join every route uses
    assert uid is not None
    assert db.query_one("SELECT 1 FROM users WHERE id = ?", (uid,)) is not None


def test_workspace_id_backfill_prefers_existing_workspace(tmp_path):
    """Unit test of the backfill helper itself: if a workspace already exists
    by the time it runs, orphans join that one instead of minting a rescue
    user. (Not reachable through a real upgrade, since migration v2 is what
    creates the workspaces table in the first place, so this exercises the
    helper's other branch directly.)"""
    conn = sqlite3.connect(tmp_path / "unit.db")
    conn.row_factory = sqlite3.Row
    for stmt in db._split_statements(db.MIGRATIONS[0]):
        conn.execute(stmt)
    conn.execute(
        "INSERT INTO notebooks (id, name, created, updated) VALUES (?, ?, ?, ?)",
        ("orphan-nb", "Orphan", db.now(), db.now()))
    for stmt in db._split_statements(db.MIGRATIONS[1]):
        conn.execute(stmt)  # creates users/workspaces; workspace_id is NULL on orphan-nb

    uid = db.new_id()
    conn.execute("INSERT INTO users (id, email, password_hash, created) VALUES (?, ?, ?, ?)",
                (uid, "early@ex.com", "x", db.now()))
    wid = db.new_id()
    conn.execute("INSERT INTO workspaces (id, user_id, name, created) VALUES (?, ?, ?, ?)",
                (wid, uid, "Early workspace", db.now()))
    conn.commit()

    db._backfill_orphan_notebooks(conn, translate=False)
    conn.commit()

    row = conn.execute(
        "SELECT workspace_id FROM notebooks WHERE id = ?", ("orphan-nb",)).fetchone()
    assert row["workspace_id"] == wid  # joined the existing workspace
    count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    assert count == 1  # no rescue user was minted
    conn.close()
