"""Full-text + metadata search over the vault index."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from application.api.routes_auth import require_user_id
from application import vault_index

router = APIRouter(prefix="/api/search", tags=["search"])


@router.get("")
def search(request: Request, q: str = Query("", max_length=200), limit: int = Query(50, ge=1, le=200)):
    require_user_id(request)
    return {"query": q, "results": vault_index.search(q, limit=limit)}


@router.get("/resolve")
def resolve_wiki_link(
    request: Request,
    target: str = Query(..., min_length=1, max_length=500),
    from_path: str | None = Query(None, alias="from", max_length=500),
):
    """Resolve [[wiki]] target to a vault note path (Obsidian-like)."""
    require_user_id(request)
    path = vault_index.resolve_link(target, from_path=from_path or None)
    return {"target": target, "from": from_path, "path": path}
