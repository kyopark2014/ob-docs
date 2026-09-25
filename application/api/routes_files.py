"""Vault file tree CRUD."""

from __future__ import annotations

import logging
import mimetypes
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

from application.api.routes_auth import require_user_id
from application import notes_db, vault_backend, vault_index, vault_order, vault_share, vault_sync, viewer_html

logger = logging.getLogger("routes_files")

router = APIRouter(prefix="/api/files", tags=["files"])

HIDDEN_SKIP = {".git", ".keep", ".gitkeep"}
# Empty folders need a marker object so they survive S3 sync / tree rebuild.
FOLDER_KEEP_NAME = ".keep"
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
TEXT_VIEWER_EXTENSIONS = {
    ".md",
    ".markdown",
    ".txt",
    ".csv",
    ".json",
    ".yaml",
    ".yml",
    ".xml",
    ".html",
    ".htm",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".rst",
}
INLINE_BINARY_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".pdf",
}
TEXT_VIEWER_MAX_BYTES = 2 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}
ALLOWED_UPLOAD_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".md",
    ".markdown",
    ".txt",
    ".csv",
    ".json",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".yml",
    ".yaml",
    ".xml",
    ".html",
    ".htm",
    ".rst",
    ".dxf",
    ".pdf",
    ".doc",
    ".docx",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
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


class ReorderBody(BaseModel):
    folder: str = Field("", max_length=1024, description="Parent folder path; empty = vault root")
    names: list[str] = Field(..., min_length=1, max_length=2000)


def _tree_node(path: Path, root: Path) -> dict[str, Any]:
    rel = path.relative_to(root).as_posix()
    if path.is_dir():
        children = []
        for child in path.iterdir():
            if child.name in HIDDEN_SKIP:
                continue
            if child.name == ".vault":
                continue
            children.append(_tree_node(child, root))
        children = vault_order.apply_order(rel, children)
        return {"name": path.name, "path": rel, "type": "folder", "children": children}
    node: dict[str, Any] = {
        "name": path.name,
        "path": rel,
        "type": "file",
        "ext": path.suffix.lower().lstrip("."),
    }
    if path.suffix.lower() == ".md":
        row = notes_db.get_by_path(rel)
        if row:
            node["note_id"] = row["note_id"]
            node["title"] = row["title"]
            node["size_bytes"] = row["size_bytes"]
            node["created_at"] = row["created_at"]
            node["updated_at"] = row["updated_at"]
    return node


@router.get("/tree")
def get_tree(request: Request) -> dict:
    require_user_id(request)
    notes_db.ensure_db()
    mode = vault_backend.backend_mode()
    if mode == "s3":
        # Tree from S3 object keys only — never await sync/flush here.
        # A stuck S3 sync was freezing the UI on "Loading vault…".
        try:
            rels = vault_sync.list_remote_vault_rels()
            children = vault_sync.build_tree_from_rels(rels)
            return {
                "root": ".",
                "mode": mode,
                "source": "s3",
                "children": children,
            }
        except Exception:
            logger.exception("S3 tree list failed; falling back to local disk")
    # Keep SQLite registry aligned before serving the local tree.
    try:
        notes_db.ensure_db()
        if notes_db.is_empty():
            notes_db.sync_from_filesystem()
    except Exception:
        logger.exception("notes_db sync before tree failed")
    root = vault_backend.vault_root()
    children = []
    for child in root.iterdir():
        if child.name in HIDDEN_SKIP or child.name == ".vault":
            continue
        children.append(_tree_node(child, root))
    children = vault_order.apply_order("", children)
    return {
        "root": ".",
        "mode": mode,
        "source": "local" if mode != "s3" else "local-fallback",
        "children": children,
    }


@router.get("/notes")
def list_notes_registry(request: Request) -> dict:
    """List all markdown notes tracked in the per-user SQLite registry."""
    require_user_id(request)
    try:
        notes_db.sync_from_filesystem()
    except Exception:
        logger.exception("notes_db sync before list failed")
    notes = notes_db.list_notes(order="updated_at")
    return {"count": len(notes), "notes": notes}


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
        item: dict[str, Any] = {
            "path": rel,
            "name": path.name,
            "size": st.st_size,
            "mtime": st.st_mtime,
        }
        if path.suffix.lower() == ".md":
            row = notes_db.get_by_path(rel) or notes_db.ensure_note_for_path(rel)
            if row:
                item["note_id"] = row["note_id"]
                item["title"] = row["title"]
                item["size_bytes"] = row["size_bytes"]
                item["created_at"] = row["created_at"]
                item["updated_at"] = row["updated_at"]
        files.append(item)
    return {"prefix": cleaned, "ext": ext, "count": len(files), "files": files}


