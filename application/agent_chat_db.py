"""Open Agent conversation history in per-user SQLite (agentic-work style).

Stored in the same ``.vault/notes.db`` as the notes registry. Messages are keyed
by ``note_id`` (also used as harness ``session_id`` / conversation room).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from application import notes_db, vault_backend

logger = logging.getLogger("agent_chat_db")

_lock = threading.RLock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _connect() -> sqlite3.Connection:
    settings = vault_backend.settings_dir()
    settings.mkdir(parents=True, exist_ok=True)
    path = settings / notes_db.DB_NAME
    conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    # Notes table may already exist (notes_db); create messages alongside.
    notes_db.ensure_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS agent_messages (
            id TEXT PRIMARY KEY,
            note_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            attachments_json TEXT NOT NULL DEFAULT '[]',
            tool_events_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_agent_messages_note_created
            ON agent_messages(note_id, created_at ASC);
        """
    )


def _parse_json_list(raw: str | None) -> list[Any]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _row_to_message(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "note_id": row["note_id"],
        "role": row["role"],
        "content": row["content"] or "",
        "attachments": _parse_json_list(row["attachments_json"]),
        "tool_events": _parse_json_list(row["tool_events_json"]),
        "created_at": row["created_at"],
    }


def list_messages(note_id: str) -> list[dict[str, Any]]:
    nid = (note_id or "").strip()
    if not nid:
        return []
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            rows = conn.execute(
                """
                SELECT * FROM agent_messages
                WHERE note_id = ?
                ORDER BY created_at ASC, rowid ASC
                """,
                (nid,),
            ).fetchall()
            return [_row_to_message(r) for r in rows]
        finally:
            conn.close()


def add_message(
    note_id: str,
    role: str,
    content: str,
    *,
    attachments: list[str] | None = None,
    tool_events: list[dict[str, Any]] | None = None,
    message_id: str | None = None,
) -> dict[str, Any]:
    nid = (note_id or "").strip()
    if not nid:
        raise ValueError("note_id is required")
    role_norm = (role or "").strip().lower()
    if role_norm not in {"user", "assistant"}:
        raise ValueError("role must be user or assistant")

    mid = (message_id or "").strip() or str(uuid.uuid4())
    now = _utc_now()
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            conn.execute(
                """
                INSERT INTO agent_messages (
                    id, note_id, role, content, attachments_json, tool_events_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mid,
                    nid,
                    role_norm,
                    content or "",
                    json.dumps(attachments or [], ensure_ascii=False),
                    json.dumps(tool_events or [], ensure_ascii=False),
                    now,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM agent_messages WHERE id = ?", (mid,)
            ).fetchone()
            assert row is not None
            out = _row_to_message(row)
            logger.info(
                "agent_chat add role=%s note_id=%s id=%s chars=%d tools=%d",
                role_norm,
                nid,
                mid,
                len(content or ""),
                len(tool_events or []),
            )
            _schedule_persist()
            return out
        finally:
            conn.close()


def _schedule_persist() -> None:
    try:
        from application import vault_db_persistence

        vault_db_persistence.schedule_persist()
    except Exception:
        logger.debug("notes.db persist schedule skipped", exc_info=True)


def delete_messages_for_note(note_id: str) -> int:
    nid = (note_id or "").strip()
    if not nid:
        return 0
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            cur = conn.execute("DELETE FROM agent_messages WHERE note_id = ?", (nid,))
            conn.commit()
            n = int(cur.rowcount)
            if n:
                _schedule_persist()
            return n
        finally:
            conn.close()


def delete_messages_for_notes(note_ids: list[str]) -> int:
    ids = [n.strip() for n in note_ids if (n or "").strip()]
    if not ids:
        return 0
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            deleted = 0
            for nid in ids:
                cur = conn.execute(
                    "DELETE FROM agent_messages WHERE note_id = ?", (nid,)
                )
                deleted += int(cur.rowcount)
            conn.commit()
            if deleted:
                _schedule_persist()
            return deleted
        finally:
            conn.close()


def clear_messages(note_id: str) -> int:
    """Clear conversation history for a note (keep the note row)."""
    return delete_messages_for_note(note_id)
