"""Stage A regression: the provider.generation settings default declares the
16384 generation token cap, and it survives the get_setting -> ProviderConfig
round trip both for a fresh install (no row saved yet) and for a config saved
before this field existed (ProviderConfig's own field default must supply the
cap so generate_validated never sees a config missing max_tokens)."""

import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-stageA-settings-"))

import pytest  # noqa: E402

from docloom_studio.db import execute, init_db  # noqa: E402
from docloom_studio.providers import ProviderConfig  # noqa: E402
from docloom_studio.settings import DEFAULTS, get_setting, set_setting  # noqa: E402


@pytest.fixture(autouse=True)
def _db():
    init_db()
    execute("DELETE FROM settings")


def test_defaults_declare_the_16384_token_cap():
    assert DEFAULTS["provider.generation"]["max_tokens"] == 16384


def test_get_setting_default_round_trips_into_provider_config():
    # No row saved yet: get_setting falls back to DEFAULTS, which now carries
    # max_tokens, and ProviderConfig must accept and keep it.
    cfg = ProviderConfig(**get_setting("provider.generation"))
    assert cfg.max_tokens == 16384


def test_old_saved_config_without_max_tokens_still_gets_the_default():
    # Simulate a config saved before this field existed: no "max_tokens" key
    # at all, the exact shape of every pre-existing user's stored settings.
    old_shape = {"kind": "ollama", "base_url": "http://localhost:11434",
                 "api_key": "", "model": "qwen3.5:9b"}
    set_setting("provider.generation", old_shape)

    stored = get_setting("provider.generation")
    assert "max_tokens" not in stored  # confirms this exercises the back-compat path, not DEFAULTS

    cfg = ProviderConfig(**stored)
    assert cfg.max_tokens == 16384
