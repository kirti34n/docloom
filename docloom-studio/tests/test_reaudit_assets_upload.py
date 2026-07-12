"""Re-audit regression: the asset upload route must reject a Windows drive-
relative filename (e.g. "D:pwned.exe") that has no separator to strip yet joins
onto another drive's root, escaping the asset sandbox, and must validate the
'type' field so an unknown value cannot bypass the extension gate. Companion to
the upload guardrails in tests/test_security.py."""

import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-reaudit-assets-"))

import io  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from docloom_studio.db import execute, init_db  # noqa: E402
from docloom_studio.main import app  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture(autouse=True)
def _db():
    init_db()
    for t in ("chat_messages", "artifact_versions", "artifacts", "sources",
              "notebooks", "assets", "user_settings", "auth_sessions",
              "workspaces", "users"):
        execute(f"DELETE FROM {t}")


def _register(email: str) -> TestClient:
    c = TestClient(app)
    c.post("/api/auth/register", json={"email": email, "password": "password1"})
    return c


def _escaped(name: str) -> bool:
    """Best-effort, portable check that nothing was written to a drive root."""
    for root in ("C:/", "D:/", "E:/"):
        try:
            if Path(root + name).exists():
                return True
        except OSError:
            pass
    return False


def test_upload_rejects_windows_drive_relative_filename():
    a = _register("drive-a@ex.com")
    # ".png" clears the extension gate, so the drive prefix is what must be
    # caught: "D:pwned.png" joins onto D:'s root, escaping the asset directory.
    r = a.post("/api/assets",
               files={"file": ("D:pwned.png", io.BytesIO(PNG), "image/png")},
               data={"type": "image"})
    assert r.status_code == 400, r.text
    assert a.get("/api/assets").json() == []  # no asset row created
    assert not _escaped("pwned.png")


def test_upload_rejects_unknown_type_that_would_bypass_extension_gate():
    a = _register("drive-b@ex.com")
    # type "icon" is not a real type; before validation it slipped past both
    # extension gates, letting a drive-relative name write an arbitrary .exe.
    r = a.post("/api/assets",
               files={"file": ("D:pwned.exe", io.BytesIO(PNG), "application/octet-stream")},
               data={"type": "icon"})
    assert r.status_code == 400, r.text
    assert a.get("/api/assets").json() == []
    assert not _escaped("pwned.exe")


def test_normal_image_upload_still_succeeds():
    a = _register("drive-c@ex.com")
    r = a.post("/api/assets",
               files={"file": ("pic.png", io.BytesIO(PNG), "image/png")},
               data={"type": "image"})
    assert r.status_code == 200, r.text
    assert a.get("/api/assets").json()[0]["filename"] == "pic.png"
