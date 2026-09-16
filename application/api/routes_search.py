"""Full-text + metadata search over the vault index."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from application.api.routes_auth import require_user_id
from application import vault_index

router = APIRouter(prefix="/vault/api/search", tags=["search"])


@router.get("")
def search(request: Request, q: str = Query("", max_length=200), limit: int = Query(50, ge=1, le=200)):
    require_user_id(request)
    return {"query": q, "results": vault_index.search(q, limit=limit)}
