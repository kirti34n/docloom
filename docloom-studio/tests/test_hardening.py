"""Phase-0 hardening: provider API keys are never returned to the client in
cleartext, the masked round-trip preserves the stored key, and the SPA file
server refuses path traversal."""

import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-hard-"))

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from docloom_studio import settings as S  # noqa: E402
from docloom_studio.db import execute, init_db  # noqa: E402
from docloom_studio.main import _safe_spa_file  # noqa: E402


@pytest.fixture(autouse=True)
def _db():
    init_db()
    execute("DELETE FROM settings")


def test_get_masks_api_key():
    S.set_setting("provider.generation",
                  {"kind": "openai", "base_url": "x", "api_key": "sk-REAL", "model": "gpt"})
    red = S.redact_settings(S.all_settings())
    assert red["provider.generation"]["api_key"] == S.SECRET_MASK


def test_put_preserves_key_on_mask():
    S.set_setting("provider.generation",
                  {"kind": "openai", "base_url": "x", "api_key": "sk-REAL", "model": "gpt"})
    incoming = {"kind": "openai", "base_url": "x",
                "api_key": S.SECRET_MASK, "model": "gpt-4o"}  # user changed model only
    S.set_setting("provider.generation", S.unmask_value("provider.generation", incoming))
    cfg = S.get_setting("provider.generation")
    assert cfg["api_key"] == "sk-REAL"   # real key kept
    assert cfg["model"] == "gpt-4o"      # edit applied


def test_put_stores_new_key():
    S.set_setting("provider.generation", S.unmask_value(
        "provider.generation",
        {"kind": "openai", "base_url": "x", "api_key": "sk-NEW", "model": "gpt-4o"}))
    assert S.get_setting("provider.generation")["api_key"] == "sk-NEW"


def test_empty_key_not_masked():
    out = S.redact_settings({"provider.embeddings": {"api_key": ""}})
    assert out["provider.embeddings"]["api_key"] == ""


def test_spa_blocks_traversal(tmp_path):
    (tmp_path / "index.html").write_text("ok")
    (tmp_path / "app.js").write_text("js")
    (tmp_path.parent / "SECRET.txt").write_text("leak")
    assert _safe_spa_file(tmp_path, "app.js") is not None
    assert _safe_spa_file(tmp_path, "../SECRET.txt") is None
    assert _safe_spa_file(tmp_path, "../../etc/passwd") is None
    assert _safe_spa_file(tmp_path, "api/x") is None
    assert _safe_spa_file(tmp_path, "") is None