def _forget_missing_note(path: str) -> None:
    """Drop registry / index entries when a note is gone locally and on S3."""
    cleaned = (path or "").replace("\\", "/").lstrip("/")
    if not cleaned:
        return
    try:
        notes_db.on_note_deleted(cleaned)
    except Exception:
        logger.exception("notes_db cleanup failed for missing %s", cleaned)
    try:
        vault_index.remove_note(cleaned)
    except Exception:
        logger.exception("vault_index cleanup failed for missing %s", cleaned)
    try:
        vault_order.notify_deleted(cleaned)
    except Exception:
        logger.debug("vault_order cleanup skipped for %s", cleaned, exc_info=True)


def _require_local_file(path: str) -> Path:
    """Resolve ``path`` to an on-disk file, pulling from S3 on demand.

    If the file is missing both locally and remotely, purge stale note metadata
    and raise 404 so the UI can drop tabs/pins for ghost entries.
    """
    target = vault_sync.ensure_local_file(path)
    if target is not None and target.is_file():
        return target
    purged = False
    if notes_db.is_markdown_path(path) and not vault_sync.remote_file_exists(path):
        _forget_missing_note(path)
        purged = True
    raise HTTPException(
        status_code=404,
        detail={"error": "file_not_found", "path": path, "purged": purged},
    )


@router.get("/read")
def read_file(request: Request, path: str) -> dict:
    require_user_id(request)
    try:
        target = _require_local_file(path)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if target.suffix.lower() not in {".md", ".txt", ".json", ".csv", ".yaml", ".yml"}:
        raise HTTPException(status_code=400, detail="Use /raw for binary files")
    content = target.read_text(encoding="utf-8", errors="replace")
    # Prefer the on-disk relative path (handles NFC/NFD spelling differences).
    try:
        rel = target.resolve().relative_to(vault_backend.vault_root().resolve()).as_posix()
    except Exception:
        rel = path
    meta = vault_index.get_meta(rel) if target.suffix.lower() == ".md" else None
    backlinks = vault_index.backlinks(rel) if meta else []
    note_row = (
        notes_db.ensure_note_for_path(rel) if target.suffix.lower() == ".md" else None
    )
    return {
        "path": rel,
        "content": content,
        "word_count": meta.word_count if meta else len(content.split()),
        "char_count": meta.char_count if meta else len(content),
        "backlinks": backlinks,
        "title": (note_row or {}).get("title")
        or (meta.title if meta else Path(rel).stem),
        "tags": meta.tags if meta else [],
        "note_id": (note_row or {}).get("note_id"),
        "size_bytes": (note_row or {}).get("size_bytes", target.stat().st_size),
        "created_at": (note_row or {}).get("created_at"),
        "updated_at": (note_row or {}).get("updated_at"),
    }


@router.get("/raw")
def raw_file(request: Request, path: str) -> Response:
    require_user_id(request)
    target = _require_local_file(path)
    media, _ = mimetypes.guess_type(str(target))
    return FileResponse(target, media_type=media or "application/octet-stream")


