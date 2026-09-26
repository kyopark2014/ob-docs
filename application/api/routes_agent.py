"""Open Agent chat — SSE over in-process LangGraph (vault tools + code)."""

from __future__ import annotations

import json
import logging
import queue
import threading
from pathlib import Path
from typing import Any, Generator, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from application import (
    agent_chat_db,
    models as model_catalog,
    notes_db,
    vault_backend,
)
from application.api.routes_auth import require_user_id
from application.harness_client import parse_vault_writes, strip_write_markers
from application.open_agent import vault_ops
from application.open_agent.prompt import build_user_prompt
from application.open_agent.runner import (
    agent_ready,
    iter_agent_events,
    normalize_session_id,
)

logger = logging.getLogger("routes_agent")

router = APIRouter(prefix="/api/agent", tags=["agent"])

SSE_HEARTBEAT_INTERVAL_SECONDS = 15


class ChatBody(BaseModel):
    prompt: str = ""
    note_path: Optional[str] = Field(default=None, max_length=1024)
    session_id: Optional[str] = Field(default=None, max_length=128)
    model_name: Optional[str] = Field(default=None, max_length=128)
    image_paths: list[str] = Field(default_factory=list, max_length=20)
    file_paths: list[str] = Field(default_factory=list, max_length=20)


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _sse_keepalive() -> str:
    return ": keepalive\n\n"


def _apply_vault_write(path: str, content: str) -> dict[str, Any]:
    return vault_ops.apply_vault_write(path, content)


def _upsert_tool_event(timeline: list[dict[str, Any]], event: dict[str, Any]) -> None:
    """Keep tool / tool_result upserted by toolUseId (harness-work style)."""
    etype = event.get("type")
    tid = (event.get("toolUseId") or "").strip()
    if etype == "text":
        data = (event.get("data") or "").strip()
        if data:
            timeline.append({"type": "text", "data": data})
        return
    if etype not in ("tool", "tool_result", "info"):
        return
    if tid:
        for i in range(len(timeline) - 1, -1, -1):
            prev = timeline[i]
            if prev.get("type") == etype and prev.get("toolUseId") == tid:
                timeline[i] = {**prev, **event}
                return
    timeline.append(dict(event))


def _is_streaming_prefix_of_final(partial: str, final: str) -> bool:
    if not partial or not final:
        return False
    if final.startswith(partial) or partial.startswith(final):
        return True
    head_len = min(len(partial), len(final), 80)
    return partial[:head_len] == final[:head_len]


def _set_final_text_in_timeline(
    timeline: list[dict[str, Any]], final_content: str
) -> None:
    """Append (or extend last text) so final reply stays after tool cards."""
    stripped = (final_content or "").strip()
    if not stripped:
        return
    if timeline and timeline[-1].get("type") == "text":
        last = (timeline[-1].get("data") or "").strip()
        if last == stripped:
            return
        if _is_streaming_prefix_of_final(last, stripped):
            timeline[-1] = {"type": "text", "data": stripped}
            return
    timeline.append({"type": "text", "data": stripped})


def _sanitize_timeline_text(timeline: list[dict[str, Any]]) -> None:
    cleaned: list[dict[str, Any]] = []
    for ev in timeline:
        if ev.get("type") != "text":
            cleaned.append(ev)
            continue
        data = strip_write_markers(ev.get("data") or "").strip()
        if data:
            cleaned.append({**ev, "data": data})
    timeline[:] = cleaned


@router.get("/health")
def agent_health() -> dict:
    catalog = model_catalog.list_models()
    ready = agent_ready()
    return {
        "status": "ok",
        "backend": "langgraph",
        "agentConfigured": ready,
        "harnessConfigured": False,
        "harnessArn": None,
        "skills": [],
        "mcp": [],
        "tools": ["vault_read", "vault_write", "vault_search", "vault_list", "execute_code", "bash"],
        "models": catalog["models"],
        "default_model": catalog["default_model"],
    }


@router.get("/models")
def agent_models(request: Request) -> dict:
    require_user_id(request)
    return model_catalog.list_models()


@router.get("/note-meta")
def note_meta(request: Request, path: str) -> dict:
    """Filename + size for the agent input chip (includes durable note_id)."""
    require_user_id(request)
    content, size = vault_ops.read_note(path)
    name = Path(path).name
    row = notes_db.ensure_note_for_path(path) if path.lower().endswith(".md") else None
    return {
        "path": path,
        "name": name,
        "size": size,
        "char_count": len(content),
        "note_id": (row or {}).get("note_id"),
        "title": (row or {}).get("title"),
        "size_bytes": (row or {}).get("size_bytes", size),
        "created_at": (row or {}).get("created_at"),
        "updated_at": (row or {}).get("updated_at"),
    }


