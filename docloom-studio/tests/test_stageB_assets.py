"""Stage B regression for assets.py (CONTRACT C6 + C7).

C6: uploading a logo auto-binds it as the active brand logo when the user
has none set yet, without clobbering a logo already chosen, whether that
prior choice came from an earlier upload or a manual brand-kit edit.

C7: save_generated_image persists AI-generated image bytes the same way an
uploaded asset would: a user-scoped assets row whose asset://{id} both the
render pipeline's resolver (irx._resolve_path) and the serve route resolve.
"""

import io
import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-stageB-assets-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from docloom_studio import assets  # noqa: E402
from docloom_studio.db import execute, init_db, new_id, now, query_one  # noqa: E402
from docloom_studio.irx import _resolve_path  # noqa: E402
from docloom_studio.main import app  # noqa: E402
from docloom_studio.settings import data_dir  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.fixture(autouse=True)
def _db():
    init_db()
    for t in ("chat_messages", "artifact_versions", "artifacts", "sources",
              "notebooks", "assets", "user_settings", "auth_sessions",
              "workspaces", "users"):
        execute(f"DELETE FROM {t}")


def _register(email: str) -> tuple[TestClient, str]:
    c = TestClient(app)
    r = c.post("/api/auth/register", json={"email": email, "password": "password1"})
    return c, r.json()["id"]


def _user() -> str:
    uid = new_id()
    execute("INSERT INTO users (id, email, password_hash, created) VALUES (?, ?, ?, ?)",
            (uid, f"{uid}@t.local", "x", now()))
    return uid


def _upload_logo(c: TestClient, name: str):
    return c.post("/api/assets", files={"file": (name, io.BytesIO(PNG), "image/png")},
                 data={"type": "logo"})


# --------------------------------------------------------------------- C6

def test_first_logo_upload_auto_binds_brand_active():
    c, _uid = _register("logo-a@ex.com")
    r = _upload_logo(c, "brand.png")
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    assert r.json()["logo_asset_id"] == aid  # response reflects the new bind

    brand = c.get("/api/brand-kit").json()
    assert brand["logo_asset_id"] == aid


def test_second_logo_upload_does_not_overwrite_existing_brand_logo():
    c, _uid = _register("logo-b@ex.com")
    first = _upload_logo(c, "one.png").json()["id"]

    r2 = _upload_logo(c, "two.png")
    assert r2.status_code == 200, r2.text
    second = r2.json()["id"]
    assert second != first
    assert r2.json()["logo_asset_id"] is None  # nothing bound this time

    brand = c.get("/api/brand-kit").json()
    assert brand["logo_asset_id"] == first  # still the first upload, not clobbered


def test_upload_never_overwrites_a_manually_chosen_logo():
    c, _uid = _register("logo-c@ex.com")
    # a plain image, manually picked as the brand logo (the pre-existing
    # dropdown flow) rather than uploaded with type=logo
    picked = c.post("/api/assets",
                    files={"file": ("pick.png", io.BytesIO(PNG), "image/png")},
                    data={"type": "image"}).json()["id"]
    assert c.put("/api/brand-kit", json={"logo_asset_id": picked}).status_code == 200

    r = _upload_logo(c, "new.png")
    assert r.json()["logo_asset_id"] is None
    assert c.get("/api/brand-kit").json()["logo_asset_id"] == picked


def test_non_logo_upload_does_not_touch_brand_active():
    c, _uid = _register("logo-d@ex.com")
    r = c.post("/api/assets", files={"file": ("plain.png", io.BytesIO(PNG), "image/png")},
              data={"type": "image"})
    assert r.json()["logo_asset_id"] is None
    assert c.get("/api/brand-kit").json().get("logo_asset_id") is None


# --------------------------------------------------------------------- C7

def test_save_generated_image_writes_file_and_db_row():
    uid = _user()
    aid = assets.save_generated_image(uid, PNG, prompt="a red fox in a misty forest")

    path = data_dir() / "assets" / aid / "generated.png"
    assert path.is_file()
    assert path.read_bytes() == PNG

    row = query_one(
        "SELECT type, filename, user_id, tags FROM assets WHERE id = ?", (aid,))
    assert row["type"] == "image"
    assert row["filename"] == "generated.png"
    assert row["user_id"] == uid
    assert "fox" in row["tags"]


def test_save_generated_image_asset_url_resolves_and_is_user_scoped():
    a, a_uid = _register("gen-a@ex.com")
    b, _b_uid = _register("gen-b@ex.com")
    aid = assets.save_generated_image(a_uid, PNG, prompt="a mountain at sunrise")

    # the render pipeline's own asset:// resolver finds the file
    resolved = _resolve_path(f"asset://{aid}")
    assert resolved is not None and os.path.isfile(resolved)

    # the owner can fetch it; a different user gets 404 (still user-scoped)
    assert a.get(f"/api/assets/{aid}/file").status_code == 200
    assert b.get(f"/api/assets/{aid}/file").status_code == 404


def test_save_generated_image_rejects_oversized_bytes(monkeypatch):
    monkeypatch.setattr("docloom_studio.assets.MAX_UPLOAD_BYTES", 1024)
    uid = _user()
    with pytest.raises(ValueError):
        assets.save_generated_image(uid, b"x" * 2000, prompt="too big")


def test_save_generated_image_normalizes_unknown_extension_to_png():
    uid = _user()
    aid = assets.save_generated_image(uid, PNG, prompt="fallback", ext=".bin")
    row = query_one("SELECT filename FROM assets WHERE id = ?", (aid,))
    assert row["filename"] == "generated.png"
    assert (data_dir() / "assets" / aid / "generated.png").is_file()
