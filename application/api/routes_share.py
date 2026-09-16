"""Public share viewer — no session cookie required."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

from application.api.routes_auth import require_user_id
from application import vault_backend, vault_share, viewer_html

# Authenticated create endpoint lives under /vault/api
api_router = APIRouter(prefix="/vault/api/files", tags=["share"])

# Public pages under /vault/s/… (no auth)
public_router = APIRouter(prefix="/vault/s", tags=["share-public"])


class ShareBody(BaseModel):
    path: str = Field(..., min_length=1, max_length=1024)


def _share_asset_rel(note_path: str, rel: str) -> str:
    cleaned = unquote((rel or "").replace("\\", "/")).strip()
    if (
        not cleaned
        or cleaned.startswith("/")
        or cleaned.startswith("http:")
        or cleaned.startswith("https:")
        or cleaned.startswith("data:")
        or ".." in cleaned.split("/")
    ):
        raise HTTPException(status_code=400, detail="Invalid asset path")
    note_dir = str(Path(note_path).parent).replace("\\", "/")
    if note_dir in {".", ""}:
        joined = cleaned
    else:
        joined = f"{note_dir}/{cleaned}"
    joined = joined.replace("\\", "/").lstrip("/")
    try:
        target = vault_backend.resolve_vault_path(joined)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if target.is_file():
        return joined
    # Obsidian-style basename fallback on local vault
    found = vault_backend.find_vault_file(cleaned)
    if found is not None:
        root = vault_backend.vault_root()
        try:
            return found.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return joined
    # Fall through — S3 lookup may still succeed for the joined path
    return joined


@api_router.post("/share")
def create_share(request: Request, body: ShareBody) -> dict:
    """Create (or reuse) a public share link for a markdown note."""
    require_user_id(request)
    try:
        entry = vault_share.create_or_get_share(body.path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    token = entry["token"]
    url_path = vault_share.public_share_path(token)
    return {
        "ok": True,
        "token": token,
        "path": entry["path"],
        "title": entry.get("title") or Path(entry["path"]).stem,
        "created_at": entry.get("created_at"),
        "url_path": url_path,
        "url": vault_share.public_share_url(token),
    }


@api_router.get("/shares")
def list_shares(request: Request) -> dict:
    require_user_id(request)
    items = vault_share.list_shares()
    return {"ok": True, "count": len(items), "shares": items}


class DeleteShareBody(BaseModel):
    token: str = Field(..., min_length=16, max_length=64)


@api_router.post("/share/delete")
def delete_share(request: Request, body: DeleteShareBody) -> dict:
    require_user_id(request)
    ok = vault_share.delete_share(body.token)
    if not ok:
        raise HTTPException(status_code=404, detail="Share not found")
    return {"ok": True, "token": body.token}


@public_router.get("/{token}")
def view_share(token: str) -> HTMLResponse:
    entry = vault_share.get_share(token, refresh=True)
    if not entry:
        raise HTTPException(status_code=404, detail="Share not found")
    note_path = entry.get("path") or ""
    text = vault_share.read_vault_text(note_path)
    if text is None:
        # Note deleted/moved away — drop dangling share so Shared List stays clean.
        try:
            vault_share.revoke_share_if_missing(token, note_path)
        except Exception:
            pass
        raise HTTPException(status_code=404, detail="Shared note no longer exists")
    text = vault_share.rewrite_md_assets_for_share(text, token)
    title = entry.get("title") or Path(note_path).stem
    page = viewer_html.build_markdown_viewer_page(
        title,
        text,
        topbar_right_html='<span style="color:#8b949e;font-size:12px">Public share</span>',
    )
    return HTMLResponse(
        content=page,
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "private, no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
            "X-Robots-Tag": "noindex",
        },
    )


@public_router.get("/{token}/raw")
def share_raw_asset(token: str, path: str):
    entry = vault_share.get_share(token, refresh=True)
    if not entry:
        raise HTTPException(status_code=404, detail="Share not found")
    note_path = entry.get("path") or ""
    rel = _share_asset_rel(note_path, path)
    headers = {
        "Cache-Control": "private, no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
    }
    try:
        target = vault_backend.resolve_vault_path(rel)
        if target.is_file():
            media, _ = mimetypes.guess_type(str(target))
            return FileResponse(
                target,
                media_type=media or "application/octet-stream",
                headers=headers,
            )
    except ValueError:
        pass
    data = vault_share.read_vault_bytes(rel)
    if data is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    media, _ = mimetypes.guess_type(rel)
    return Response(
        content=data,
        media_type=media or "application/octet-stream",
        headers=headers,
    )
