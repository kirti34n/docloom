"""Shared helpers for the seed scripts.

Notebooks are scoped to a workspace, which is scoped to a user (the
multi-tenant auth layer). These scripts predate that layer and used to insert
notebooks with no workspace_id, which the notebooks API silently filters out,
so seeded data was invisible to everyone. Every seed script now goes through
seed_workspace() or newest_notebook() so what they create is actually visible
through the API.

Not itself a seed script: the leading underscore keeps it out of anyone's
`python scripts/seed_*.py` glob.
"""

from docloom_studio.auth import create_user, list_workspaces
from docloom_studio.db import execute, init_db, new_id, now, query_one

DEMO_EMAIL = "demo@docloom.local"
DEMO_PASSWORD = "docloom-demo-seed"


def seed_workspace() -> str:
    """Id of a workspace to attach seeded data to: the first existing user's
    first workspace, or a freshly created demo user's default workspace."""
    init_db()
    user = query_one("SELECT id FROM users ORDER BY created LIMIT 1")
    uid = user["id"] if user else create_user(DEMO_EMAIL, DEMO_PASSWORD)["id"]
    return list_workspaces(uid)[0]["id"]


def newest_notebook(name: str) -> str:
    """Id of the newest notebook actually visible through the API (it has a
    workspace_id), or a new one called `name` in a seeded workspace if there
    is none yet, so each seed script works standalone, in any order."""
    init_db()
    row = query_one(
        "SELECT id FROM notebooks WHERE workspace_id IS NOT NULL "
        "ORDER BY created DESC LIMIT 1"
    )
    if row:
        return row["id"]
    nb = new_id()
    execute(
        "INSERT INTO notebooks (id, name, workspace_id, created, updated) "
        "VALUES (?, ?, ?, ?, ?)", (nb, name, seed_workspace(), now(), now()),
    )
    return nb
