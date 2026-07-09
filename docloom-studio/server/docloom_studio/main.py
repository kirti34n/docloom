"""docloom studio server: FastAPI API + built SPA, one process, one port."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

import docloom
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .artifacts import router as artifacts_router
from .auth import current_user, router as auth_router
from .db import execute, init_db, new_id, now
from .assets import router as assets_router
from .jobs import reconcile_jobs
from .notebooks import router as notebooks_router
from .sources import router as sources_router
from .providers import (
    ProviderConfig, ProviderError, complete, list_models,
)
from .settings import (
    all_settings, get_setting, redact_settings, set_setting, unmask_value,
)


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


HOST = _env("DOCLOOM_STUDIO_HOST", "127.0.0.1")
PORT = int(_env("DOCLOOM_STUDIO_PORT", "8899"))
NO_BROWSER = _env("DOCLOOM_STUDIO_NO_BROWSER", "").lower() in ("1", "true", "yes")
LOG_LEVEL = _env("DOCLOOM_STUDIO_LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("docloom_studio")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    reconcile_jobs()  # logs its own count
    log.info("docloom studio %s ready (docloom %s)", __version__, docloom.__version__)
    yield


app = FastAPI(title="docloom studio", version=__version__, lifespan=lifespan)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Attach a request id, time each request, and log it. The id is echoed as
    X-Request-ID so a client error can be correlated with a server log line."""
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("rid=%s %s %s -> unhandled", rid, request.method, request.url.path)
        raise
    dur_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Request-ID"] = rid
    # keep noise down: chatty static/asset GETs log at debug
    level = logging.DEBUG if request.url.path.startswith("/assets") else logging.INFO
    log.log(level, "rid=%s %s %s -> %d %.1fms",
            rid, request.method, request.url.path, response.status_code, dur_ms)
    return response


app.include_router(auth_router)
app.include_router(notebooks_router)
app.include_router(sources_router)
app.include_router(assets_router)
app.include_router(artifacts_router)


@app.get("/api/health")
async def health() -> dict:
    """Liveness + DB-writability. A read-only or missing DB is not healthy."""
    db_ok = True
    db_error = None
    try:
        probe = new_id()
        execute("INSERT INTO health_probe (id, t) VALUES (?, ?)", (probe, now()))
        execute("DELETE FROM health_probe WHERE id = ?", (probe,))
    except Exception as e:  # pragma: no cover - exercised via degraded DB
        db_ok = False
        db_error = str(e)[:200]
    body = {"ok": db_ok, "version": __version__,
            "docloom": docloom.__version__, "db": db_ok}
    if db_error:
        body["error"] = db_error
    return JSONResponse(body, status_code=200 if db_ok else 503)


@app.get("/api/settings")
async def read_settings(user: dict = Depends(current_user)) -> dict:
    return redact_settings(all_settings(user["id"]))


class SettingsPatch(BaseModel):
    values: dict


@app.put("/api/settings")
async def write_settings(
    patch: SettingsPatch, user: dict = Depends(current_user)
) -> dict:
    for key, value in patch.values.items():
        set_setting(key, unmask_value(key, value, user["id"]), user["id"])
    return redact_settings(all_settings(user["id"]))


def provider_for(slot: str, user_id: str) -> ProviderConfig:
    return ProviderConfig(**get_setting(f"provider.{slot}", user_id))


@app.get("/api/providers/models")
async def provider_models(
    slot: str = "generation", user: dict = Depends(current_user)
) -> dict:
    cfg = provider_for(slot, user["id"])
    try:
        return {"models": await list_models(cfg)}
    except Exception as e:
        return {"models": [], "error": str(e)[:300]}


_TEST_SCHEMA = {
    "type": "object",
    "properties": {"greeting": {"type": "string"},
                   "number": {"type": "integer"}},
    "required": ["greeting", "number"],
    "additionalProperties": False,
}


@app.post("/api/providers/test")
async def provider_test(user: dict = Depends(current_user)) -> dict:
    cfg = provider_for("generation", user["id"])
    try:
        raw = await complete(
            cfg,
            [{"role": "user",
              "content": "Reply with a JSON greeting and any number."}],
            schema=_TEST_SCHEMA, max_tokens=200,
        )
        parsed = json.loads(raw)
        ok = isinstance(parsed.get("greeting"), str)
        return {"ok": ok, "raw": raw[:400]}
    except (ProviderError, json.JSONDecodeError) as e:
        return {"ok": False, "error": str(e)[:400]}


@app.get("/api/themes")
async def themes() -> list[dict]:
    theme_dir = Path(__file__).parent / "themes"
    out = []
    for f in sorted(theme_dir.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        data["name"] = f.stem
        out.append(data)
    return out


@app.get("/api/layout")
async def layout() -> dict:
    from docloom.render import pptx as pptx_renderer

    constants = getattr(pptx_renderer, "LAYOUT", None)
    if constants is None:  # renderer predates 0.2 constants export
        constants = {"slide_w_in": 13.333, "slide_h_in": 7.5}
    return constants


# ---- SPA ------------------------------------------------------------------

def _webdist() -> Path | None:
    candidates = [
        Path(__file__).parent / "webdist",                 # installed wheel
        Path(__file__).parents[2] / "web" / "dist",        # repo layout
    ]
    for c in candidates:
        if (c / "index.html").is_file():
            return c
    return None


def _safe_spa_file(dist: Path, path: str) -> Path | None:
    """Resolve `path` under `dist`, or None if it's empty, an API route, or
    escapes the dist root (path traversal). Prevents arbitrary file reads."""
    if not path or path.startswith("api/"):
        return None
    root = dist.resolve()
    candidate = (root / path).resolve()
    if root in candidate.parents and candidate.is_file():
        return candidate
    return None


dist = _webdist()
if dist is not None:
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="spa-assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):  # SPA fallback: every non-API route serves index
        if path.startswith("api/"):
            raise HTTPException(404)
        file = _safe_spa_file(dist, path)
        if file is not None:
            return FileResponse(file)
        return FileResponse(dist / "index.html")
else:
    @app.get("/", include_in_schema=False)
    async def no_spa() -> JSONResponse:
        return JSONResponse({"docloom-studio": __version__,
                             "note": "frontend not built; run `npm run build` in web/"})


def run() -> None:
    import uvicorn

    init_db()
    # For the browser link, show a loopback host even when bound to 0.0.0.0.
    link_host = "127.0.0.1" if HOST in ("0.0.0.0", "::") else HOST
    print(f"docloom studio: http://{link_host}:{PORT}")
    if not NO_BROWSER:
        try:
            webbrowser.open(f"http://{link_host}:{PORT}")
        except Exception:
            pass
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    run()
