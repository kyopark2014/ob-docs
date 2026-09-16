"""Wiki-link knowledge graph API."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from application.api.routes_auth import require_user_id
from application import vault_index

router = APIRouter(prefix="/vault/api/graph", tags=["graph"])


@router.get("")
def get_graph(request: Request):
    require_user_id(request)
    return vault_index.get_graph()


@router.post("/rebuild")
def rebuild(request: Request):
    require_user_id(request)
    stats = vault_index.rebuild_index()
    return {"ok": True, **stats}


@router.get("/backlinks")
def backlinks(request: Request, path: str = Query(..., min_length=1)):
    require_user_id(request)
    return {"path": path, "backlinks": vault_index.backlinks(path)}
