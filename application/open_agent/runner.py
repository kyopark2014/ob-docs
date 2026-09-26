"""Stream LangGraph Open Agent events in the harness-compatible SSE shape."""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any, Generator, Optional

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from application.open_agent.graph import build_agent, get_all_tools
from application.open_agent.prompt import SYSTEM_PROMPT, build_user_prompt, history_to_messages
from application.open_agent.tools_vault import (
    bind_write_context,
    drain_write_events,
    reset_write_context,
)

logger = logging.getLogger("open_agent.runner")

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,127}$")
TOOL_INPUT_PREVIEW_MAX = 8000
TOOL_RESULT_PREVIEW_MAX = 8000


def agent_ready() -> bool:
    """True when LangGraph + langchain-aws import cleanly."""
    try:
        import langchain_aws  # noqa: F401
        import langgraph  # noqa: F401

        return True
    except Exception:
        logger.exception("open agent not ready")
        return False


def normalize_session_id(raw: Optional[str]) -> str:
    value = (raw or "").strip()
    if value and SESSION_ID_RE.match(value):
        return value
    return "ob" + uuid.uuid4().hex


def _json_preview(obj: Any, max_len: int = 2400) -> str:
    try:
        s = json.dumps(obj, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        s = str(obj)
    if len(s) > max_len:
        return s[:max_len] + f"... (+{len(s) - max_len} chars)"
    return s


def _assistant_text(msg: AIMessage) -> str:
    content = msg.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(content) if content else ""


def iter_agent_events(
    user_prompt: str,
    *,
    session_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    model_name: Optional[str] = None,
    note_path: Optional[str] = None,
    image_paths: Optional[list[str]] = None,
    file_paths: Optional[list[str]] = None,
    history_rows: Optional[list[dict]] = None,
    allowed_write_paths: Optional[set[str]] = None,
) -> Generator[dict[str, Any], None, str]:
    """Yield ``token`` / ``text`` / ``tool`` / ``tool_result`` / ``note_updated``.

    Return value (StopIteration.value) is the final assistant text.
    ``actor_id`` is accepted for API parity; vault user_scope is set by the caller.
    """
    del actor_id  # vault user_scope is bound by routes_agent worker
    sid = normalize_session_id(session_id)
    logger.info(
        "open_agent start session=%s model=%s note=%r",
        sid,
        model_name,
        note_path,
    )

    allowed = set(allowed_write_paths or set())
    if note_path:
        allowed.add(note_path.replace("\\", "/").lstrip("/"))
    for p in file_paths or []:
        cleaned = (p or "").replace("\\", "/").lstrip("/")
        if cleaned.lower().endswith((".md", ".markdown", ".txt")):
            allowed.add(cleaned)

    write_events: list[dict[str, Any]] = []
    tokens = bind_write_context(allowed_paths=allowed or None, write_events=write_events)

    tools = get_all_tools()
    graph = build_agent(model_name=model_name, tools=tools, system_prompt=SYSTEM_PROMPT)

    prior = history_to_messages(history_rows or [])
    human = build_user_prompt(
        user_prompt,
        note_path=note_path,
        image_paths=image_paths,
        file_paths=file_paths,
    )
    inputs = {"messages": [*prior, HumanMessage(content=human)]}

    final_text = ""
    emitted_tool_ids: set[str] = set()

    try:
        for update in graph.stream(
            inputs,
            config={"recursion_limit": 40, "configurable": {"thread_id": sid}},
            stream_mode="updates",
        ):
            if not isinstance(update, dict):
                continue

            # vault_write tool already surfaces as tool/tool_result; only notify UI.
            for meta in drain_write_events():
                yield {"type": "note_updated", **meta}

            if "agent" in update:
                messages = (update["agent"] or {}).get("messages") or []
                for msg in messages:
                    if not isinstance(msg, AIMessage):
                        continue
                    text = _assistant_text(msg).strip()
                    if text and not msg.tool_calls:
                        final_text = text
                        yield {"type": "token", "text": text}
                        yield {"type": "text", "data": text}
                    if msg.tool_calls:
                        # Intermediate narration before tools.
                        if text:
                            yield {"type": "text", "data": text}
                        for tc in msg.tool_calls:
                            tid = tc.get("id") or f"tool-{uuid.uuid4().hex[:10]}"
                            name = tc.get("name") or "tool"
                            args = tc.get("args") or {}
                            # vault_write body can be huge — trim preview.
                            preview_args = args
                            if name == "vault_write" and isinstance(args, dict):
                                preview_args = {
                                    "path": args.get("path"),
                                    "content_chars": len(str(args.get("content") or "")),
                                }
                            if tid not in emitted_tool_ids:
                                emitted_tool_ids.add(tid)
                                yield {
                                    "type": "tool",
                                    "tool": name,
                                    "toolUseId": tid,
                                    "input": preview_args
                                    if len(_json_preview(preview_args))
                                    <= TOOL_INPUT_PREVIEW_MAX
                                    else {
                                        "_preview": _json_preview(
                                            preview_args, TOOL_INPUT_PREVIEW_MAX
                                        )
                                    },
                                }

            if "action" in update:
                messages = (update["action"] or {}).get("messages") or []
                for msg in messages:
                    if not isinstance(msg, ToolMessage):
                        continue
                    tid = msg.tool_call_id or f"tool-{uuid.uuid4().hex[:10]}"
                    name = getattr(msg, "name", None) or "tool"
                    data = msg.content
                    if not isinstance(data, str):
                        data = _json_preview(data, TOOL_RESULT_PREVIEW_MAX)
                    elif len(data) > TOOL_RESULT_PREVIEW_MAX:
                        data = data[:TOOL_RESULT_PREVIEW_MAX] + "…"
                    yield {
                        "type": "tool_result",
                        "tool": name,
                        "toolUseId": tid,
                        "data": data,
                    }

                for meta in drain_write_events():
                    yield {"type": "note_updated", **meta}

    except Exception:
        logger.exception("open_agent stream failed")
        raise
    finally:
        reset_write_context(tokens)

    if not final_text:
        final_text = ""
    return final_text
