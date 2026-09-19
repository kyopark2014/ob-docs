"""Notes (vault markdown) knowledge graph API.

Wiki-link JSON for skills + Notes Sync/Rebuild/Graph/Configure (Wiki UX, Notes labels).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from application import notes_graph, vault_backend, vault_index
from application.api.routes_auth import require_user_id

router = APIRouter(prefix="/api/graph", tags=["graph"])


class NotesPatternPatch(BaseModel):
    pattern: str = Field(..., min_length=1, max_length=32)


class NotesSourcesPut(BaseModel):
    folders: list[str] = Field(default_factory=list, max_length=20)
    include_missing: bool | None = None


class NotesQueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    mode: str = Field(default="bfs", max_length=8)
    budget: int = Field(default=2000, ge=200, le=8000)


@router.get("")
def get_graph(request: Request):
    """Skill-compatible wiki-link graph JSON (filtered by Notes Configure folders)."""
    require_user_id(request)
    return notes_graph.build_notes_graph()


@router.get("/status")
def graph_status(request: Request) -> dict[str, Any]:
    require_user_id(request)
    return notes_graph.status_payload()


@router.post("/sync")
def sync_notes_graph(
    request: Request,
    full: bool = Query(False),
) -> dict[str, Any]:
    """Queue Notes graph Sync (full=true → Rebuild)."""
    require_user_id(request)
    job = notes_graph.ensure_sync(full=full)
    return {**notes_graph.status_payload(), **job}


@router.post("/rebuild")
def rebuild(request: Request):
    """Immediate full rebuild (legacy) + enqueue Notes Rebuild semantics."""
    require_user_id(request)
    # Keep sync response shape for UI; also return ok/notes/links after kickoff
    job = notes_graph.ensure_sync(full=True)
    payload = notes_graph.status_payload()
    return {"ok": True, "notes": 0, "links": 0, **payload, **job}


@router.patch("/pattern")
def patch_pattern(body: NotesPatternPatch, request: Request) -> dict[str, Any]:
    require_user_id(request)
    if not notes_graph.graph_json_exists():
        raise HTTPException(
            status_code=404,
            detail="Notes 그래프가 아직 없습니다. 좌측 Graph → Sync를 실행하세요.",
        )
    pid = notes_graph.set_pattern(body.pattern)
    try:
        ok = notes_graph.republish_html(pattern=pid)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Notes 그래프 패턴 전환 실패: {exc}",
        ) from exc
    if not ok:
        raise HTTPException(
            status_code=500,
            detail="Notes 그래프 HTML을 생성하지 못했습니다.",
        )
    return {**notes_graph.status_payload(), "pattern": pid, "status": "ready"}


@router.get("/sources")
def get_sources(request: Request) -> dict[str, Any]:
    require_user_id(request)
    cfg = notes_graph.load_config()
    return {
        "notes_dir": str(vault_backend.vault_root()),
        "folders": cfg.get("folders") or [],
        "include_missing": bool(cfg.get("include_missing", True)),
        "available_folders": notes_graph.list_vault_folders(),
        "max_sources": 20,
    }


@router.put("/sources")
def put_sources(body: NotesSourcesPut, request: Request) -> dict[str, Any]:
    require_user_id(request)
    cfg = notes_graph.save_config(
        folders=list(body.folders or []),
        include_missing=body.include_missing,
    )
    return {
        "notes_dir": notes_graph.status_payload()["notes_dir"],
        "folders": cfg.get("folders") or [],
        "include_missing": bool(cfg.get("include_missing", True)),
        "available_folders": notes_graph.list_vault_folders(),
        "max_sources": 20,
    }


@router.get("/graph")
def get_notes_graph_html(request: Request):
    """Serve Notes Graph HTML (iframe target)."""
    require_user_id(request)
    path = notes_graph.ensure_html_current()
    if path is None or not path.is_file():
        job = notes_graph.get_job_status()
        status = job.get("status") or "idle"
        if status in ("queued", "running"):
            detail = "Notes 그래프를 동기화하는 중입니다. 잠시 후 다시 열어 주세요."
        elif status == "error":
            detail = f"Notes 동기화에 실패했습니다: {job.get('error') or 'unknown error'}"
        else:
            detail = (
                "Notes 그래프가 아직 없습니다. 좌측 Graph → Sync로 "
                "먼저 동기화하세요."
            )
        return HTMLResponse(
            "<!doctype html><html lang='ko'><head><meta charset='UTF-8' />"
            "<title>Notes Graph</title></head><body style='"
            "font-family:system-ui,sans-serif;background:#0d1117;color:#e6edf3;"
            "padding:48px;max-width:640px;margin:0 auto;'>"
            "<h1>Notes Graph 없음</h1>"
            f"<p>{detail}</p>"
            "</body></html>",
            status_code=404,
        )
    return FileResponse(
        path,
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": "inline",
        },
    )


@router.get("/backlinks")
def backlinks(request: Request, path: str = Query(..., min_length=1)):
    require_user_id(request)
    return {"path": path, "backlinks": vault_index.backlinks(path)}


@router.post("/query")
def query_notes_graph(body: NotesQueryRequest, request: Request) -> dict[str, Any]:
    """BFS/DFS + source excerpts over Notes graph (agentic-work Ask/Search)."""
    require_user_id(request)
    if not notes_graph.graph_json_exists():
        raise HTTPException(
            status_code=404,
            detail="Notes 그래프가 아직 없습니다. 좌측 Graph → Sync를 실행하세요.",
        )
    try:
        return notes_graph.query_notes_graph(
            body.question,
            mode=body.mode,
            budget=body.budget,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"notes query failed: {exc}",
        ) from exc
