"""Multi-tenant auth foundation: register/login/logout, session cookies,
per-user workspaces, and that protected routes reject the unauthenticated."""

import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-auth-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from docloom_studio import auth  # noqa: E402
from docloom_studio.db import execute, init_db  # noqa: E402
from docloom_studio.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def _db():
    init_db()
    for t in ("chat_messages", "artifact_versions", "artifacts", "sources",
              "notebooks", "user_settings", "auth_sessions", "workspaces", "users"):
        execute(f"DELETE FROM {t}")


@pytest.fixture
def client():
    return TestClient(app)  # keeps a cookie jar across calls


def test_register_login_me_and_workspaces(client):
    r = client.post("/api/auth/register",
                    json={"email": "A@Ex.com", "password": "hunter2!"})
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "a@ex.com"  # normalized

    me = client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["email"] == "a@ex.com"

    ws = client.get("/api/workspaces").json()
    assert len(ws) == 1 and ws[0]["name"] == "My workspace"  # default workspace

    client.post("/api/workspaces", json={"name": "Research"})
    names = {w["name"] for w in client.get("/api/workspaces").json()}
    assert names == {"My workspace", "Research"}

    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401  # cookie cleared

    assert client.post("/api/auth/login",
                       json={"email": "a@ex.com", "password": "hunter2!"}).status_code == 200
    assert client.get("/api/auth/me").status_code == 200


def test_wrong_password_rejected(client):
    client.post("/api/auth/register",
                json={"email": "b@ex.com", "password": "correct-horse"})
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login",
                       json={"email": "b@ex.com", "password": "nope"}).status_code == 401


def test_duplicate_email_rejected(client):
    client.post("/api/auth/register", json={"email": "c@ex.com", "password": "password1"})
    r = client.post("/api/auth/register", json={"email": "c@ex.com", "password": "password2"})
    assert r.status_code == 409


def test_short_password_rejected(client):
    r = client.post("/api/auth/register", json={"email": "d@ex.com", "password": "short"})
    assert r.status_code == 400