@router.get("/messages")
def get_agent_messages(
    request: Request,
    note_id: Optional[str] = None,
    note_path: Optional[str] = None,
) -> dict:
    """Load Open Agent transcript for a note (conversation room)."""
    require_user_id(request)
    nid = (note_id or "").strip()
    path = (note_path or "").strip()
    if not nid and path:
        row = notes_db.ensure_note_for_path(path)
        nid = (row or {}).get("note_id") or ""
    if not nid:
        raise HTTPException(status_code=400, detail="note_id or note_path required")
    messages = agent_chat_db.list_messages(nid)
    return {"note_id": nid, "count": len(messages), "messages": messages}


@router.delete("/messages")
def clear_agent_messages(
    request: Request,
    note_id: Optional[str] = None,
    note_path: Optional[str] = None,
) -> dict:
    """Clear Open Agent transcript for a note."""
    require_user_id(request)
    nid = (note_id or "").strip()
    path = (note_path or "").strip()
    if not nid and path:
        row = notes_db.get_by_path(path)
        nid = (row or {}).get("note_id") or ""
    if not nid:
        raise HTTPException(status_code=400, detail="note_id or note_path required")
    removed = agent_chat_db.clear_messages(nid)
    return {"ok": True, "note_id": nid, "removed": removed}


