"""ob-docs FastAPI server — Obsidian-like vault at /vault."""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from application.api.routes_auth import router as auth_router
from application.api.routes_files import router as files_router
from application.api.routes_graph import router as graph_router
from application.api.routes_search import router as search_router
from application import vault_backend, vault_index

logging.basicConfig(
    level=logging.INFO,
    format="%(filename)s:%(lineno)d | %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("server")

_APP_DIR = Path(__file__).resolve().parent
_ROOT = _APP_DIR.parent
_WEB_DIST = _ROOT / "web" / "dist"

_ENABLE_API_DOCS = os.environ.get("ENABLE_API_DOCS", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    vault_backend.ensure_seed_vault()
    mode = vault_backend.backend_mode()
    logger.info("Vault backend mode: %s root=%s", mode, vault_backend.vault_root())
    if mode == "s3":
        try:
            vault_backend.sync_from_s3(force=True)
        except Exception:
            logger.exception("Initial vault S3 sync failed")
    stats = vault_index.rebuild_index()
    logger.info("Vault index ready: %s", stats)
    yield


app = FastAPI(
    title="ob-docs",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/vault/api/docs" if _ENABLE_API_DOCS else None,
    redoc_url="/vault/api/redoc" if _ENABLE_API_DOCS else None,
    openapi_url="/vault/api/openapi.json" if _ENABLE_API_DOCS else None,
)

app.include_router(auth_router)
app.include_router(files_router)
app.include_router(search_router)
app.include_router(graph_router)


@app.get("/vault/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "ob-docs",
        "backend": vault_backend.backend_mode(),
    }


@app.get("/api/health")
def health_alias() -> dict:
    """ALB-friendly alias."""
    return health()


@app.get("/")
def root_redirect():
    """Local convenience: app lives under /vault."""
    return RedirectResponse(url="/vault/", status_code=302)


@app.get("/favicon.ico")
def favicon():
    icon = _WEB_DIST / "favicon.svg"
    if icon.is_file():
        return FileResponse(icon)
    return HTMLResponse("", status_code=204)


if _WEB_DIST.is_dir():
    assets = _WEB_DIST / "assets"
    if assets.is_dir():
        app.mount("/vault/assets", StaticFiles(directory=str(assets)), name="vault-assets")

    @app.get("/vault")
    @app.get("/vault/")
    def vault_index_page():
        index = _WEB_DIST / "index.html"
        if index.is_file():
            return FileResponse(index)
        return HTMLResponse("<h1>ob-docs</h1><p>Build web/ first.</p>", status_code=503)

    @app.get("/vault/{full_path:path}")
    def vault_spa(full_path: str):
        if full_path.startswith("api/"):
            return HTMLResponse("Not Found", status_code=404)
        # static file from dist
        candidate = _WEB_DIST / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        index = _WEB_DIST / "index.html"
        if index.is_file():
            return FileResponse(index)
        return HTMLResponse("Build web/ first", status_code=503)
else:

    @app.get("/vault")
    @app.get("/vault/")
    def vault_missing():
        return HTMLResponse(
            "<h1>ob-docs</h1><p>Frontend not built. Run <code>cd web && npm run build</code>.</p>",
            status_code=503,
        )