def test_protected_routes_require_auth(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/workspaces").status_code == 401
    assert client.post("/api/workspaces", json={"name": "x"}).status_code == 401


def test_workspaces_are_per_user():
    c1, c2 = TestClient(app), TestClient(app)
    c1.post("/api/auth/register", json={"email": "u1@ex.com", "password": "password1"})
    c2.post("/api/auth/register", json={"email": "u2@ex.com", "password": "password2"})
    c1.post("/api/workspaces", json={"name": "u1-only"})
    u2_names = {w["name"] for w in c2.get("/api/workspaces").json()}
    assert "u1-only" not in u2_names  # isolation


def test_password_hash_roundtrip():
    h = auth.hash_password("s3cret-pass")
    assert h.startswith("scrypt$") and "s3cret-pass" not in h
    assert auth.verify_password("s3cret-pass", h)
    assert not auth.verify_password("wrong", h)


def test_notebooks_scoped_to_workspace(client):
    client.post("/api/auth/register", json={"email": "n@ex.com", "password": "password1"})
    wid = client.get("/api/workspaces").json()[0]["id"]
    nb = client.post("/api/notebooks",
                     json={"name": "Deck plan", "workspace_id": wid}).json()
    assert nb["workspace_id"] == wid
    assert [n["id"] for n in client.get(f"/api/notebooks?workspace_id={wid}").json()] == [nb["id"]]
    assert client.get(f"/api/notebooks/{nb['id']}").status_code == 200

    anon = TestClient(app)  # no session
    assert anon.get(f"/api/notebooks?workspace_id={wid}").status_code == 401
    assert anon.get(f"/api/notebooks/{nb['id']}").status_code == 401


def test_cross_user_notebook_isolation():
    a, b = TestClient(app), TestClient(app)
    a.post("/api/auth/register", json={"email": "a2@ex.com", "password": "password1"})
    b.post("/api/auth/register", json={"email": "b2@ex.com", "password": "password2"})
    wa = a.get("/api/workspaces").json()[0]["id"]
    nb = a.post("/api/notebooks", json={"name": "secret", "workspace_id": wa}).json()

    # B cannot read A's notebook, list A's workspace, or add sources to it
    assert b.get(f"/api/notebooks/{nb['id']}").status_code == 404
    assert b.get(f"/api/notebooks?workspace_id={wa}").status_code == 404
    assert b.post(f"/api/notebooks/{nb['id']}/sources/text",
                  json={"title": "t", "text": "x"}).status_code == 404


def test_artifact_isolation_and_auth():
    from docloom_studio.db import execute, new_id, now

    a, b = TestClient(app), TestClient(app)
    a.post("/api/auth/register", json={"email": "art-a@ex.com", "password": "password1"})
    b.post("/api/auth/register", json={"email": "art-b@ex.com", "password": "password2"})
    wa = a.get("/api/workspaces").json()[0]["id"]
    nb = a.post("/api/notebooks", json={"name": "n", "workspace_id": wa}).json()
    aid = new_id()
    execute("INSERT INTO artifacts (id, notebook_id, kind, title, version, "
            "payload_json, created, updated) VALUES (?, ?, 'deck', 'T', 1, '{}', ?, ?)",
            (aid, nb["id"], now(), now()))

    assert a.get(f"/api/artifacts/{aid}").status_code == 200          # owner
    assert b.get(f"/api/artifacts/{aid}").status_code == 404          # other tenant
    assert TestClient(app).get(f"/api/artifacts/{aid}").status_code == 401  # anon


def test_asset_and_settings_routes_require_auth():
    anon = TestClient(app)
    assert anon.get("/api/assets").status_code == 401
    assert anon.get("/api/brand-kit").status_code == 401
    assert anon.get("/api/settings").status_code == 401
    assert anon.get("/api/providers/models").status_code == 401


def test_provider_settings_are_per_user():
    a, b = TestClient(app), TestClient(app)
    a.post("/api/auth/register", json={"email": "cfg-a@ex.com", "password": "password1"})
    b.post("/api/auth/register", json={"email": "cfg-b@ex.com", "password": "password2"})

    # A configures their own OpenAI key
    a.put("/api/settings", json={"values": {"provider.generation": {
        "kind": "openai", "base_url": "x", "api_key": "sk-A-SECRET", "model": "gpt"}}})

    # A sees their config (key masked); B is completely unaffected
    ga = a.get("/api/settings").json()["provider.generation"]
    assert ga["kind"] == "openai" and ga["api_key"] == "__stored__"
    gb = b.get("/api/settings").json()["provider.generation"]
    assert gb["kind"] == "ollama" and gb["api_key"] == ""  # B's default, not A's key

    # brand kit is per-user too
    a.put("/api/brand-kit", json={"accent": "#ff0066"})
    assert a.get("/api/brand-kit").json().get("accent") == "#ff0066"
    assert b.get("/api/brand-kit").json().get("accent") is None


def test_chat_history_and_source_content():
    import json as _json

    from docloom_studio.chat import _save_message
    from docloom_studio.db import execute, new_id, now
    from docloom_studio.ingest import _source_dir

    a, b = TestClient(app), TestClient(app)
    a.post("/api/auth/register", json={"email": "h-a@ex.com", "password": "password1"})
    b.post("/api/auth/register", json={"email": "h-b@ex.com", "password": "password2"})
    wa = a.get("/api/workspaces").json()[0]["id"]
    nb = a.post("/api/notebooks", json={"name": "n", "workspace_id": wa}).json()

    # persisted conversation is returned in order, with evidence
    _save_message(nb["id"], "user", "what is X?", [])
    _save_message(nb["id"], "assistant", "X is Y [1].",
                  [{"n": 1, "source_id": "s1", "text": "grounding"}])
    hist = a.get(f"/api/notebooks/{nb['id']}/messages").json()
    assert [m["role"] for m in hist] == ["user", "assistant"]
    assert hist[1]["evidence"][0]["source_id"] == "s1"
    assert b.get(f"/api/notebooks/{nb['id']}/messages").status_code == 404  # other tenant

    # source content for the reader
    sid = new_id()
    execute("INSERT INTO sources (id, notebook_id, kind, title, status, "
            "context_mode, meta_json, created) VALUES (?, ?, 'text', 'Doc', 'ready', "
            "'full', '{}', ?)", (sid, nb["id"], now()))
    (_source_dir(sid) / "chunks.jsonl").write_text(
        _json.dumps({"chunk_ix": 0, "page": 1, "section": "Intro",
                     "text": "hello world"}) + "\n", encoding="utf-8")
    content = a.get(f"/api/sources/{sid}/content").json()
    assert content["title"] == "Doc"
    assert content["chunks"][0]["text"] == "hello world"
    assert b.get(f"/api/sources/{sid}/content").status_code == 404          # other tenant
    assert TestClient(app).get(f"/api/sources/{sid}/content").status_code == 401  # anon
