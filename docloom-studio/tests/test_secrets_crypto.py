"""Secrets are encrypted at rest, decrypt transparently on read, and the
masking round-trip still works on top of encryption."""

import json
import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-sec-"))

import pytest  # noqa: E402

from docloom_studio import crypto, settings as S  # noqa: E402
from docloom_studio.db import execute, init_db, query_one  # noqa: E402


@pytest.fixture(autouse=True)
def _db():
    init_db()
    execute("DELETE FROM settings")


def test_crypto_roundtrip():
    assert crypto.available()
    ct = crypto.encrypt("sk-secret")
    assert ct.startswith("enc:") and "sk-secret" not in ct
    assert crypto.decrypt(ct) == "sk-secret"


def test_empty_and_legacy_passthrough():
    assert crypto.encrypt("") == ""
    assert crypto.decrypt("plaintext-legacy") == "plaintext-legacy"


def test_api_key_encrypted_in_db_but_readable():
    S.set_setting("provider.generation",
                  {"kind": "openai", "base_url": "x", "api_key": "sk-REAL", "model": "gpt"})
    # raw DB row must not contain the cleartext key
    raw = query_one("SELECT value_json FROM settings WHERE key = 'provider.generation'")
    stored = json.loads(raw["value_json"])
    assert stored["api_key"].startswith("enc:")
    assert "sk-REAL" not in raw["value_json"]
    # but get_setting decrypts it
    assert S.get_setting("provider.generation")["api_key"] == "sk-REAL"


def test_mask_roundtrip_keeps_encrypted_key():
    S.set_setting("provider.generation",
                  {"kind": "openai", "base_url": "x", "api_key": "sk-REAL", "model": "gpt"})
    # client sees the mask
    red = S.redact_settings(S.all_settings())
    assert red["provider.generation"]["api_key"] == S.SECRET_MASK
    # client edits the model, sends the mask back → real key preserved
    incoming = {"kind": "openai", "base_url": "x",
                "api_key": S.SECRET_MASK, "model": "gpt-4o"}
    S.set_setting("provider.generation", S.unmask_value("provider.generation", incoming))
    cfg = S.get_setting("provider.generation")
    assert cfg["api_key"] == "sk-REAL" and cfg["model"] == "gpt-4o"
