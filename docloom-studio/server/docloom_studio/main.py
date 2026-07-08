"""docloom studio server: FastAPI API + built SPA, one process, one port."""

from __future__ import annotations

import json
import webbrowser
from importlib import resources
from pathlib import Path

import docloom
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .artifacts import router as artifacts_router
from .db import init_db
from .assets import router as assets_router
from .notebooks import router as notebooks_router
from .sources import router as sources_router
from .providers import (
    ProviderConfig, ProviderError, complete, list_models,
)
from .settings import all_settings, get_setting, set_setting

HOST, PORT = "127.0.0.1", 8899

app = FastAPI(title="docloom studio", version=__version__)
app.include_router(notebooks_router)
app.include_router(sources_router)
app.include_router(assets_router)
app.include_router(artifacts_router)


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "version": __version__,
            "docloom": docloom.__version__}


@app.get("/api/settings")
async def read_settings() -> dict:
    return all_settings()


class SettingsPatch(BaseModel):
    values: dict


@app.put("/api/settings")
async def write_settings(patch: SettingsPatch) -> dict:
    for key, value in patch.values.items():
        set_setting(key, value)
    return all_settings()


def provider_for(slot: str) -> ProviderConfig:
    return ProviderConfig(**get_setting(f"provider.{slot}"))


@app.get("/api/providers/models")
async def provider_models(slot: str = "generation") -> dict:
    cfg = provider_for(slot)
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
async def provider_test() -> dict:
    cfg = provider_for("generation")
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


dist = _webdist()
if dist is not None:
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="spa-assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):  # SPA fallback: every non-API route serves index
        if path.startswith("api/"):
            raise HTTPException(404)
        file = dist / path
        if path and file.is_file():
            return FileResponse(file)
        return FileResponse(dist / "index.html")
else:
    @app.get("/", include_in_schema=False)
    async def no_spa() -> JSONResponse:
        return JSONResponse({"docloom-studio": __version__,
                             "note": "frontend not built; run `npm run build` in web/"})


@app.on_event("startup")
async def _startup() -> None:
    init_db()


def run() -> None:
    import uvicorn

    init_db()
    print(f"docloom studio: http://{HOST}:{PORT}")
    try:
        webbrowser.open(f"http://{HOST}:{PORT}")
    except Exception:
        pass
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    run()
