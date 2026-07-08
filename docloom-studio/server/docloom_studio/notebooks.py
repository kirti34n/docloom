"""Notebook CRUD routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .db import execute, new_id, now, query_all, query_one, rows_to_dicts

router = APIRouter(prefix="/api/notebooks", tags=["notebooks"])


class NotebookIn(BaseModel):
    name: str = "Untitled notebook"


@router.get("")
async def list_notebooks() -> list[dict]:
    return rows_to_dicts(query_all(
        "SELECT id, name, created, updated FROM notebooks ORDER BY updated DESC"
    ))


@router.post("")
async def create_notebook(body: NotebookIn) -> dict:
    nb_id = new_id()
    t = now()
    execute("INSERT INTO notebooks (id, name, created, updated) VALUES (?, ?, ?, ?)",
            (nb_id, body.name, t, t))
    return {"id": nb_id, "name": body.name, "created": t, "updated": t}


@router.get("/{notebook_id}")
async def get_notebook(notebook_id: str) -> dict:
    row = query_one("SELECT * FROM notebooks WHERE id = ?", (notebook_id,))
    if row is None:
        raise HTTPException(404, "notebook not found")
    artifacts = rows_to_dicts(query_all(
        "SELECT id, kind, title, version, updated FROM artifacts "
        "WHERE notebook_id = ? ORDER BY updated DESC", (notebook_id,)
    ))
    sources = rows_to_dicts(query_all(
        "SELECT id, kind, title, status, context_mode, url, created "
        "FROM sources WHERE notebook_id = ? ORDER BY created", (notebook_id,)
    ))
    return {**dict(row), "artifacts": artifacts, "sources": sources}


@router.patch("/{notebook_id}")
async def rename_notebook(notebook_id: str, body: NotebookIn) -> dict:
    execute("UPDATE notebooks SET name = ?, updated = ? WHERE id = ?",
            (body.name, now(), notebook_id))
    return {"ok": True}


@router.delete("/{notebook_id}")
async def delete_notebook(notebook_id: str) -> dict:
    execute("DELETE FROM artifact_versions WHERE artifact_id IN "
            "(SELECT id FROM artifacts WHERE notebook_id = ?)", (notebook_id,))
    execute("DELETE FROM artifacts WHERE notebook_id = ?", (notebook_id,))
    execute("DELETE FROM sources WHERE notebook_id = ?", (notebook_id,))
    execute("DELETE FROM notebooks WHERE id = ?", (notebook_id,))
    return {"ok": True}
