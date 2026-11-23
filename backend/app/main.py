"""FastAPI application: `uvicorn app.main:app` (systemd: osm-tilegen)."""

from __future__ import annotations

import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, db
from .api import exports as exports_api
from .api import jobs, maps, outputs, preview
from .core.config import get_settings
from .importer import exports


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_settings().ensure_dirs()
    db.init_db()
    exports.migrate_legacy()
    yield


app = FastAPI(title="osm-tilegen", version=__version__, lifespan=lifespan)
app.include_router(maps.router)
app.include_router(exports_api.router)
app.include_router(jobs.router)
app.include_router(preview.router)
app.include_router(outputs.router)


@app.get("/api/health")
def health() -> dict:
    s = get_settings()
    usage = shutil.disk_usage(s.data_dir) if s.data_dir.exists() else None
    return {
        "version": __version__,
        "render_engine": s.render_engine,
        "tools": {
            "osmium": shutil.which(s.osmium_bin) is not None,
            "tilemaker": shutil.which(s.tilemaker_bin) is not None,
        },
        "disk_free_bytes": usage.free if usage else None,
    }


_settings = get_settings()
if _settings.styles_dir.exists():
    app.mount("/styles", StaticFiles(directory=_settings.styles_dir), name="styles")

class _UiFiles(StaticFiles):
    """UI files revalidate on every load (ETag), so a rebuilt UI is never hidden by browser cache."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


if (_settings.frontend_dist / "index.html").exists():
    app.mount("/", _UiFiles(directory=_settings.frontend_dist, html=True), name="ui")
else:
    @app.get("/", response_class=HTMLResponse)
    def no_ui() -> str:
        return (
            "<h1>osm-tilegen</h1><p>Frontend nie jest zbudowany. "
            "Uruchom <code>make frontend</code> albo w trybie dev "
            "<code>make dev-frontend</code> (http://localhost:5173).</p>"
            '<p><a href="/docs">API</a></p>'
        )
