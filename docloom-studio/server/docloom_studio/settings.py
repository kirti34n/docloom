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
        "kind": "ollama",  # llama-server | ollama | lmstudio | openai | anthropic | gemini
        "base_url": "http://localhost:11434",
        "api_key": "",
        "model": "qwen3.5:9b",
        "max_tokens": 32768,  # generous headroom so large docs/sections don't truncate
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
    "provider.image": {
        "kind": "gemini",  # cloud, paid: illustrative slide images only (Nano Banana)
        "base_url": "https://generativelanguage.googleapis.com",
        "api_key": "",
        "model": "gemini-2.5-flash-image",
        "enabled": False,
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


def _decrypt_stored(key: str, value: Any) -> Any:
    """Decrypt a loaded setting value, honoring both the nested secret fields
    and the bare-string secret keys in _SECRET_KEYS (e.g. research.tavily_key)."""
    from . import crypto

    if key in _SECRET_KEYS:
        return crypto.decrypt(value) if crypto.is_encrypted(value) else value
    return _decrypt_secrets(value)


def get_setting(key: str, user_id: str | None = None) -> Any:
    """A setting's value: the user's own override (if user_id given), else the
    global value, else the built-in default. Secret fields are decrypted."""
    from .db import query_one

    if user_id is not None:
        row = query_one(
            "SELECT value_json FROM user_settings WHERE user_id = ? AND key = ?",
            (user_id, key))
        if row is not None:
            return _decrypt_stored(key, json.loads(row["value_json"]))
    row = query_one("SELECT value_json FROM settings WHERE key = ?", (key,))
    if row is None:
        return DEFAULTS.get(key)
    return _decrypt_stored(key, json.loads(row["value_json"]))


def set_setting(key: str, value: Any, user_id: str | None = None) -> None:
    from . import crypto
    from .db import execute

    if key in _SECRET_KEYS and isinstance(value, str) and value and not crypto.is_encrypted(value):
        value = crypto.encrypt(value)
    else:
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
        merged[row["key"]] = _decrypt_stored(row["key"], json.loads(row["value_json"]))
    if user_id is not None:
        for row in query_all(
            "SELECT key, value_json FROM user_settings WHERE user_id = ?", (user_id,)
        ):
            merged[row["key"]] = _decrypt_stored(row["key"], json.loads(row["value_json"]))
    return merged


# ------------------------------------------------------------------ secrets

# Secret fields are never sent to the client in cleartext. The GET response
# replaces a stored secret with SECRET_MASK; on PUT, an incoming SECRET_MASK
# means "keep the stored value" so the round-trip doesn't wipe the real key.
SECRET_MASK = "__stored__"
_SECRET_FIELDS = ("api_key",)
# Bare-string top-level secret settings (stored/masked as the whole value,
# not as a nested field). Kept encrypted at rest and masked on the way out.
_SECRET_KEYS = ("research.tavily_key", "assets.pexels_key")


def redact_settings(merged: dict[str, Any]) -> dict[str, Any]:
    """Copy of `merged` with any populated secret field masked."""
    out: dict[str, Any] = {}
    for key, value in merged.items():
        if key in _SECRET_KEYS and value:
            value = SECRET_MASK
        elif isinstance(value, dict):
            value = dict(value)
            for field in _SECRET_FIELDS:
                if value.get(field):
                    value[field] = SECRET_MASK
        out[key] = value
    return out


def unmask_value(key: str, value: Any, user_id: str | None = None) -> Any:
    """Restore masked secrets from the user's stored value before persisting."""
    if not isinstance(value, dict):
        if key in _SECRET_KEYS and value == SECRET_MASK:
            return get_setting(key, user_id) or ""
        return value
    stored = get_setting(key, user_id)
    if not isinstance(stored, dict):
        stored = {}
    value = dict(value)
    for field in _SECRET_FIELDS:
        if value.get(field) == SECRET_MASK:
            value[field] = stored.get(field, "")
    return value
