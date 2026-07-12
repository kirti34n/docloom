"""Re-audit regression: the bare-string top-level secret settings
(research.tavily_key, assets.pexels_key) get the same protection as the nested
provider.*.api_key secrets, i.e. encrypted at rest and masked on the way out.
Before the fix they were stored as plaintext and returned to the client
unmasked, bypassing both documented controls."""

import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-reaudit-"))

import json  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip("cryptography")  # encryption-at-rest needs Fernet

from docloom_studio.db import execute, init_db, new_id, now, query_one  # noqa: E402
from docloom_studio.settings import (  # noqa: E402
    SECRET_MASK, all_settings, get_setting, redact_settings, set_setting,
    unmask_value,
)


@pytest.fixture(autouse=True)
def _db():
    init_db()
    for t in ("user_settings", "settings", "users"):
        execute(f"DELETE FROM {t}")


def _user() -> str:
    uid = new_id()
    execute("INSERT INTO users (id, email, password_hash, created) VALUES (?, ?, ?, ?)",
            (uid, "reaudit@ex.com", "x", now()))
    return uid


def test_bare_string_secret_is_encrypted_masked_and_round_trips():
    uid = _user()
    set_setting("research.tavily_key", "sk-secret", uid)

    # (a) the raw DB value must not hold the secret in plaintext
    row = query_one(
        "SELECT value_json FROM user_settings WHERE user_id = ? AND key = ?",
        (uid, "research.tavily_key"))
    assert row is not None
    assert "sk-secret" not in row["value_json"]
    assert json.loads(row["value_json"]).startswith("enc:")  # ciphertext at rest

    # (b) reading it back returns the real key (decryption is transparent)
    assert get_setting("research.tavily_key", uid) == "sk-secret"

    # (c) the client-facing view masks it, never the plaintext
    redacted = redact_settings(all_settings(uid))
    assert redacted["research.tavily_key"] == SECRET_MASK
    assert "sk-secret" not in json.dumps(redacted)

    # a client PUT that echoes the mask back must keep the stored key, not wipe it
    assert unmask_value("research.tavily_key", SECRET_MASK, uid) == "sk-secret"
