"""Regression: the notebook file-source upload route confines the written file
to the source directory. The basename strip in add_file leaves a Windows drive
prefix intact, so a name like "D:pwned.txt" (an allowed .txt) joined onto the
source dir becomes the drive-relative path D:pwned.txt and would write to D:'s
root, escaping data_dir()/sources. The containment check (dest.parent must
equal the resolved source dir) rejects it while a normal upload still works."""

import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-srcup-"))

import io  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from docloom_studio.db import execute, init_db  # noqa: E402
from docloom_studio.ingest import _source_dir  # noqa: E402
from docloom_studio.main import app  # noqa: E402


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


def _notebook(client: TestClient) -> str:
    wid = client.get("/api/workspaces").json()[0]["id"]
    return client.post(
        "/api/notebooks", json={"name": "n", "workspace_id": wid}).json()["id"]


def test_file_upload_rejects_drive_relative_name_but_allows_normal():
    a = _register("srcup-a@ex.com")
    nb = _notebook(a)

    # "D:pwned.txt" has no separators (survives the basename strip) and an
    # allowed .txt extension; joined onto the source dir it is the Windows
    # drive-relative path D:pwned.txt, which resolves outside data_dir().
    evil = a.post(f"/api/notebooks/{nb}/sources/file",
                  files={"file": ("D:pwned.txt", io.BytesIO(b"owned"), "text/plain")})
    assert evil.status_code == 400, evil.text

    # a normal filename still uploads and lands inside the source directory
    ok = a.post(f"/api/notebooks/{nb}/sources/file",
                files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")})
    assert ok.status_code == 200, ok.text
    sid = ok.json()["source_id"]
    assert (_source_dir(sid) / "notes.txt").is_file()
