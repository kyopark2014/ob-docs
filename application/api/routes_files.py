"""Vault file tree CRUD."""

from __future__ import annotations

import mimetypes
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from application.api.routes_auth import require_user_id
from application import vault_backend, vault_index

router = APIRouter(prefix="/vault/api/files", tags=["files"])

HIDDEN_SKIP = {".git"}


class WriteBody(BaseModel):
    path: str = Field(..., min_length=1, max_length=1024)
    content: str = ""


class MkdirBody(BaseModel):
    path: str = Field(..., min_length=1, max_length=1024)


class RenameBody(BaseModel):
    from_path: str = Field(..., min_length=1, max_length=1024)
    to_path: str = Field(..., min_length=1, max_length=1024)


class DeleteBody(BaseModel):
    path: str = Field(..., min_length=1, max_length=1024)


def _tree_node(path: Path, root: Path) -> dict[str, Any]:
    rel = path.relative_to(root).as_posix()
    if path.is_dir():
        children = []
        for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if child.name in HIDDEN_SKIP:
                continue
            if child.name == ".vault":
                continue
            children.append(_tree_node(child, root))
        return {"name": path.name, "path": rel, "type": "folder", "children": children}
    return {
        "name": path.name,
        "path": rel,
        "type": "file",
        "ext": path.suffix.lower().lstrip("."),
    }


@router.get("/tree")
def get_tree(request: Request) -> dict:
    require_user_id(request)
    if vault_backend.backend_mode() == "s3":
        vault_backend.sync_from_s3()
    root = vault_backend.vault_root()
    children = []
    for child in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if child.name in HIDDEN_SKIP or child.name == ".vault":
            continue
        children.append(_tree_node(child, root))
    return {
        "root": ".",
        "mode": vault_backend.backend_mode(),
        "children": children,
    }


@router.get("/read")
def read_file(request: Request, path: str) -> dict:
    require_user_id(request)
    try:
        target = vault_backend.resolve_vault_path(path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if target.suffix.lower() not in {".md", ".txt", ".json", ".csv", ".yaml", ".yml"}:
        raise HTTPException(status_code=400, detail="Use /raw for binary files")
    content = target.read_text(encoding="utf-8", errors="replace")
    meta = vault_index.get_meta(path) if target.suffix.lower() == ".md" else None
    backlinks = vault_index.backlinks(path) if meta else []
    return {
        "path": path,
        "content": content,
        "word_count": meta.word_count if meta else len(content.split()),
        "char_count": meta.char_count if meta else len(content),
        "backlinks": backlinks,
        "title": meta.title if meta else Path(path).stem,
        "tags": meta.tags if meta else [],
    }


@router.get("/raw")
def raw_file(request: Request, path: str) -> Response:
    require_user_id(request)
    try:
        target = vault_backend.resolve_vault_path(path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    media, _ = mimetypes.guess_type(str(target))
    return FileResponse(target, media_type=media or "application/octet-stream")


@router.put("/write")
def write_file(request: Request, body: WriteBody) -> dict:
    require_user_id(request)
    try:
        target = vault_backend.resolve_vault_path(body.path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if target.suffix.lower() == "" and not body.path.endswith(".md"):
        # allow writing dirs? no
        pass
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body.content, encoding="utf-8")
    if target.suffix.lower() == ".md":
        vault_index.update_note(body.path)
        vault_index.rebuild_index()
    if vault_backend.backend_mode() == "s3":
        vault_backend.sync_to_s3(body.path)
    meta = vault_index.get_meta(body.path)
    return {
        "ok": True,
        "path": body.path,
        "word_count": meta.word_count if meta else None,
        "char_count": meta.char_count if meta else None,
    }


@router.post("/mkdir")
def mkdir(request: Request, body: MkdirBody) -> dict:
    require_user_id(request)
    try:
        target = vault_backend.resolve_vault_path(body.path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    target.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "path": body.path}


@router.post("/rename")
def rename(request: Request, body: RenameBody) -> dict:
    require_user_id(request)
    try:
        src = vault_backend.resolve_vault_path(body.from_path)
        dst = vault_backend.resolve_vault_path(body.to_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not src.exists():
        raise HTTPException(status_code=404, detail="Source not found")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    if body.from_path.endswith(".md"):
        vault_index.remove_note(body.from_path)
    if body.to_path.endswith(".md"):
        vault_index.update_note(body.to_path)
    vault_index.rebuild_index()
    if vault_backend.backend_mode() == "s3":
        vault_backend.sync_to_s3()
    return {"ok": True, "from": body.from_path, "to": body.to_path}


@router.post("/delete")
def delete_path(request: Request, body: DeleteBody) -> dict:
    require_user_id(request)
    try:
        target = vault_backend.resolve_vault_path(body.path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
        if body.path.endswith(".md"):
            vault_index.remove_note(body.path)
    vault_index.rebuild_index()
    return {"ok": True, "path": body.path}


@router.get("/settings/{name}")
def get_settings(request: Request, name: str) -> Any:
    require_user_id(request)
    safe = name.replace("..", "").replace("/", "")
    if not safe.endswith(".json"):
        safe += ".json"
    path = vault_backend.settings_dir() / safe
    if not path.is_file():
        return {}
    import json

    return json.loads(path.read_text(encoding="utf-8"))


@router.put("/settings/{name}")
def put_settings(request: Request, name: str, body: dict) -> dict:
    require_user_id(request)
    import json

    safe = name.replace("..", "").replace("/", "")
    if not safe.endswith(".json"):
        safe += ".json"
    # workspace is ephemeral; app.json is allowed
    path = vault_backend.settings_dir() / safe
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "path": f".vault/{safe}"}