@router.post("/chat")
def agent_chat(request: Request, body: ChatBody) -> StreamingResponse:
    user_id = require_user_id(request)
    prompt = (body.prompt or "").strip()
    note_path = (body.note_path or "").strip() or None
    image_paths = [
        (p or "").strip()
        for p in (body.image_paths or [])
        if (p or "").strip()
    ][:20]
    file_paths = [
        (p or "").strip()
        for p in (body.file_paths or [])
        if (p or "").strip()
    ][:20]
    if not prompt and not note_path and not image_paths and not file_paths:
        raise HTTPException(
            status_code=400,
            detail="prompt, note_path, or attachments are required",
        )
    if not prompt:
        if image_paths and not file_paths:
            prompt = "첨부한 이미지를 분석해 주세요."
        elif file_paths and not image_paths:
            prompt = "첨부한 파일을 바탕으로 요약·설명해 주세요."
        elif image_paths or file_paths:
            prompt = "첨부한 이미지와 파일을 바탕으로 답변해 주세요."
        else:
            prompt = "이 노트의 내용을 요약해 주세요."

    if not agent_ready():
        raise HTTPException(
            status_code=503,
            detail="Open Agent(LangGraph)를 초기화할 수 없습니다. Bedrock 권한/리전을 확인하세요.",
        )

    # Conversation room id = durable note_id.
    session_raw = (body.session_id or "").strip() or None
    note_id: Optional[str] = None
    if note_path and note_path.lower().endswith((".md", ".markdown")):
        row = notes_db.ensure_note_for_path(note_path)
        note_id = (row or {}).get("note_id") if row else None
        if note_id:
            if session_raw and session_raw != note_id:
                logger.info(
                    "agent chat override session_id %s -> note_id %s for note_path=%r",
                    session_raw,
                    note_id,
                    note_path,
                )
            session_raw = note_id
    session_id = normalize_session_id(session_raw)

    attach_paths: list[str] = []
    seen_attach: set[str] = set()
    for p in [note_path, *image_paths, *file_paths]:
        if not p or p in seen_attach:
            continue
        seen_attach.add(p)
        attach_paths.append(p)

    user_content = prompt
    if not user_content:
        if image_paths and not file_paths:
            user_content = f"이미지 {len(image_paths)}개"
        elif file_paths and not image_paths:
            user_content = f"파일 {len(file_paths)}개"
        elif image_paths or file_paths:
            user_content = "첨부"
        elif note_path:
            user_content = Path(note_path).name
        else:
            user_content = "(빈 메시지)"
    model_name = model_catalog.normalize_model_name(body.model_name)
    logger.info(
        "agent chat backend=langgraph note_path=%r model=%s images=%d files=%d "
        "prompt_chars=%d session=%s",
        note_path,
        model_name,
        len(image_paths),
        len(file_paths),
        len(prompt),
        session_id,
    )
    # Prompt is built inside iter_agent_events; log preview size here.
    preview = build_user_prompt(
        prompt,
        note_path=note_path,
        image_paths=image_paths,
        file_paths=file_paths,
    )
    logger.info(
        "agent chat built_prompt_chars=%d note_path=%r files=%d",
        len(preview),
        note_path,
        len(file_paths),
    )

    def event_stream() -> Generator[str, None, None]:
        q: queue.Queue[Any] = queue.Queue()

        def worker() -> None:
            with vault_backend.user_scope(user_id):
                tool_events: list[dict[str, Any]] = []
                history_rows: list[dict[str, Any]] = []
                if note_id:
                    try:
                        agent_chat_db.add_message(
                            note_id,
                            "user",
                            user_content,
                            attachments=attach_paths,
                        )
                        history_rows = agent_chat_db.list_messages(note_id)
                    except Exception:
                        logger.exception(
                            "failed to persist user agent message note_id=%s", note_id
                        )
                updated_paths: set[str] = set()
                try:
                    gen = iter_agent_events(
                        prompt,
                        session_id=session_id,
                        actor_id=user_id,
                        model_name=model_name,
                        note_path=note_path,
                        image_paths=image_paths,
                        file_paths=file_paths,
                        history_rows=history_rows,
                        allowed_write_paths={note_path} if note_path else None,
                    )
                    final = ""
                    try:
                        while True:
                            event = next(gen)
                            etype = event.get("type")
                            if etype == "token":
                                text = event.get("text") or ""
                                final = text
                                q.put(("token", text))
                            elif etype == "text":
                                data = strip_write_markers(event.get("data") or "")
                                if data:
                                    cleaned = {"type": "text", "data": data}
                                    _upsert_tool_event(tool_events, cleaned)
                                    q.put(("text", data))
                            elif etype in ("tool", "tool_result", "info"):
                                _upsert_tool_event(tool_events, event)
                                q.put((etype, event))
                            elif etype == "note_updated":
                                path = event.get("path") or ""
                                if path:
                                    updated_paths.add(path)
                                q.put(("note_updated", event))
                    except StopIteration as stop:
                        if isinstance(stop.value, str) and stop.value:
                            final = stop.value

                    # Fallback: VAULT_WRITE markers (legacy / model habit).
                    writes = parse_vault_writes(final)
                    updated: list[dict[str, Any]] = []
                    for path, content in writes:
                        if note_path and path != note_path:
                            logger.warning(
                                "Ignoring WRITE to %s (attached note is %s)",
                                path,
                                note_path,
                            )
                            continue
                        if path in updated_paths:
                            continue
                        try:
                            meta = _apply_vault_write(path, content)
                            updated.append(meta)
                            updated_paths.add(path)
                            write_event = {
                                "type": "tool",
                                "tool": "vault_write",
                                "toolUseId": f"vault-write:{path}",
                                "input": {
                                    "path": path,
                                    "bytes": meta.get("bytes"),
                                },
                            }
                            _upsert_tool_event(tool_events, write_event)
                            q.put(("tool", write_event))
                            result_event = {
                                "type": "tool_result",
                                "tool": "vault_write",
                                "toolUseId": f"vault-write:{path}",
                                "data": json.dumps(
                                    {
                                        "ok": True,
                                        "path": path,
                                        "bytes": meta.get("bytes"),
                                    },
                                    ensure_ascii=False,
                                ),
                            }
                            _upsert_tool_event(tool_events, result_event)
                            q.put(("tool_result", result_event))
                            q.put(("note_updated", meta))
                        except Exception as e:
                            logger.exception("vault write failed for %s", path)
                            q.put(("error", f"노트 저장 실패 ({path}): {e}"))

                    visible_final = strip_write_markers(final).strip()
                    _sanitize_timeline_text(tool_events)
                    _set_final_text_in_timeline(tool_events, visible_final)

                    if note_id:
                        try:
                            agent_chat_db.add_message(
                                note_id,
                                "assistant",
                                visible_final or "(응답 없음)",
                                tool_events=tool_events,
                            )
                        except Exception:
                            logger.exception(
                                "failed to persist assistant agent message note_id=%s",
                                note_id,
                            )

                    q.put(
                        (
                            "done",
                            {
                                "session_id": session_id,
                                "result": visible_final,
                                "updated": updated
                                or [{"path": p} for p in sorted(updated_paths)],
                                "tool_events": tool_events,
                            },
                        )
                    )
                except Exception as e:
                    logger.exception("agent chat failed")
                    q.put(("error", str(e)))
                finally:
                    q.put(None)

        threading.Thread(target=worker, daemon=True).start()
        yield _sse({"type": "session", "session_id": session_id})

        while True:
            try:
                item = q.get(timeout=SSE_HEARTBEAT_INTERVAL_SECONDS)
            except queue.Empty:
                yield _sse_keepalive()
                continue
            if item is None:
                break
            kind, payload = item
            if kind == "token":
                yield _sse({"type": "token", "text": payload})
            elif kind == "text":
                yield _sse({"type": "text", "data": payload})
            elif kind == "tool":
                yield _sse({"type": "tool", **payload})
            elif kind == "tool_result":
                yield _sse({"type": "tool_result", **payload})
            elif kind == "info":
                yield _sse({"type": "info", **payload})
            elif kind == "note_updated":
                yield _sse({"type": "note_updated", **payload})
            elif kind == "done":
                yield _sse({"type": "done", **payload})
            elif kind == "error":
                yield _sse({"type": "error", "error": payload})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
