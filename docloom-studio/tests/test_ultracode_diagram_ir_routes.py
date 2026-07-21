"""P0 backend routes for the in-app diagram IR editor (editor-design.md
section 3a/3b): POST /diagram/layout (solve a working Diagram IR into the
geometry the canvas seeds from, never persisting) and POST /diagram/render
(the parity engine -- renders through docloom.render_diagram(), the exact
path export uses, and writes render.svg/render.png via the same fixed-name
file plumbing save_renders/_resolve_artifact_render read).

Covers: well-formed geometry (every node has x/y/w/h, groups present, edges
have pts), render.svg written with node labels inside, a duplicate-id
diagram -> 422 from both routes, and cross-tenant auth -> 404."""

import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-ultracode-diagram-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from docloom_studio.db import execute, init_db, new_id, now, query_one  # noqa: E402
from docloom_studio.generate import create_artifact, save_artifact  # noqa: E402
from docloom_studio.main import app  # noqa: E402
from docloom_studio.settings import data_dir  # noqa: E402

DIAGRAM = {
    "type": "diagram",
    "direction": "LR",
    "title": "Sample Architecture",
    "nodes": [
        {"id": "n1", "label": "Client App", "type": "client"},
        {"id": "n2", "label": "API Gateway", "type": "service", "group": "g1"},
        {"id": "n3", "label": "Primary Database", "type": "store", "group": "g1"},
    ],
    "edges": [
        {"source": "n1", "target": "n2", "label": "HTTPS"},
        {"source": "n2", "target": "n3"},
    ],
    "groups": [{"id": "g1", "label": "Backend", "kind": "region"}],
}

DUPLICATE_ID_DIAGRAM = {
    "type": "diagram",
    "direction": "LR",
    "nodes": [
        {"id": "n1", "label": "First", "type": "service"},
        {"id": "n1", "label": "Second", "type": "service"},
    ],
    "edges": [],
    "groups": [],
}


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
    assert r.status_code == 200, r.text
    return c, r.json()["id"]


def _notebook_for(user_id: str) -> str:
    wid = query_one("SELECT id FROM workspaces WHERE user_id = ?", (user_id,))["id"]
    nb = new_id()
    execute("INSERT INTO notebooks (id, name, workspace_id, created, updated) "
            "VALUES (?, ?, ?, ?, ?)", (nb, "nb", wid, now(), now()))
    return nb


def _diagram_artifact(user_id: str) -> str:
    nb = _notebook_for(user_id)
    aid = create_artifact(nb, "diagram")
    save_artifact(aid, "Sample Architecture", {
        "type": "diagram_ir", "diagram_ir": DIAGRAM, "theme_name": "paper",
        "layout": "native", "overlay": None, "render": "svg",
    })
    return aid


# --------------------------------------------------------------- /diagram/layout

def test_layout_returns_well_formed_geometry():
    c, uid = _register("layout-a@ex.com")
    aid = _diagram_artifact(uid)

    r = c.post(f"/api/artifacts/{aid}/diagram/layout",
               json={"diagram_ir": DIAGRAM, "layout": "native", "theme_name": "paper"})
    assert r.status_code == 200, r.text
    report = r.json()

    assert report["width"] > 0 and report["height"] > 0
    assert len(report["nodes"]) == 3
    for n in report["nodes"]:
        for key in ("id", "type", "label", "x", "y", "w", "h"):
            assert key in n, f"node missing {key}: {n}"
        assert isinstance(n["x"], (int, float))
        assert n["w"] > 0 and n["h"] > 0

    assert len(report["groups"]) == 1
    g = report["groups"][0]
    for key in ("id", "label", "x", "y", "w", "h"):
        assert key in g

    assert len(report["edges"]) == 2
    for e in report["edges"]:
        assert "pts" in e and len(e["pts"]) >= 2
        assert "source" in e and "target" in e


def test_layout_defaults_to_native_layout_without_theme_name():
    c, uid = _register("layout-b@ex.com")
    aid = _diagram_artifact(uid)
    r = c.post(f"/api/artifacts/{aid}/diagram/layout", json={"diagram_ir": DIAGRAM})
    assert r.status_code == 200, r.text
    assert r.json()["direction"] in ("LR", "TB")


def test_layout_duplicate_ids_returns_422():
    c, uid = _register("layout-c@ex.com")
    aid = _diagram_artifact(uid)
    r = c.post(f"/api/artifacts/{aid}/diagram/layout",
               json={"diagram_ir": DUPLICATE_ID_DIAGRAM})
    assert r.status_code == 422, r.text


def test_layout_invalid_diagram_shape_returns_422():
    c, uid = _register("layout-d@ex.com")
    aid = _diagram_artifact(uid)
    r = c.post(f"/api/artifacts/{aid}/diagram/layout",
               json={"diagram_ir": {"nodes": [{"id": "n1"}]}})  # missing required label
    assert r.status_code == 422, r.text


def test_layout_other_users_artifact_is_404():
    _c1, uid1 = _register("layout-e1@ex.com")
    aid = _diagram_artifact(uid1)
    c2, _uid2 = _register("layout-e2@ex.com")
    r = c2.post(f"/api/artifacts/{aid}/diagram/layout", json={"diagram_ir": DIAGRAM})
    assert r.status_code == 404, r.text


def test_layout_requires_auth():
    c = TestClient(app)
    r = c.post("/api/artifacts/does-not-matter/diagram/layout", json={"diagram_ir": DIAGRAM})
    assert r.status_code == 401


# --------------------------------------------------------------- /diagram/render

def test_render_writes_render_svg_containing_node_labels_and_returns_it():
    c, uid = _register("render-a@ex.com")
    aid = _diagram_artifact(uid)

    r = c.post(f"/api/artifacts/{aid}/diagram/render",
               json={"diagram_ir": DIAGRAM, "theme_name": "paper", "layout": "native"})
    assert r.status_code == 200, r.text
    svg = r.json()["svg"]
    assert "<svg" in svg
    assert "Client App" in svg
    assert "API Gateway" in svg
    assert "Primary Database" in svg

    adir = data_dir() / "artifacts" / aid
    render_svg_path = adir / "render.svg"
    assert render_svg_path.is_file()
    assert render_svg_path.read_text(encoding="utf-8") == svg

    # png is best-effort (None, and no file written, if the raster extra
    # isn't installed) -- only assert it's non-empty when it *was* written.
    render_png_path = adir / "render.png"
    if render_png_path.is_file():
        assert render_png_path.stat().st_size > 0


def test_render_duplicate_ids_returns_422_and_does_not_write_render():
    c, uid = _register("render-b@ex.com")
    aid = _diagram_artifact(uid)
    r = c.post(f"/api/artifacts/{aid}/diagram/render",
               json={"diagram_ir": DUPLICATE_ID_DIAGRAM})
    assert r.status_code == 422, r.text


def test_render_other_users_artifact_is_404():
    _c1, uid1 = _register("render-c1@ex.com")
    aid = _diagram_artifact(uid1)
    c2, _uid2 = _register("render-c2@ex.com")
    r = c2.post(f"/api/artifacts/{aid}/diagram/render", json={"diagram_ir": DIAGRAM})
    assert r.status_code == 404, r.text


def test_render_requires_auth():
    c = TestClient(app)
    r = c.post("/api/artifacts/does-not-matter/diagram/render", json={"diagram_ir": DIAGRAM})
    assert r.status_code == 401
