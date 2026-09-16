"""Vault file tree CRUD."""

from __future__ import annotations

import mimetypes
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from application.api.routes_auth import require_user_id
from application import vault_backend, vault_index

router = APIRouter(prefix="/vault/api/files", tags=["files"])

HIDDEN_SKIP = {".git"}
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}


class WriteBody(BaseModel):
    path: str = Field(..., min_length=1, max_length=1024)
    content: str = ""


class AppendBody(BaseModel):
    path: str = Field(..., min_length=1, max_length=1024)
    content: str = ""
    create: bool = True
    separator: str = "\n"


class MkdirBody(BaseModel):
    path: str = Field(..., min_length=1, max_length=1024)


class RenameBody(BaseModel):
    from_path: str = Field(..., min_length=1, max_length=1024)
    to_path: str = Field(..., min_length=1, max_length=1024)


class DeleteBody(BaseModel):
    path: str = Field(..., min_length=1, max_length=1024)


class DuplicateBody(BaseModel):
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


@router.get("/list")
def list_files(
    request: Request,
    prefix: str = "",
    ext: str = "md",
) -> dict:
    """Flat list of vault files (default: ``*.md``). Use ``ext=*`` for all files."""
    require_user_id(request)
    if vault_backend.backend_mode() == "s3":
        vault_backend.sync_from_s3()
    root = vault_backend.vault_root()
    cleaned = (prefix or "").replace("\\", "/").strip("/")
    if ".." in cleaned.split("/"):
        raise HTTPException(status_code=400, detail="Path traversal is not allowed")
    base = root / cleaned if cleaned else root
    if not base.exists():
        return {"prefix": cleaned, "ext": ext, "files": []}
    if base.is_file():
        rel = base.relative_to(root).as_posix()
        return {
            "prefix": cleaned,
            "ext": ext,
            "files": [
                {
                    "path": rel,
                    "name": base.name,
                    "size": base.stat().st_size,
                    "mtime": base.stat().st_mtime,
                }
            ],
        }

    want_ext = (ext or "md").lower().lstrip(".")
    all_ext = want_ext in {"*", "all", ""}
    files: list[dict[str, Any]] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        try:
            rel_parts = path.relative_to(root).parts
        except ValueError:
            continue
        if any(part in HIDDEN_SKIP or part == ".vault" for part in rel_parts):
            continue
        if not all_ext and path.suffix.lower().lstrip(".") != want_ext:
            continue
        rel = path.relative_to(root).as_posix()
        st = path.stat()
        files.append(
            {
                "path": rel,
                "name": path.name,
                "size": st.st_size,
                "mtime": st.st_mtime,
            }
        )
    return {"prefix": cleaned, "ext": ext, "count": len(files), "files": files}


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


@router.post("/upload")
async def upload_file(
    request: Request,
    path: str = Form(..., min_length=1, max_length=1024),
    file: UploadFile = File(...),
) -> dict:
    """Save a binary file (e.g. pasted image) into the vault."""
    require_user_id(request)
    try:
        target = vault_backend.resolve_vault_path(path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    content_type = (file.content_type or "").split(";")[0].strip().lower()
    suffix = Path(path).suffix.lower()
    if content_type and content_type not in ALLOWED_IMAGE_TYPES and not suffix:
        raise HTTPException(status_code=400, detail=f"Unsupported content type: {content_type}")
    if content_type in ALLOWED_IMAGE_TYPES and suffix not in ALLOWED_IMAGE_TYPES.values():
        # keep requested path suffix if present; otherwise require image extension
        pass
    if suffix and suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".md", ".txt"}:
        # allow common image + text; reject unknown binaries for now
        if content_type not in ALLOWED_IMAGE_TYPES and not content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix}")

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 15MB)")
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    if vault_backend.backend_mode() == "s3":
        vault_backend.sync_to_s3(path)
    return {
        "ok": True,
        "path": path,
        "size": len(data),
        "content_type": content_type or mimetypes.guess_type(str(target))[0],
    }


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


@router.post("/append")
def append_file(request: Request, body: AppendBody) -> dict:
    """Append text to a note (create parent dirs / file when ``create=true``)."""
    require_user_id(request)
    try:
        target = vault_backend.resolve_vault_path(body.path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if target.exists() and not target.is_file():
        raise HTTPException(status_code=400, detail="Path is a directory")
    if not target.exists():
        if not body.create:
            raise HTTPException(status_code=404, detail="File not found")
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = ""
    else:
        existing = target.read_text(encoding="utf-8", errors="replace")
    chunk = body.content or ""
    if existing and chunk and not existing.endswith(("\n", "\r")):
        sep = body.separator if body.separator is not None else "\n"
        content = existing + sep + chunk
    elif existing and chunk:
        content = existing + chunk
    else:
        content = existing + chunk
    target.write_text(content, encoding="utf-8")
    if target.suffix.lower() == ".md":
        vault_index.update_note(body.path)
        vault_index.rebuild_index()
    if vault_backend.backend_mode() == "s3":
        vault_backend.sync_to_s3(body.path)
    meta = vault_index.get_meta(body.path)
    return {
        "ok": True,
        "path": body.path,
        "appended": len(chunk),
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
    if vault_backend.backend_mode() == "s3":
        vault_backend.sync_to_s3()
    return {"ok": True, "path": body.path}


def _unique_copy_path(src: Path) -> Path:
    """Return sibling path like 'name copy' / 'name copy 2'."""
    parent = src.parent
    stem = src.name
    candidate = parent / f"{stem} copy"
    n = 2
    while candidate.exists():
        candidate = parent / f"{stem} copy {n}"
        n += 1
    return candidate


@router.post("/duplicate")
def duplicate_path(request: Request, body: DuplicateBody) -> dict:
    require_user_id(request)
    try:
        src = vault_backend.resolve_vault_path(body.path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not src.exists():
        raise HTTPException(status_code=404, detail="Not found")
    dst = _unique_copy_path(src)
    root = vault_backend.vault_root()
    if root != dst and root not in dst.parents:
        raise HTTPException(status_code=400, detail="Invalid destination")
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)
    vault_index.rebuild_index()
    if vault_backend.backend_mode() == "s3":
        vault_backend.sync_to_s3()
    rel = dst.relative_to(root).as_posix()
    return {"ok": True, "from": body.path, "to": rel}


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
