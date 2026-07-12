"""Notebook CRUD routes — scoped to a workspace the current user owns."""

from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .auth import current_user, require_notebook, user_owns_workspace
from .db import execute, new_id, now, query_all, query_one, rows_to_dicts
from .settings import data_dir

router = APIRouter(prefix="/api/notebooks", tags=["notebooks"])


class NotebookCreate(BaseModel):
    name: str = "Untitled notebook"
    workspace_id: str


class NotebookRename(BaseModel):
    name: str


@router.get("")
async def list_notebooks(
    workspace_id: str = Query(...), user: dict = Depends(current_user)
) -> list[dict]:
    if not user_owns_workspace(user["id"], workspace_id):
        raise HTTPException(404, "workspace not found")
    return rows_to_dicts(query_all(
        "SELECT id, name, created, updated FROM notebooks "
        "WHERE workspace_id = ? ORDER BY updated DESC", (workspace_id,)
    ))


@router.post("")
async def create_notebook(
    body: NotebookCreate, user: dict = Depends(current_user)
) -> dict:
    if not user_owns_workspace(user["id"], body.workspace_id):
        raise HTTPException(404, "workspace not found")
    nb_id = new_id()
    t = now()
    execute(
        "INSERT INTO notebooks (id, name, workspace_id, created, updated) "
        "VALUES (?, ?, ?, ?, ?)", (nb_id, body.name, body.workspace_id, t, t)
    )
    return {"id": nb_id, "name": body.name, "workspace_id": body.workspace_id,
            "created": t, "updated": t}


@router.get("/{notebook_id}")
async def get_notebook(notebook_id: str, user: dict = Depends(current_user)) -> dict:
    require_notebook(user["id"], notebook_id)
    row = query_one("SELECT * FROM notebooks WHERE id = ?", (notebook_id,))
    artifacts = rows_to_dicts(query_all(
        "SELECT id, kind, title, version, status, updated FROM artifacts "
        "WHERE notebook_id = ? ORDER BY updated DESC", (notebook_id,)
    ))
    sources = rows_to_dicts(query_all(
        "SELECT id, kind, title, status, context_mode, url, created "
        "FROM sources WHERE notebook_id = ? ORDER BY created", (notebook_id,)
    ))
    return {**dict(row), "artifacts": artifacts, "sources": sources}


@router.patch("/{notebook_id}")
async def rename_notebook(
    notebook_id: str, body: NotebookRename, user: dict = Depends(current_user)
) -> dict:
    require_notebook(user["id"], notebook_id)
    execute("UPDATE notebooks SET name = ?, updated = ? WHERE id = ?",
            (body.name, now(), notebook_id))
    return {"ok": True}


@router.delete("/{notebook_id}")
async def delete_notebook(
    notebook_id: str, user: dict = Depends(current_user)
) -> dict:
    require_notebook(user["id"], notebook_id)
    source_ids = [r["id"] for r in query_all(
        "SELECT id FROM sources WHERE notebook_id = ?", (notebook_id,))]
    execute("DELETE FROM artifact_versions WHERE artifact_id IN "
            "(SELECT id FROM artifacts WHERE notebook_id = ?)", (notebook_id,))
    execute("DELETE FROM artifacts WHERE notebook_id = ?", (notebook_id,))
    execute("DELETE FROM sources WHERE notebook_id = ?", (notebook_id,))
    # chat_messages also references notebooks(id); with foreign_keys=ON the final
    # delete fails unless these are removed first
    execute("DELETE FROM chat_messages WHERE notebook_id = ?", (notebook_id,))
    execute("DELETE FROM notebooks WHERE id = ?", (notebook_id,))
    for sid in source_ids:
        d = data_dir() / "sources" / sid
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}
