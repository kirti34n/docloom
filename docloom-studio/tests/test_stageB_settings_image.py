"""Stage B regression: the provider.image settings default (Nano Banana,
Gemini image generation) exists, is disabled by default, and its nested
api_key round-trips through the same encrypt/redact/unmask machinery as
every other provider.* setting, with no change needed to that machinery."""

import json
import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-stageB-settings-image-"))

import pytest  # noqa: E402

from docloom_studio.db import execute, init_db, query_one  # noqa: E402
from docloom_studio.settings import (  # noqa: E402
    DEFAULTS, SECRET_MASK, get_setting, redact_settings, set_setting, unmask_value,
)


@pytest.fixture(autouse=True)
def _db():
    init_db()
    execute("DELETE FROM settings")


def test_defaults_declare_image_provider_disabled_by_default():
    image = DEFAULTS["provider.image"]
    assert image["enabled"] is False
    assert image["kind"] == "gemini"
    assert image["model"] == "gemini-2.5-flash-image"
    assert image["base_url"] == "https://generativelanguage.googleapis.com"
    assert image["api_key"] == ""


def test_get_setting_default_round_trips_the_full_shape():
    # No row saved yet (fresh install): get_setting falls back to DEFAULTS,
    # and the whole dict, including the disabled flag, must come through.
    cfg = get_setting("provider.image")
    assert cfg == DEFAULTS["provider.image"]
    assert cfg["enabled"] is False


def test_api_key_encrypts_at_rest_and_decrypts_on_read():
    set_setting("provider.image",
                {"kind": "gemini", "base_url": "https://generativelanguage.googleapis.com",
                 "api_key": "AIza-REAL-KEY", "model": "gemini-2.5-flash-image",
                 "enabled": True})

    # raw DB row must not contain the cleartext key
    raw = query_one("SELECT value_json FROM settings WHERE key = 'provider.image'")
    stored = json.loads(raw["value_json"])
    assert stored["api_key"].startswith("enc:")
    assert "AIza-REAL-KEY" not in raw["value_json"]

    # but get_setting decrypts it transparently
    cfg = get_setting("provider.image")
    assert cfg["api_key"] == "AIza-REAL-KEY"
    assert cfg["enabled"] is True


def test_mask_roundtrip_keeps_encrypted_key_across_an_enable_toggle():
    set_setting("provider.image",
                {"kind": "gemini", "base_url": "https://generativelanguage.googleapis.com",
                 "api_key": "AIza-REAL-KEY", "model": "gemini-2.5-flash-image",
                 "enabled": True})

    # client only ever sees the mask, never the cleartext key
    red = redact_settings({"provider.image": get_setting("provider.image")})
    assert red["provider.image"]["api_key"] == SECRET_MASK

    # client flips the enable toggle and sends the mask back unchanged:
    # the real key must survive, not get wiped by the mask sentinel
    incoming = {"kind": "gemini", "base_url": "https://generativelanguage.googleapis.com",
                "api_key": SECRET_MASK, "model": "gemini-2.5-flash-image", "enabled": False}
    set_setting("provider.image", unmask_value("provider.image", incoming))

    cfg = get_setting("provider.image")
    assert cfg["api_key"] == "AIza-REAL-KEY"
    assert cfg["enabled"] is False
