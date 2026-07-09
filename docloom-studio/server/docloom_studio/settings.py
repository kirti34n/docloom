"""App data directory and settings storage."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

APP_NAME = "docloom-studio"


def data_dir() -> Path:
    root = os.environ.get("DOCLOOM_STUDIO_HOME")
    if root:
        base = Path(root)
    else:
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    for sub in ("sources", "artifacts", "assets", "exports", "cache/web"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    return base


DEFAULTS: dict[str, Any] = {
    "provider.generation": {
        "kind": "ollama",  # llama-server | ollama | lmstudio | openai | anthropic
        "base_url": "http://localhost:11434",
        "api_key": "",
        "model": "qwen3.5:9b",
    },
    "provider.embeddings": {
        "kind": "ollama",
        "base_url": "http://localhost:11434",
        "api_key": "",
        "model": "nomic-embed-text",
    },
    "provider.tts": {
        "kind": "kokoro",       # local podcast voices (pip install kokoro soundfile)
        "lang": "a",            # 'a' = American English
        "voice_a": "af_heart",  # host
        "voice_b": "am_michael",  # guest
    },
    "research.tavily_key": "",
    "assets.pexels_key": "",
    "deck.theme": "paper",
}


def _decrypt_secrets(value: Any) -> Any:
    """Decrypt any encrypted secret fields in a loaded setting value."""
    from . import crypto

    if isinstance(value, dict):
        out = dict(value)
        for field in _SECRET_FIELDS:
            v = out.get(field)
            if crypto.is_encrypted(v):
                out[field] = crypto.decrypt(v)
        return out
    return value


def _encrypt_secrets(value: Any) -> Any:
    """Encrypt non-empty secret fields before a setting value is persisted."""
    from . import crypto

    if isinstance(value, dict):
        out = dict(value)
        for field in _SECRET_FIELDS:
            v = out.get(field)
            if v and not crypto.is_encrypted(v):
                out[field] = crypto.encrypt(v)
        return out
    return value


def get_setting(key: str, user_id: str | None = None) -> Any:
    """A setting's value: the user's own override (if user_id given), else the
    global value, else the built-in default. Secret fields are decrypted."""
    from .db import query_one

    if user_id is not None:
        row = query_one(
            "SELECT value_json FROM user_settings WHERE user_id = ? AND key = ?",
            (user_id, key))
        if row is not None:
            return _decrypt_secrets(json.loads(row["value_json"]))
    row = query_one("SELECT value_json FROM settings WHERE key = ?", (key,))
    if row is None:
        return DEFAULTS.get(key)
    return _decrypt_secrets(json.loads(row["value_json"]))


def set_setting(key: str, value: Any, user_id: str | None = None) -> None:
    from .db import execute

    value = _encrypt_secrets(value)
    if user_id is not None:
        execute(
            "INSERT INTO user_settings (user_id, key, value_json) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET value_json = excluded.value_json",
            (user_id, key, json.dumps(value)),
        )
    else:
        execute(
            "INSERT INTO settings (key, value_json) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
            (key, json.dumps(value)),
        )


def all_settings(user_id: str | None = None) -> dict[str, Any]:
    merged = dict(DEFAULTS)
    from .db import query_all

    for row in query_all("SELECT key, value_json FROM settings"):
        merged[row["key"]] = _decrypt_secrets(json.loads(row["value_json"]))
    if user_id is not None:
        for row in query_all(
            "SELECT key, value_json FROM user_settings WHERE user_id = ?", (user_id,)
        ):
            merged[row["key"]] = _decrypt_secrets(json.loads(row["value_json"]))
    return merged


# ------------------------------------------------------------------ secrets

# Secret fields are never sent to the client in cleartext. The GET response
# replaces a stored secret with SECRET_MASK; on PUT, an incoming SECRET_MASK
# means "keep the stored value" so the round-trip doesn't wipe the real key.
SECRET_MASK = "__stored__"
_SECRET_FIELDS = ("api_key",)


def redact_settings(merged: dict[str, Any]) -> dict[str, Any]:
    """Copy of `merged` with any populated secret field masked."""
    out: dict[str, Any] = {}
    for key, value in merged.items():
        if isinstance(value, dict):
            value = dict(value)
            for field in _SECRET_FIELDS:
                if value.get(field):
                    value[field] = SECRET_MASK
        out[key] = value
    return out


def unmask_value(key: str, value: Any, user_id: str | None = None) -> Any:
    """Restore masked secrets from the user's stored value before persisting."""
    if not isinstance(value, dict):
        return value
    stored = get_setting(key, user_id) or {}
    value = dict(value)
    for field in _SECRET_FIELDS:
        if value.get(field) == SECRET_MASK:
            value[field] = (stored or {}).get(field, "")
    return value
