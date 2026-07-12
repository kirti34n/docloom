"""Path-traversal regression for studio_theme (CWE-22): the user-controlled
theme_name must stay confined to THEME_DIR. A name with ../ segments or an
absolute path resolves outside THEME_DIR and falls back to the paper theme
instead of reading an arbitrary .json off the sandbox, while a genuine theme
name still loads as itself."""

import json
import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-irx-"))

from pathlib import Path  # noqa: E402

from docloom_studio.irx import studio_theme  # noqa: E402


def test_paper_theme_loads():
    theme = studio_theme("paper")
    assert theme["name"] == "paper"
    assert theme["primary"] == "#1F3D63"


def test_real_theme_name_still_loads():
    # A genuine theme in THEME_DIR is served as itself, not the fallback.
    theme = studio_theme("aurora")
    assert theme["name"] == "aurora"
    assert theme != studio_theme("paper")


def test_relative_traversal_falls_back_to_paper():
    # ../ segments resolve outside THEME_DIR, so the name is rejected and the
    # paper theme is returned verbatim (same tokens as studio_theme('paper')).
    assert studio_theme("../../../../../../etc/passwd") == studio_theme("paper")


def test_absolute_path_name_does_not_read_outside_file():
    # Plant a .json outside THEME_DIR and aim theme_name straight at it via an
    # absolute path. The confinement guard must reject it and fall back to
    # paper, never returning the planted file's contents.
    outside = Path(tempfile.mkdtemp(prefix="ds-irx-outside-")) / "secret.json"
    outside.write_text(
        json.dumps({"primary": "#000000", "leaked_marker": "DO_NOT_LEAK"}),
        encoding="utf-8",
    )
    # studio_theme appends ".json", so pass the path without the suffix.
    theme = studio_theme(str(outside.with_suffix("")))
    assert "leaked_marker" not in theme
    assert theme == studio_theme("paper")
