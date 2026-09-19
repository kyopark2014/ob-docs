"""Open Agent chat — SSE over InvokeHarness (exa + code interpreter + use-vault)."""

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

from application.api.routes_auth import require_user_id
from application import harness_client, models as model_catalog, vault_backend, vault_index

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


def _read_note(path: str) -> tuple[str, int]:
    try:
        target = vault_backend.resolve_vault_path(path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"Note not found: {path}")
    content = target.read_text(encoding="utf-8", errors="replace")
    size = target.stat().st_size
    return content, size


def _apply_vault_write(path: str, content: str) -> dict[str, Any]:
    try:
        target = vault_backend.resolve_vault_path(path)
    except ValueError as e:
        raise ValueError(str(e)) from e
    if target.suffix.lower() not in {".md", ".txt", ".markdown"}:
        raise ValueError(f"Only markdown/text notes can be written: {path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    if target.suffix.lower() in {".md", ".markdown"}:
        vault_index.update_note(path)
        vault_index.rebuild_index()
    if vault_backend.backend_mode() == "s3":
        vault_backend.sync_to_s3(path)
    return {"path": path, "bytes": len(content.encode("utf-8"))}


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
        data = harness_client.strip_write_markers(ev.get("data") or "").strip()
        if data:
            cleaned.append({**ev, "data": data})
    timeline[:] = cleaned


@router.get("/health")
def agent_health() -> dict:
    arn = harness_client.resolve_harness_arn()
    catalog = model_catalog.list_models()
    return {
        "status": "ok",
        "harnessConfigured": bool(arn),
        "harnessArn": arn or None,
        "skills": ["use-vault"],
        "mcp": ["websearch"],
        "tools": ["exa", "code"],
        "models": catalog["models"],
        "default_model": catalog["default_model"],
    }


@router.get("/models")
def agent_models(request: Request) -> dict:
    require_user_id(request)
    return model_catalog.list_models()


@router.get("/note-meta")
def note_meta(request: Request, path: str) -> dict:
    """Filename + size for the agent input chip."""
    require_user_id(request)
    content, size = _read_note(path)
    name = Path(path).name
    return {
        "path": path,
        "name": name,
        "size": size,
        "char_count": len(content),
    }


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

    if not harness_client.resolve_harness_arn():
        raise HTTPException(
            status_code=503,
            detail=(
                "HARNESS_ARN이 설정되지 않았습니다. "
                "`python installer.py`로 ob-docs harness를 프로비저닝하세요."
            ),
        )

    note_content: Optional[str] = None
    # Bodies are not inlined anymore (S3 + presigned URL). Skip reading the full note.
    if note_path:
        note_content = None

    session_id = harness_client.normalize_session_id(body.session_id)
    model_name = model_catalog.normalize_model_name(body.model_name)
    logger.info(
        "agent chat note_path=%r model=%s images=%d files=%d prompt_chars=%d session=%s",
        note_path,
        model_name,
        len(image_paths),
        len(file_paths),
        len(prompt),
        session_id,
    )
    full_prompt = harness_client.build_user_prompt(
        prompt,
        note_path=note_path,
        note_content=note_content,
        image_paths=image_paths,
        file_paths=file_paths,
    )
    logger.info(
        "agent chat built_prompt_chars=%d note_path=%r files=%d",
        len(full_prompt),
        note_path,
        len(file_paths),
    )

    def event_stream() -> Generator[str, None, None]:
        q: queue.Queue[Any] = queue.Queue()

        def worker() -> None:
            # ContextVar does not propagate to bare threads (same as vault_sync).
            # Bind the signed-in user so resolve_vault_path / sync_to_s3 work.
            with vault_backend.user_scope(user_id):
                tool_events: list[dict[str, Any]] = []
                try:
                    gen = harness_client.iter_harness_events(
                        full_prompt,
                        session_id=session_id,
                        actor_id=user_id,
                        model_name=model_name,
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
                                data = harness_client.strip_write_markers(
                                    event.get("data") or ""
                                )
                                if data:
                                    cleaned = {"type": "text", "data": data}
                                    _upsert_tool_event(tool_events, cleaned)
                                    q.put(("text", data))
                            elif etype in ("tool", "tool_result", "info"):
                                _upsert_tool_event(tool_events, event)
                                q.put((etype, event))
                    except StopIteration as stop:
                        if isinstance(stop.value, str) and stop.value:
                            final = stop.value

                    writes = harness_client.parse_vault_writes(final)
                    updated: list[dict[str, Any]] = []
                    for path, content in writes:
                        if note_path and path != note_path:
                            logger.warning(
                                "Ignoring WRITE to %s (attached note is %s)",
                                path,
                                note_path,
                            )
                            continue
                        try:
                            meta = _apply_vault_write(path, content)
                            updated.append(meta)
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

                    visible_final = harness_client.strip_write_markers(final).strip()
                    _sanitize_timeline_text(tool_events)
                    # Final assistant reply must come AFTER tool cards (harness-work order).
                    _set_final_text_in_timeline(tool_events, visible_final)

                    q.put(
                        (
                            "done",
                            {
                                "session_id": session_id,
                                "result": visible_final,
                                "updated": updated,
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
