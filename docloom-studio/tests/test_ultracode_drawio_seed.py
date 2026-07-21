"""Phase 1 backend seam for the self-hosted draw.io editor
(scratchpad drawio_plan.md, Phase 1): GET /api/artifacts/{id}/diagram/drawio
returns mxGraph XML seeded from the Diagram IR (the same theme overlay
export/diagram_render use), or the already-forked drawio_xml verbatim once
one exists. Also documents/covers the third payload discriminant
(`type: "diagram_drawio"`) accepted opaquely by the existing PUT /payload.

Covers: IR-seeded XML contains node labels, a saved drawio_xml is returned
untouched (not re-seeded), a legacy D2 {source} artifact -> 422, and
cross-tenant -> 404."""

import os
import tempfile

os.environ.setdefault("DOCLOOM_STUDIO_HOME", tempfile.mkdtemp(prefix="ds-ultracode-drawio-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from docloom_studio.db import execute, init_db, new_id, now, query_one  # noqa: E402
from docloom_studio.generate import create_artifact, save_artifact  # noqa: E402
from docloom_studio.main import app  # noqa: E402

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


def _diagram_ir_artifact(user_id: str) -> str:
    nb = _notebook_for(user_id)
    aid = create_artifact(nb, "diagram")
    save_artifact(aid, "Sample Architecture", {
        "type": "diagram_ir", "diagram_ir": DIAGRAM, "theme_name": "paper",
        "layout": "native", "overlay": None, "render": "svg",
    })
    return aid


def _legacy_d2_artifact(user_id: str) -> str:
    nb = _notebook_for(user_id)
    aid = create_artifact(nb, "diagram")
    save_artifact(aid, "Legacy D2 Diagram", {
        "source": "client -> api -> db", "mermaid_src": "client -> api -> db",
    })
    return aid


# ------------------------------------------------------------- /diagram/drawio

def test_seed_returns_mxfile_xml_with_node_labels():
    c, uid = _register("drawio-a@ex.com")
    aid = _diagram_ir_artifact(uid)

    r = c.get(f"/api/artifacts/{aid}/diagram/drawio")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/xml")
    xml = r.text
    assert xml.startswith("<mxfile") or "<mxfile" in xml
    assert "Client App" in xml
    assert "API Gateway" in xml
    assert "Primary Database" in xml


def test_saved_drawio_xml_is_returned_verbatim_not_reseeded():
    c, uid = _register("drawio-b@ex.com")
    aid = _diagram_ir_artifact(uid)

    forked_xml = "<mxfile><diagram><mxGraphModel><root><mxCell id=\"0\"/></root>" \
                 "</mxGraphModel></diagram></mxfile>"
    r = c.put(f"/api/artifacts/{aid}/payload", json={"payload": {
        "type": "diagram_drawio", "drawio_xml": forked_xml,
        "theme_name": "paper", "render": "svg", "diagram_ir": DIAGRAM,
    }})
    assert r.status_code == 200, r.text

    # GET /artifacts/{id} echoes the saved payload, including drawio_xml.
    got = c.get(f"/api/artifacts/{aid}")
    assert got.status_code == 200, got.text
    assert got.json()["payload"]["drawio_xml"] == forked_xml

    r2 = c.get(f"/api/artifacts/{aid}/diagram/drawio")
    assert r2.status_code == 200, r2.text
    assert r2.text == forked_xml
    # Not a re-seed: none of the IR's own node labels appear in this tiny
    # hand-written fork.
    assert "Client App" not in r2.text


def test_legacy_source_only_artifact_returns_422():
    c, uid = _register("drawio-c@ex.com")
    aid = _legacy_d2_artifact(uid)

    r = c.get(f"/api/artifacts/{aid}/diagram/drawio")
    assert r.status_code == 422, r.text


def test_other_users_artifact_is_404():
    _c1, uid1 = _register("drawio-d1@ex.com")
    aid = _diagram_ir_artifact(uid1)
    c2, _uid2 = _register("drawio-d2@ex.com")

    r = c2.get(f"/api/artifacts/{aid}/diagram/drawio")
    assert r.status_code == 404, r.text


def test_requires_auth():
    c = TestClient(app)
    r = c.get("/api/artifacts/does-not-matter/diagram/drawio")
    assert r.status_code == 401