@router.get("/view")
def view_vault_file(
    request: Request,
    path: str = Query(..., min_length=1, max_length=1024),
    download: int = Query(0),
):
    """Open a vault attachment in a new browser tab (agentic-work Load-files viewer).

    Markdown/text → HTML viewer; images/PDF → inline; other → download.
    """
    require_user_id(request)
    try:
        target = _require_local_file(path)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    name = target.name
    ext = target.suffix.lower()
    force_download = bool(download)
    encoded = quote(path, safe="")
    download_href = f"/api/files/view?path={encoded}&download=1"

    if force_download or (
        ext not in TEXT_VIEWER_EXTENSIONS and ext not in INLINE_BINARY_EXTENSIONS
    ):
        media, _ = mimetypes.guess_type(str(target))
        return FileResponse(
            target,
            media_type=media or "application/octet-stream",
            filename=name,
            content_disposition_type="attachment",
        )

    if ext in INLINE_BINARY_EXTENSIONS:
        media, _ = mimetypes.guess_type(str(target))
        return FileResponse(
            target,
            media_type=media or "application/octet-stream",
            filename=name,
            content_disposition_type="inline",
        )

    data = target.read_bytes()
    if len(data) > TEXT_VIEWER_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large for viewer (max {TEXT_VIEWER_MAX_BYTES} bytes)",
        )
    text = data.decode("utf-8", errors="replace")
    as_markdown = ext in {".md", ".markdown"}
    page = viewer_html.build_text_viewer_page(
        name,
        text,
        as_markdown=as_markdown,
        download_href=download_href,
    )
    return HTMLResponse(content=page, media_type="text/html; charset=utf-8")


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
    if suffix and suffix not in ALLOWED_UPLOAD_SUFFIXES:
        if content_type not in ALLOWED_IMAGE_TYPES and not content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix}")

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 15MB)")
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    note_row = None
    if notes_db.is_markdown_path(path):
        try:
            text = data.decode("utf-8", errors="replace")
            note_row = notes_db.on_note_written(path, content=text)
            vault_index.update_note(path)
            vault_index.rebuild_index()
        except Exception:
            logger.exception("notes_db upsert after upload failed for %s", path)
    if vault_backend.backend_mode() == "s3":
        vault_backend.sync_to_s3(path)
    return {
        "ok": True,
        "path": path,
        "size": len(data),
        "content_type": content_type or mimetypes.guess_type(str(target))[0],
        "note_id": (note_row or {}).get("note_id"),
        "created": (note_row or {}).get("created"),
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
    note_row = None
    if notes_db.is_markdown_path(body.path):
        note_row = notes_db.on_note_written(body.path, content=body.content)
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
        "note_id": (note_row or {}).get("note_id"),
        "title": (note_row or {}).get("title"),
        "size_bytes": (note_row or {}).get("size_bytes"),
        "created_at": (note_row or {}).get("created_at"),
        "updated_at": (note_row or {}).get("updated_at"),
        "created": (note_row or {}).get("created"),
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
    note_row = None
    if notes_db.is_markdown_path(body.path):
        note_row = notes_db.on_note_written(body.path, content=content)
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
        "note_id": (note_row or {}).get("note_id"),
        "title": (note_row or {}).get("title"),
        "size_bytes": (note_row or {}).get("size_bytes"),
        "created_at": (note_row or {}).get("created_at"),
        "updated_at": (note_row or {}).get("updated_at"),
        "created": (note_row or {}).get("created"),
    }


