"""Regression tests for two provider/settings crash paths surfaced by audit:

1. provider_for() was constructed outside the try/except of
   /api/providers/models and /api/providers/test, so an unknown slot
   (get_setting returns None) or a corrupted non-dict provider.* setting
   raised a TypeError before the endpoint's own graceful handler ever ran,
   producing an HTTP 500 instead of the documented {"ok"/"models", "error"}
   body.

2. unmask_value() assumed the stored value for a nested-secret key was a
   dict; a persisted truthy non-dict (e.g. the string "hello" stored for
   provider.generation) made `(stored or {}).get(field)` call `.get` on a
   plain string and raise AttributeError, surfacing as a 500 on the settings
   PUT recovery path."""

import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-ultracode-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from docloom_studio.db import execute, init_db  # noqa: E402
from docloom_studio.main import app  # noqa: E402
from docloom_studio.settings import SECRET_MASK, set_setting, unmask_value  # noqa: E402


@pytest.fixture(autouse=True)
def _db():
    init_db()
    for t in ("user_settings", "settings", "auth_sessions", "workspaces", "users"):
        execute(f"DELETE FROM {t}")


def _register(email: str) -> TestClient:
    c = TestClient(app, raise_server_exceptions=False)
    c.post("/api/auth/register", json={"email": email, "password": "password1"})
    return c


# ---- Finding 1: provider_for() reachable crashes must become 200 error bodies

def test_models_unknown_slot_returns_graceful_error_not_500():
    c = _register("slot@ex.com")
    r = c.get("/api/providers/models", params={"slot": "bogus"})
    assert r.status_code == 200  # was 500 before the fix
    body = r.json()
    assert body["models"] == []
    assert "error" in body


def test_provider_test_survives_corrupted_generation_setting():
    c = _register("corrupt@ex.com")
    put = c.put("/api/settings", json={"values": {"provider.generation": "hello"}})
    assert put.status_code == 200
    r = c.post("/api/providers/test")
    assert r.status_code == 200  # was 500 before the fix
    body = r.json()
    assert body["ok"] is False
    assert "error" in body


def test_models_valid_slot_with_bad_field_type_returns_graceful_error_not_500():
    c = _register("badfield@ex.com")
    put = c.put(
        "/api/settings",
        json={"values": {"provider.generation": {
            "kind": "openai", "max_tokens": "not-a-number",
        }}},
    )
    assert put.status_code == 200
    r = c.get("/api/providers/models", params={"slot": "generation"})
    assert r.status_code == 200  # was 500 before the fix
    body = r.json()
    assert body["models"] == []
    assert "error" in body


# ---- Finding 2: unmask_value must tolerate a corrupted non-dict stored value

def test_unmask_tolerates_corrupted_non_dict_stored_value():
    uid_client = _register("unmask@ex.com")
    uid = uid_client.get("/api/auth/me").json()["id"]

    set_setting("provider.generation", "hello", uid)  # malformed non-dict persisted

    out = unmask_value(
        "provider.generation",
        {"kind": "openai", "api_key": SECRET_MASK},
        uid,
    )
    assert out["api_key"] == ""  # no recoverable secret -> empty, not a crash
    assert out["kind"] == "openai"


def test_settings_put_survives_corrupted_stored_value_then_recovers():
    c = _register("recover@ex.com")
    first = c.put("/api/settings", json={"values": {"provider.generation": "hello"}})
    assert first.status_code == 200

    second = c.put(
        "/api/settings",
        json={"values": {"provider.generation": {
            "kind": "openai", "api_key": SECRET_MASK,
        }}},
    )
    assert second.status_code == 200  # was 500 before the fix
    assert second.json()["provider.generation"]["api_key"] == ""
