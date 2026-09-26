"""LangChain tools that read/write the signed-in user's vault in-process."""

from __future__ import annotations

import contextvars
import json
import logging
from typing import Any, Optional

from langchain_core.tools import tool

from application.open_agent import vault_ops

logger = logging.getLogger("open_agent.tools_vault")

# Paths the agent is allowed to overwrite this turn (selected note + extras).
_allowed_write_paths: contextvars.ContextVar[Optional[set[str]]] = contextvars.ContextVar(
    "open_agent_allowed_writes", default=None
)
# Successful writes for SSE note_updated (filled by vault_write).
_write_events: contextvars.ContextVar[Optional[list[dict[str, Any]]]] = contextvars.ContextVar(
    "open_agent_write_events", default=None
)


def bind_write_context(
    *,
    allowed_paths: Optional[set[str]] = None,
    write_events: Optional[list[dict[str, Any]]] = None,
) -> tuple[contextvars.Token, contextvars.Token]:
    t1 = _allowed_write_paths.set(allowed_paths)
    t2 = _write_events.set(write_events if write_events is not None else [])
    return t1, t2


def reset_write_context(tokens: tuple[contextvars.Token, contextvars.Token]) -> None:
    _allowed_write_paths.reset(tokens[0])
    _write_events.reset(tokens[1])


def drain_write_events() -> list[dict[str, Any]]:
    events = _write_events.get()
    if not events:
        return []
    out = list(events)
    events.clear()
    return out


def _normalize_path(path: str) -> str:
    return (path or "").replace("\\", "/").lstrip("/")


@tool
def vault_read(path: str) -> str:
    """Read a vault note or text file by relative path.

    Args:
        path: Vault-relative path (e.g. ``AI/Ontology.md``).
    """
    cleaned = _normalize_path(path)
    try:
        content, size = vault_ops.read_note(cleaned)
    except FileNotFoundError as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
    except Exception as e:
        logger.exception("vault_read failed path=%s", cleaned)
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
    return json.dumps(
        {"ok": True, "path": cleaned, "bytes": size, "content": content},
        ensure_ascii=False,
    )


@tool
def vault_write(path: str, content: str) -> str:
    """Write/overwrite a markdown or text note in the vault (full body).

    Prefer this over shell/code for note edits. Content must start with ``# Title``.
    Do not include YAML frontmatter.

    Args:
        path: Vault-relative path to write (usually the selected note).
        content: Full note body including the ``# Title`` heading.
    """
    cleaned = _normalize_path(path)
    allowed = _allowed_write_paths.get()
    if allowed is not None and cleaned not in allowed:
        return json.dumps(
            {
                "ok": False,
                "error": (
                    f"Write to {cleaned!r} is not allowed this turn. "
                    f"Allowed: {sorted(allowed)}"
                ),
            },
            ensure_ascii=False,
        )
    try:
        meta = vault_ops.apply_vault_write(cleaned, content)
    except Exception as e:
        logger.exception("vault_write failed path=%s", cleaned)
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)

    sink = _write_events.get()
    if sink is not None:
        sink.append(dict(meta))
    return json.dumps({"ok": True, **meta}, ensure_ascii=False)


@tool
def vault_search(query: str, limit: int = 20) -> str:
    """Search vault notes by keyword (title, tags, body).

    Args:
        query: Search string.
        limit: Max hits (default 20).
    """
    hits = vault_ops.search_vault(query, limit=max(1, min(int(limit or 20), 50)))
    return json.dumps({"ok": True, "query": query, "results": hits}, ensure_ascii=False)


@tool
def vault_list(prefix: str = "", limit: int = 100) -> str:
    """List vault files under an optional folder prefix.

    Args:
        prefix: Folder prefix (e.g. ``Agent``) or empty for vault root.
        limit: Max files to return.
    """
    files = vault_ops.list_vault(prefix, limit=max(1, min(int(limit or 100), 500)))
    return json.dumps({"ok": True, "prefix": prefix, "files": files}, ensure_ascii=False)


def get_vault_tools() -> list:
    return [vault_read, vault_write, vault_search, vault_list]