@router.post("/mkdir")
def mkdir(request: Request, body: MkdirBody) -> dict:
    require_user_id(request)
    try:
        target = vault_backend.resolve_vault_path(body.path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    target.mkdir(parents=True, exist_ok=True)
    # S3 has no empty directories: a marker file keeps the folder in the tree
    # after sync_from_s3 rebuilds from object keys.
    keep = target / FOLDER_KEEP_NAME
    if not keep.exists():
        keep.write_text("", encoding="utf-8")
    keep_rel = f"{body.path.rstrip('/')}/{FOLDER_KEEP_NAME}"
    if vault_backend.backend_mode() == "s3":
        vault_backend.sync_to_s3(keep_rel)
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
    was_file = src.is_file()
    from_name = Path(body.from_path).name
    to_name = Path(body.to_path).name
    if vault_backend.backend_mode() == "s3":
        # Queue deletes for old keys before the local move.
        vault_sync.enqueue_delete_tree(body.from_path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Case-only rename (agent → Agent) needs a temp hop on case-insensitive disks.
    if from_name.lower() == to_name.lower() and from_name != to_name:
        import uuid as _uuid

        tmp = src.with_name(f".__rename_{_uuid.uuid4().hex[:10]}")
        src.rename(tmp)
        tmp.rename(src.parent / to_name)
    else:
        shutil.move(str(src), str(dst))
    notes_db.on_note_renamed(body.from_path, body.to_path)
    if notes_db.is_markdown_path(body.from_path):
        vault_index.remove_note(body.from_path)
    if notes_db.is_markdown_path(body.to_path):
        # Index only — DB path already updated by on_note_renamed (preserves note_id).
        vault_index.update_note(body.to_path)
    vault_index.rebuild_index()
    # Keep public shares pointing at the new path and republish content to S3.
    vault_share.rewrite_share_paths(body.from_path, body.to_path)
    try:
        if was_file:
            vault_share.publish_vault_file_to_s3(body.to_path)
            vault_share.delete_vault_from_s3(body.from_path)
        else:
            vault_share.delete_vault_tree_from_s3(body.from_path)
            if vault_backend.backend_mode() == "s3":
                vault_sync.enqueue_put_tree(body.to_path)
            else:
                root = vault_backend.vault_root()
                target = vault_backend.resolve_vault_path(body.to_path)
                if target.is_dir():
                    for p in target.rglob("*.md"):
                        rel = p.resolve().relative_to(root.resolve()).as_posix()
                        vault_share.publish_vault_file_to_s3(rel)
    except Exception:
        pass
    if vault_backend.backend_mode() == "s3":
        vault_sync.enqueue_put_tree(body.to_path)
        vault_sync.schedule_flush_pending(reconcile_casing=True)
    vault_order.notify_renamed(body.from_path, body.to_path)
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
    # Revoke public shares before removing the note so Shared List / CloudFront
    # cannot keep a dangling token → "Shared note no longer exists".
    vault_share.remove_shares_for_path(body.path)
    if vault_backend.backend_mode() == "s3":
        vault_sync.enqueue_delete_tree(body.path)
    else:
        # Mount/local with bucket: still drop S3 objects so share fallback cannot resurrect.
        try:
            vault_share.delete_vault_tree_from_s3(body.path)
        except Exception:
            pass
    if target.is_dir():
        shutil.rmtree(target)
        notes_db.on_note_deleted(body.path)
    else:
        target.unlink()
        if notes_db.is_markdown_path(body.path):
            vault_index.remove_note(body.path)
            notes_db.on_note_deleted(body.path)
    vault_index.rebuild_index()
    vault_order.notify_deleted(body.path)
    if vault_backend.backend_mode() == "s3":
        vault_sync.schedule_flush_pending()
    return {"ok": True, "path": body.path}


@router.put("/order")
def reorder_folder(request: Request, body: ReorderBody) -> dict:
    """Persist custom sibling order for a folder (same-folder drag reorder)."""
    require_user_id(request)
    folder = (body.folder or "").replace("\\", "/").strip("/")
    if folder:
        try:
            target = vault_backend.resolve_vault_path(folder)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if not target.is_dir():
            raise HTTPException(status_code=404, detail="Folder not found")
    names = vault_order.set_order(folder, body.names)
    return {"ok": True, "folder": folder, "names": names}


def _unique_copy_path(src: Path) -> Path:
    """Return sibling path like 'Name 2.md' / 'Name 3' (folders)."""
    parent = src.parent
    if src.is_dir():
        base = src.name
        suffix = ""
    else:
        base = src.stem
        suffix = src.suffix
    n = 2
    while True:
        candidate = parent / f"{base} {n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


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
    rel = dst.relative_to(root).as_posix()
    if notes_db.is_markdown_path(rel):
        # Duplicate gets a fresh note_id (new registration).
        notes_db.on_note_written(rel)
    elif dst.is_dir():
        notes_db.sync_from_filesystem()
    if vault_backend.backend_mode() == "s3":
        vault_sync.enqueue_put_tree(rel)
        vault_sync.schedule_flush_pending()
    return {"ok": True, "from": body.path, "to": rel}


@router.get("/sync")
def sync_status(request: Request) -> dict:
    """Pending outbound ops + background sync job status (for SyncProgressModal)."""
    require_user_id(request)
    return vault_sync.get_sync_status()


@router.post("/sync")
def sync_from_remote(request: Request) -> dict:
    """Start background sync: flush pending local→S3, then incremental pull."""
    require_user_id(request)
    if vault_backend.backend_mode() != "s3":
        return {
            "ok": False,
            "status": "error",
            "busy": False,
            "mode": vault_backend.backend_mode(),
            "message": (
                "로컬 모드에서는 S3 동기화를 사용할 수 없습니다. "
                "VAULT_S3_ENABLE=1 로 실행한 뒤 다시 시도하세요."
            ),
        }
    return vault_sync.start_sync_job(force_download=False)


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
