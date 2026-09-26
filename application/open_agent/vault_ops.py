"""Vault read/write helpers shared by Open Agent tools and routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from application import notes_db, vault_backend, vault_index


def read_note(path: str) -> tuple[str, int]:
    from application import vault_sync

    target = vault_sync.ensure_local_file(path)
    if target is None or not target.is_file():
        raise FileNotFoundError(f"Note not found: {path}")
    content = target.read_text(encoding="utf-8", errors="replace")
    return content, target.stat().st_size


def apply_vault_write(path: str, content: str) -> dict[str, Any]:
    """Write a markdown/text note and sync index + S3."""
    try:
        target = vault_backend.resolve_vault_path(path)
    except ValueError as e:
        raise ValueError(str(e)) from e
    if target.suffix.lower() not in {".md", ".txt", ".markdown"}:
        raise ValueError(f"Only markdown/text notes can be written: {path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    note_row = None
    if target.suffix.lower() in {".md", ".markdown"}:
        note_row = notes_db.on_note_written(path, content=content)
        vault_index.update_note(path)
        vault_index.rebuild_index()
    if vault_backend.backend_mode() == "s3":
        vault_backend.sync_to_s3(path)
    return {
        "path": path,
        "bytes": len(content.encode("utf-8")),
        "note_id": (note_row or {}).get("note_id"),
        "created": (note_row or {}).get("created"),
    }


def list_vault(prefix: str = "", *, limit: int = 200) -> list[dict[str, Any]]:
    """Flat list of vault files under optional prefix."""
    root = vault_backend.vault_root()
    base = root
    cleaned = (prefix or "").replace("\\", "/").strip("/")
    if cleaned:
        base = vault_backend.resolve_vault_path(cleaned)
    if not base.exists():
        return []
    out: list[dict[str, Any]] = []
    if base.is_file():
        rel = str(base.relative_to(root)).replace("\\", "/")
        out.append({"path": rel, "type": "file", "size": base.stat().st_size})
        return out
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        out.append({"path": rel, "type": "file", "size": path.stat().st_size})
        if len(out) >= limit:
            break
    return out


def search_vault(query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = vault_index.search(query, limit=limit)
    results: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            results.append(
                {
                    "path": row.get("path") or row.get("rel") or "",
                    "title": row.get("title") or "",
                    "snippet": row.get("snippet") or row.get("preview") or "",
                    "score": row.get("score"),
                }
            )
        else:
            results.append({"path": str(row)})
    return results


def note_basename(path: Optional[str]) -> str:
    if not path:
        return ""
    return Path(path).name
