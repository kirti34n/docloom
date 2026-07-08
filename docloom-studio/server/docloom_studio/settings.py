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
    "research.tavily_key": "",
    "assets.pexels_key": "",
    "deck.theme": "paper",
}


def get_setting(key: str) -> Any:
    from .db import query_one

    row = query_one("SELECT value_json FROM settings WHERE key = ?", (key,))
    if row is None:
        return DEFAULTS.get(key)
    return json.loads(row["value_json"])


def set_setting(key: str, value: Any) -> None:
    from .db import execute

    execute(
        "INSERT INTO settings (key, value_json) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
        (key, json.dumps(value)),
    )


def all_settings() -> dict[str, Any]:
    merged = dict(DEFAULTS)
    from .db import query_all

    for row in query_all("SELECT key, value_json FROM settings"):
        merged[row["key"]] = json.loads(row["value_json"])
    return merged
