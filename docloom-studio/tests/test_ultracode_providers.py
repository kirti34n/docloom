"""Regression tests for the studio-providers audit fixes:

Finding 1: irx._resolve_path/bake must never hand a client-authored literal
filesystem path to a renderer -- only asset://{id} resolves to a real file.
Finding 2: the asset:// lookup inside _resolve_path must be scoped to the
exporting user_id, matching the /api/assets/{id}/file route's own scoping.
Finding 3: bake()'s Artifact branch must not resolve another tenant's
render.png just because a client-supplied artifact_id names it.
Finding 4: providers.complete()'s OpenAI-compatible branch must turn a
refusal / empty-choices / content-filtered response into ProviderError
instead of returning None or raising IndexError.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-ultracode-providers-"))

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from docloom_studio import assets, providers as P  # noqa: E402
from docloom_studio.db import execute, init_db, new_id, now, query_one  # noqa: E402
from docloom_studio.irx import _resolve_path, bake, load_document  # noqa: E402
from docloom_studio.main import app  # noqa: E402
from docloom_studio.settings import data_dir  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.fixture(autouse=True)
def _db():
    init_db()
    for t in ("chat_messages", "artifact_versions", "artifacts", "sources",
              "notebooks", "assets", "user_settings", "auth_sessions",
              "workspaces", "users"):
        execute(f"DELETE FROM {t}")


def _register(email: str) -> tuple[TestClient, str]:
    c = TestClient(app)
    r = c.post("/api/auth/register", json={"email": email, "password": "password1"})
    return c, r.json()["id"]


def _notebook(user_id: str) -> str:
    wid = new_id()
    execute("INSERT INTO workspaces (id, user_id, name, created) VALUES (?, ?, ?, ?)",
            (wid, user_id, "ws", now()))
    nb = new_id()
    execute("INSERT INTO notebooks (id, name, workspace_id, created, updated) "
            "VALUES (?, ?, ?, ?, ?)", (nb, "nb", wid, now(), now()))
    return nb


def _artifact_row(notebook_id: str, kind: str = "diagram") -> str:
    aid = new_id()
    execute("INSERT INTO artifacts (id, notebook_id, kind, title, version, "
            "payload_json, created, updated) VALUES (?, ?, ?, 'T', 1, '{}', ?, ?)",
            (aid, notebook_id, kind, now(), now()))
    return aid


# ================================================================ Finding 1
# a client-authored literal filesystem path must never survive bake()


def test_bake_drops_literal_filesystem_path_on_image():
    _c, uid = _register("f1-a@ex.com")
    secret = str((data_dir() / "studio.db").resolve())
    payload = {"ir": {"title": "T", "blocks": [
        {"type": "image", "path": secret, "alt": "x"},
    ]}, "theme_name": "paper"}
    baked = bake(load_document(payload), uid)
    assert baked.blocks[0].path is None


def test_bake_drops_literal_filesystem_path_on_chart():
    _c, uid = _register("f1-b@ex.com")
    secret = str((data_dir() / "studio.db").resolve())
    payload = {"ir": {"title": "T", "blocks": [
        {"type": "chart", "chart": "bar", "path": secret},
    ]}, "theme_name": "paper"}
    baked = bake(load_document(payload), uid)
    assert baked.blocks[0].path is None


def test_export_html_does_not_leak_studio_db_bytes():
    c, uid = _register("f1-c@ex.com")
    nb = _notebook(uid)
    secret = str((data_dir() / "studio.db").resolve())
    aid = new_id()
    payload = {"ir": {"title": "T", "blocks": [
        {"type": "image", "path": secret, "alt": "x"},
    ]}, "theme_name": "paper", "brand_kit_id": None}
    execute("INSERT INTO artifacts (id, notebook_id, kind, title, version, "
            "payload_json, created, updated) VALUES (?, ?, 'deck', 'T', 1, ?, ?, ?)",
            (aid, nb, json.dumps(payload), now(), now()))
    r = c.post(f"/api/artifacts/{aid}/export", json={"format": "html"})
    assert r.status_code == 200
    filename = r.json()["filename"]
    out = data_dir() / "exports" / aid / filename
    raw = out.read_bytes()
    # SQLite's magic header, if the DB bytes leaked into the export, would
    # appear verbatim (html embeds raw file bytes as base64, but the literal
    # DB path string itself must not appear as a src/href either).
    assert secret.encode() not in raw
    assert b"SQLite format 3" not in raw


# ================================================================ Finding 2
# asset:// resolution must be scoped to the exporting user


def test_resolve_path_is_user_scoped():
    _a, a_uid = _register("f2-a@ex.com")
    _b, b_uid = _register("f2-b@ex.com")
    aid = assets.save_generated_image(a_uid, PNG, prompt="a private mountain")

    resolved = _resolve_path(f"asset://{aid}", a_uid)
    assert resolved is not None
    assert os.path.isfile(resolved)

    assert _resolve_path(f"asset://{aid}", b_uid) is None


def test_bake_does_not_resolve_another_tenants_asset():
    _a, a_uid = _register("f2-c@ex.com")
    _b, b_uid = _register("f2-d@ex.com")
    aid = assets.save_generated_image(a_uid, PNG, prompt="a private fox")

    payload = {"ir": {"title": "T", "blocks": [
        {"type": "image", "path": f"asset://{aid}", "alt": "x"},
    ]}, "theme_name": "paper"}
    baked = bake(load_document(payload), b_uid)
    assert baked.blocks[0].path is None

    baked_owner = bake(load_document(payload), a_uid)
    assert baked_owner.blocks[0].path is not None
    assert os.path.isfile(baked_owner.blocks[0].path)


# ================================================================ Finding 3
# artifact-render resolution must be scoped to the embedding artifact's owner


def test_bake_artifact_block_does_not_resolve_foreign_owners_render():
    _victim_c, victim_uid = _register("f3-victim@ex.com")
    _attacker_c, attacker_uid = _register("f3-attacker@ex.com")
    vic_nb = _notebook(victim_uid)
    vic_art = _artifact_row(vic_nb)
    vic_dir = data_dir() / "artifacts" / vic_art
    vic_dir.mkdir(parents=True, exist_ok=True)
    (vic_dir / "render.png").write_bytes(b"VICTIM_RENDER")

    payload = {"ir": {"title": "T", "blocks": [
        {"type": "artifact", "kind": "diagram", "artifact_id": vic_art},
    ]}, "theme_name": "paper"}
    baked = bake(load_document(payload), attacker_uid)
    assert baked.blocks[0].path is None


def test_bake_artifact_block_resolves_owned_render():
    _c, uid = _register("f3-owner@ex.com")
    nb = _notebook(uid)
    art = _artifact_row(nb)
    art_dir = data_dir() / "artifacts" / art
    art_dir.mkdir(parents=True, exist_ok=True)
    (art_dir / "render.png").write_bytes(b"OWN_RENDER")

    payload = {"ir": {"title": "T", "blocks": [
        {"type": "artifact", "kind": "diagram", "artifact_id": art},
    ]}, "theme_name": "paper"}
    baked = bake(load_document(payload), uid)
    assert baked.blocks[0].path is not None
    assert baked.blocks[0].path.endswith("render.png")


# ================================================================ Finding 4
# OpenAI-compatible complete(): refusal / empty choices / content_filter

_RealAsyncClient = httpx.AsyncClient  # captured before any monkeypatching


def _mock_client(handler):
    def factory(*args, **kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(handler))
        return _RealAsyncClient(*args, **kwargs)
    return factory


def _json_response(payload: dict, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


_SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}},
           "required": ["title"]}


def test_complete_openai_refusal_raises_provider_error(monkeypatch):
    def handler(request):
        return _json_response({"choices": [
            {"finish_reason": "stop",
             "message": {"content": None, "refusal": "I can't help with that."}}]})

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = P.ProviderConfig(kind="openai", model="gpt-x", api_key="k",
                           base_url="https://x.example")
    with pytest.raises(P.ProviderError):
        asyncio.run(P.complete(cfg, [{"role": "user", "content": "hi"}], schema=_SCHEMA))


def test_complete_openai_empty_choices_raises_provider_error(monkeypatch):
    def handler(request):
        return _json_response({"choices": []})

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = P.ProviderConfig(kind="openai", model="gpt-x", api_key="k",
                           base_url="https://x.example")
    with pytest.raises(P.ProviderError):
        asyncio.run(P.complete(cfg, [{"role": "user", "content": "hi"}], schema=_SCHEMA))


def test_complete_openai_content_filter_raises_provider_error(monkeypatch):
    def handler(request):
        return _json_response({"choices": [
            {"finish_reason": "content_filter", "message": {"content": None}}]})

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = P.ProviderConfig(kind="openai", model="gpt-x", api_key="k",
                           base_url="https://x.example")
    with pytest.raises(P.ProviderError):
        asyncio.run(P.complete(cfg, [{"role": "user", "content": "hi"}], schema=_SCHEMA))


def test_complete_openai_generate_validated_skips_refused_unit_not_typeerror(monkeypatch):
    def handler(request):
        return _json_response({"choices": [
            {"finish_reason": "stop",
             "message": {"content": None, "refusal": "no"}}]})

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = P.ProviderConfig(kind="openai", model="gpt-x", api_key="k",
                           base_url="https://x.example")
    with pytest.raises(P.ProviderError):
        asyncio.run(P.generate_validated(
            cfg, [{"role": "user", "content": "hi"}], _SCHEMA, parse=lambda s: s,
            max_rounds=1))


def test_complete_openai_normal_response_still_returns_content(monkeypatch):
    def handler(request):
        return _json_response({"choices": [
            {"finish_reason": "stop",
             "message": {"content": '{"title":"ok"}'}}]})

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = P.ProviderConfig(kind="openai", model="gpt-x", api_key="k",
                           base_url="https://x.example")
    out = asyncio.run(P.complete(cfg, [{"role": "user", "content": "hi"}], schema=_SCHEMA))
    assert out == '{"title":"ok"}'


def test_complete_openai_empty_string_content_still_returned(monkeypatch):
    # an empty (but non-null) content must still flow through unchanged --
    # only content is None is treated as an abnormal/no-content response.
    def handler(request):
        return _json_response({"choices": [
            {"finish_reason": "stop", "message": {"content": ""}}]})

    monkeypatch.setattr(P.httpx, "AsyncClient", _mock_client(handler))
    cfg = P.ProviderConfig(kind="openai", model="gpt-x", api_key="k",
                           base_url="https://x.example")
    out = asyncio.run(P.complete(cfg, [{"role": "user", "content": "hi"}], schema=_SCHEMA))
    assert out == ""
