"""Email + password auth for the multi-tenant foundation.

Passwords are hashed with stdlib scrypt (memory-hard, no third-party dep).
Sessions are opaque random tokens; only their SHA-256 is stored, so a DB leak
can't be replayed. Every user gets a default workspace; notebooks and their
sources/artifacts scope to a workspace the user owns.

This module is additive: it exposes `current_user` (required) and
`current_user_optional` dependencies plus a router. Wiring the existing
notebook/source/artifact routes to enforce it is the next step."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel

from .db import execute, new_id, now, query_all, query_one, rows_to_dicts

SESSION_COOKIE = "ds_session"
SESSION_TTL = 60 * 60 * 24 * 30  # 30 days, seconds
MIN_PASSWORD = 8

# scrypt work factors (RFC 7914 interactive-login range)
_N, _R, _P, _DKLEN = 2**14, 8, 1, 32


# ----------------------------------------------------------------- passwords

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"scrypt${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex),
            n=_N, r=_R, p=_P, dklen=_DKLEN,
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


# -------------------------------------------------------- users + workspaces

def create_workspace(user_id: str, name: str) -> dict:
    name = (name or "").strip() or "Untitled workspace"
    wid = new_id()
    execute("INSERT INTO workspaces (id, user_id, name, created) VALUES (?, ?, ?, ?)",
            (wid, user_id, name, now()))
    return {"id": wid, "name": name}


def list_workspaces(user_id: str) -> list[dict]:
    return rows_to_dicts(query_all(
        "SELECT id, name, created FROM workspaces WHERE user_id = ? ORDER BY created",
        (user_id,)))


def user_owns_workspace(user_id: str, workspace_id: str) -> bool:
    return query_one("SELECT 1 FROM workspaces WHERE id = ? AND user_id = ?",
                     (workspace_id, user_id)) is not None


def require_notebook(user_id: str, notebook_id: str) -> None:
    """404 unless the notebook lives in a workspace this user owns.
    (404 not 403, so IDs don't leak existence across tenants.)"""
    if query_one(
        "SELECT 1 FROM notebooks n JOIN workspaces w ON w.id = n.workspace_id "
        "WHERE n.id = ? AND w.user_id = ?", (notebook_id, user_id)) is None:
        raise HTTPException(404, "notebook not found")


def require_source(user_id: str, source_id: str) -> str:
    """Authorize a source by walking source → notebook → workspace → user.
    Returns the owning notebook_id."""
    row = query_one(
        "SELECT s.notebook_id FROM sources s "
        "JOIN notebooks n ON n.id = s.notebook_id "
        "JOIN workspaces w ON w.id = n.workspace_id "
        "WHERE s.id = ? AND w.user_id = ?", (source_id, user_id))
    if row is None:
        raise HTTPException(404, "source not found")
    return row["notebook_id"]


def require_artifact(user_id: str, artifact_id: str) -> str:
    """Authorize an artifact by walking artifact → notebook → workspace → user.
    Returns the owning notebook_id."""
    row = query_one(
        "SELECT a.notebook_id FROM artifacts a "
        "JOIN notebooks n ON n.id = a.notebook_id "
        "JOIN workspaces w ON w.id = n.workspace_id "
        "WHERE a.id = ? AND w.user_id = ?", (artifact_id, user_id))
    if row is None:
        raise HTTPException(404, "artifact not found")
    return row["notebook_id"]


def create_user(email: str, password: str) -> dict:
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "a valid email is required")
    if len(password or "") < MIN_PASSWORD:
        raise HTTPException(400, f"password must be at least {MIN_PASSWORD} characters")
    if query_one("SELECT id FROM users WHERE email = ?", (email,)):
        raise HTTPException(409, "an account with that email already exists")
    uid = new_id()
    execute("INSERT INTO users (id, email, password_hash, created) VALUES (?, ?, ?, ?)",
            (uid, email, hash_password(password), now()))
    create_workspace(uid, "My workspace")  # every user starts with one
    return {"id": uid, "email": email}


def authenticate(email: str, password: str) -> str | None:
    row = query_one("SELECT id, password_hash FROM users WHERE email = ?",
                    ((email or "").strip().lower(),))
    if row and verify_password(password, row["password_hash"]):
        return row["id"]
    return None


# ----------------------------------------------------------------- sessions

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    execute("INSERT INTO auth_sessions (token_hash, user_id, created, expires) "
            "VALUES (?, ?, ?, ?)",
            (_token_hash(token), user_id, now(), now() + SESSION_TTL))
    return token


def resolve_session(token: str | None) -> dict | None:
    if not token:
        return None
    row = query_one(
        "SELECT s.user_id, s.expires, u.email FROM auth_sessions s "
        "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
        (_token_hash(token),))
    if row is None or row["expires"] < now():
        return None
    return {"id": row["user_id"], "email": row["email"]}


def delete_session(token: str | None) -> None:
    if token:
        execute("DELETE FROM auth_sessions WHERE token_hash = ?", (_token_hash(token),))


# ------------------------------------------------------- FastAPI dependencies

def current_user_optional(ds_session: str | None = Cookie(default=None)) -> dict | None:
    return resolve_session(ds_session)


def current_user(ds_session: str | None = Cookie(default=None)) -> dict:
    user = resolve_session(ds_session)
    if user is None:
        raise HTTPException(401, "not authenticated")
    return user


# ----------------------------------------------------------------- routes

router = APIRouter(prefix="/api", tags=["auth"])


class Credentials(BaseModel):
    email: str
    password: str


class WorkspaceCreate(BaseModel):
    name: str


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_TTL,
        httponly=True, samesite="lax", path="/",
    )


@router.post("/auth/register")
async def register(body: Credentials, response: Response) -> dict:
    user = create_user(body.email, body.password)
    _set_session_cookie(response, create_session(user["id"]))
    return user


@router.post("/auth/login")
async def login(body: Credentials, response: Response) -> dict:
    uid = authenticate(body.email, body.password)
    if uid is None:
        raise HTTPException(401, "invalid email or password")
    _set_session_cookie(response, create_session(uid))
    return {"id": uid, "email": body.email.strip().lower()}


@router.post("/auth/logout")
async def logout(response: Response,
                 ds_session: str | None = Cookie(default=None)) -> dict:
    delete_session(ds_session)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
async def me(user: dict = Depends(current_user)) -> dict:
    return user


@router.get("/workspaces")
async def get_workspaces(user: dict = Depends(current_user)) -> list[dict]:
    return list_workspaces(user["id"])


@router.post("/workspaces")
async def post_workspace(body: WorkspaceCreate,
                         user: dict = Depends(current_user)) -> dict:
    return create_workspace(user["id"], body.name)
