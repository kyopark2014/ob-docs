"""Bedrock AgentCore InvokeHarness client for ob-docs Open Agent.

Tools: websearch (Exa) only — no code interpreter.
Skill: use-vault (S3, guidance). Note edits: VAULT_WRITE markers (server-side).
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any, Generator, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, EventStreamError, ReadTimeoutError

from application import utils

logger = logging.getLogger("harness_client")

HARNESS_INVOKE_READ_TIMEOUT = 900
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,127}$")

WRITE_BLOCK_RE = re.compile(
    r"<<<VAULT_WRITE\s+(.+?)>>>\s*\n?(.*?)<<<END_VAULT_WRITE>>>",
    re.DOTALL,
)
# Hide an in-progress write block while the model is still streaming.
INCOMPLETE_WRITE_RE = re.compile(r"<<<VAULT_WRITE\b[\s\S]*\Z")


def strip_write_markers(text: str) -> str:
    cleaned = WRITE_BLOCK_RE.sub("", text or "")
    cleaned = INCOMPLETE_WRITE_RE.sub("", cleaned)
    return cleaned.strip()


def parse_vault_writes(text: str) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    for match in WRITE_BLOCK_RE.finditer(text or ""):
        path = (match.group(1) or "").strip()
        content = match.group(2) or ""
        if path:
            results.append((path, content))
    return results

USE_VAULT_SKILL = "use-vault"
SKILLS_S3_PREFIX = "skills"
TOOL_INPUT_PREVIEW_MAX = 8000
TOOL_RESULT_PREVIEW_MAX = 8000


def _json_preview(obj: Any, max_len: int = 2400) -> str:
    try:
        if isinstance(obj, (bytes, bytearray)):
            return f"<binary {len(obj)} bytes>"
        s = json.dumps(obj, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        s = str(obj)
    if len(s) > max_len:
        return s[:max_len] + f"... (+{len(s) - max_len} chars)"
    return s


def _try_parse_json(raw: str) -> Any:
    text = (raw or "").strip()
    if not text or text in ("…", "..."):
        return {}
    try:
        return json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return text


def resolve_harness_arn() -> Optional[str]:
    env = (os.environ.get("HARNESS_ARN") or "").strip()
    if env:
        return env
    cfg = utils.load_config()
    arn = (cfg.get("HARNESS_ARN") or "").strip()
    return arn or None


def bedrock_region() -> str:
    cfg = utils.load_config()
    return (
        (cfg.get("region") or "").strip()
        or (os.environ.get("AWS_REGION") or "").strip()
        or (os.environ.get("AWS_DEFAULT_REGION") or "").strip()
        or "us-west-2"
    )


def s3_bucket() -> str:
    cfg = utils.load_config()
    return (
        (os.environ.get("S3_BUCKET") or "").strip()
        or (cfg.get("s3_bucket") or "").strip()
    )


def normalize_session_id(raw: Optional[str]) -> str:
    value = (raw or "").strip()
    if value and SESSION_ID_RE.match(value):
        return value
    return "ob" + uuid.uuid4().hex


def default_invoke_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "remote_mcp",
            "name": "exa",
            "config": {"remoteMcp": {"url": "https://mcp.exa.ai/mcp"}},
        },
    ]


def default_invoke_skills() -> list[dict[str, Any]]:
    bucket = s3_bucket()
    if not bucket:
        return [{"path": f"skills/{USE_VAULT_SKILL}"}]
    return [
        {
            "s3": {
                "uri": f"s3://{bucket}/{SKILLS_S3_PREFIX}/{USE_VAULT_SKILL}/",
            }
        }
    ]


def _client():
    return boto3.client(
        "bedrock-agentcore",
        region_name=bedrock_region(),
        config=Config(
            read_timeout=HARNESS_INVOKE_READ_TIMEOUT,
            connect_timeout=60,
            retries={"max_attempts": 0},
        ),
    )


def build_user_prompt(
    user_prompt: str,
    *,
    note_path: Optional[str] = None,
    note_content: Optional[str] = None,
    image_paths: Optional[list[str]] = None,
    file_paths: Optional[list[str]] = None,
) -> str:
    lines = [
        "## 사용자 요청",
        (user_prompt or "").strip() or "(요청 없음)",
        "",
    ]
    if note_path:
        lines.extend(
            [
                "## 선택된 노트",
                f"- path: {note_path}",
                "",
                "## 현재 본문",
                "```markdown",
                note_content if note_content is not None else "(빈 파일)",
                "```",
                "",
                "이 노트를 수정하려면 응답에 다음 형식으로 **전체 본문**을 넣으세요:",
                f"<<<VAULT_WRITE {note_path}>>>",
                "# 제목",
                "",
                "본문...",
                "<<<END_VAULT_WRITE>>>",
            ]
        )
    else:
        lines.append(
            "(선택된 노트 없음 — 요약·검색만 가능합니다. 수정하려면 노트를 선택하세요.)"
        )

    attachment_block = _build_attachment_sections(
        user_prompt=user_prompt,
        image_paths=image_paths or [],
        file_paths=file_paths or [],
    )
    if attachment_block:
        lines.extend(["", attachment_block])
    return "\n".join(lines)


TEXT_FILE_SUFFIXES = {
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
}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
ATTACHED_TEXT_MAX = 80_000
VISION_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def _image_format_from_name(name: str) -> str:
    ext = Path(name).suffix.lower().lstrip(".")
    if ext in ("jpg", "jpeg"):
        return "jpeg"
    if ext == "gif":
        return "gif"
    if ext == "webp":
        return "webp"
    return "png"


def _read_vault_bytes(path: str) -> bytes:
    from application import vault_backend

    target = vault_backend.resolve_vault_path(path)
    if not target.is_file():
        raise FileNotFoundError(f"Not found: {path}")
    return target.read_bytes()


def _describe_images(prompt: str, image_paths: list[str]) -> str:
    """Describe vault images via Bedrock Converse (InvokeHarness is text-only)."""
    if not image_paths:
        return ""
    content_blocks: list[dict[str, Any]] = []
    names: list[str] = []
    for path in image_paths:
        name = Path(path).name
        try:
            image_bytes = _read_vault_bytes(path)
            content_blocks.append(
                {
                    "image": {
                        "format": _image_format_from_name(name),
                        "source": {"bytes": image_bytes},
                    }
                }
            )
            names.append(name)
        except Exception as exc:
            logger.warning("vision describe: failed to load %s: %s", path, exc)
            content_blocks.append({"text": f"(이미지 로드 실패: {path} — {exc})"})

    if not any("image" in block for block in content_blocks):
        return "\n".join(f"- {p}" for p in image_paths)

    user_text = (prompt or "").strip() or "첨부한 이미지를 자세히 설명해주세요."
    content_blocks.append(
        {
            "text": (
                "다음 첨부 이미지를 자세히 분석하세요. 구성 요소, 텍스트/레이블, "
                "화살표·연결 관계, 전체 의미를 markdown으로 정리하세요.\n"
                f"사용자 요청: {user_text}"
            )
        }
    )
    try:
        runtime = boto3.client("bedrock-runtime", region_name=bedrock_region())
        response = runtime.converse(
            modelId=VISION_MODEL_ID,
            messages=[{"role": "user", "content": content_blocks}],
            inferenceConfig={"maxTokens": 4096, "temperature": 0.2},
        )
        parts: list[str] = []
        for block in response.get("output", {}).get("message", {}).get("content", []):
            text = block.get("text")
            if text:
                parts.append(text)
        description = "\n".join(parts).strip()
        if not description:
            return "\n".join(f"- {p}" for p in image_paths)
        label = ", ".join(names) if names else "첨부 이미지"
        paths_block = "\n".join(f"- {p}" for p in image_paths)
        return (
            f"[첨부 이미지 분석: {label}]\n{description}\n"
            f"[이미지 vault 경로]\n{paths_block}"
        )
    except Exception as exc:
        logger.warning("vision describe via Converse failed: %s", exc)
        return "\n".join(f"- {p}" for p in image_paths)


def _read_attached_file_section(path: str) -> str:
    from application import vault_backend

    name = Path(path).name
    suffix = Path(path).suffix.lower()
    try:
        target = vault_backend.resolve_vault_path(path)
    except ValueError as exc:
        return f"### {name}\n- path: {path}\n- error: {exc}\n"
    if not target.is_file():
        return f"### {name}\n- path: {path}\n- error: not found\n"
    size = target.stat().st_size
    if suffix in IMAGE_SUFFIXES:
        return f"### {name}\n- path: {path}\n- type: image ({size} bytes)\n"
    if suffix not in TEXT_FILE_SUFFIXES:
        return (
            f"### {name}\n- path: {path}\n"
            f"- type: binary ({size} bytes — 본문은 프롬프트에 포함되지 않음)\n"
        )
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"### {name}\n- path: {path}\n- error: {exc}\n"
    if len(text) > ATTACHED_TEXT_MAX:
        text = text[:ATTACHED_TEXT_MAX] + f"\n... (+{len(text) - ATTACHED_TEXT_MAX} chars truncated)"
    fence = "markdown" if suffix in {".md", ".markdown"} else ""
    return (
        f"### {name}\n- path: {path}\n- size: {size} bytes\n\n"
        f"```{fence}\n{text}\n```\n"
    )


def _build_attachment_sections(
    *,
    user_prompt: str,
    image_paths: list[str],
    file_paths: list[str],
) -> str:
    parts: list[str] = []
    if image_paths:
        parts.append("## 첨부 이미지")
        parts.append(_describe_images(user_prompt, image_paths))
    if file_paths:
        parts.append("## 첨부 파일")
        for path in file_paths:
            parts.append(_read_attached_file_section(path))
    return "\n".join(p for p in parts if p).strip()



def iter_harness_events(
    prompt: str,
    *,
    session_id: str,
    actor_id: Optional[str] = None,
) -> Generator[dict[str, Any], None, str]:
    """Yield stream events for SSE; return final assistant text.

    Event shapes:
      {"type": "token", "text": "<accumulated>"}
      {"type": "tool", "tool": name, "input": ..., "toolUseId": id}
      {"type": "tool_result", "tool": name, "data": str, "toolUseId": id}
    """
    harness_arn = resolve_harness_arn()
    if not harness_arn:
        raise RuntimeError(
            "HARNESS_ARN is not configured. Run `python installer.py` to provision "
            "the ob-docs harness, or set HARNESS_ARN in config.json / env."
        )

    tools = default_invoke_tools()
    skills = default_invoke_skills()
    kwargs: dict[str, Any] = {
        "harnessArn": harness_arn,
        "runtimeSessionId": session_id,
        "messages": [
            {
                "role": "user",
                "content": [{"text": prompt}],
            }
        ],
        "tools": tools,
    }
    if skills:
        kwargs["skills"] = skills
    if actor_id:
        kwargs["actorId"] = actor_id

    logger.info(
        "invoke_harness arn=%s session=%s actor=%s prompt_chars=%s tools=%s skills=%s",
        harness_arn,
        session_id,
        actor_id,
        len(prompt or ""),
        [t.get("name") for t in tools],
        skills,
    )

    response = _client().invoke_harness(**kwargs)
    stream = response.get("stream")
    if stream is None:
        raise RuntimeError(f"Empty Harness response: {response}")

    chunks: list[str] = []
    event_count = 0
    block_tool_use: dict[Any, tuple[str, str]] = {}
    block_tool_result: dict[Any, str] = {}
    tool_input_buffers: dict[str, str] = {}
    tool_result_buffers: dict[str, str] = {}
    tool_name_list: dict[str, str] = {}

    try:
        for event in stream:
            event_count += 1

            if "messageStart" in event:
                block_tool_use.clear()
                block_tool_result.clear()
                tool_input_buffers.clear()
                tool_result_buffers.clear()
                continue

            if "contentBlockStart" in event:
                cbs = event["contentBlockStart"]
                idx = cbs.get("contentBlockIndex")
                start = cbs.get("start") or {}
                if "toolUse" in start:
                    tu = start["toolUse"] or {}
                    tid = (tu.get("toolUseId") or "").strip()
                    name = tu.get("name") or ""
                    if tid and idx is not None:
                        tool_name_list[tid] = name
                        block_tool_use[idx] = (tid, name)
                        tool_input_buffers[tid] = ""
                        # Flush text segment before tool so UI can interleave.
                        if chunks:
                            yield {
                                "type": "text",
                                "data": strip_write_markers("".join(chunks)),
                            }
                            chunks.clear()
                        yield {
                            "type": "tool",
                            "tool": name,
                            "input": {},
                            "toolUseId": tid,
                        }
                        logger.info("[tool] %s toolUseId=%s", name, tid)
                if "toolResult" in start:
                    tr = start["toolResult"] or {}
                    tid = (tr.get("toolUseId") or "").strip()
                    if tid and idx is not None:
                        block_tool_result[idx] = tid
                        tool_result_buffers[tid] = ""
                        tlabel = tool_name_list.get(tid, tid)
                        yield {
                            "type": "tool_result",
                            "tool": tlabel,
                            "toolUseId": tid,
                            "data": "…",
                        }
                continue

            if "contentBlockDelta" in event:
                cbd = event["contentBlockDelta"]
                idx = cbd.get("contentBlockIndex")
                delta = cbd.get("delta") or {}
                if "text" in delta:
                    piece = delta.get("text") or ""
                    if piece:
                        chunks.append(piece)
                        yield {
                            "type": "token",
                            "text": strip_write_markers("".join(chunks)),
                        }
                if "toolUse" in delta:
                    tu = delta["toolUse"] or {}
                    tin = tu.get("input")
                    if isinstance(tin, str) and tin and idx is not None:
                        pair = block_tool_use.get(idx)
                        if pair:
                            tid, name = pair
                            tool_input_buffers[tid] = (
                                tool_input_buffers.get(tid, "") + tin
                            )
                            preview = tool_input_buffers[tid]
                            if len(preview) > TOOL_INPUT_PREVIEW_MAX:
                                preview = preview[:TOOL_INPUT_PREVIEW_MAX] + "…"
                            yield {
                                "type": "tool",
                                "tool": name,
                                "input": _try_parse_json(preview),
                                "toolUseId": tid,
                            }
                if "toolResult" in delta:
                    tr_part = delta.get("toolResult")
                    if tr_part is not None and idx is not None:
                        tid = block_tool_result.get(idx)
                        if tid:
                            buf = tool_result_buffers.get(tid, "")
                            if isinstance(tr_part, list):
                                for item in tr_part:
                                    if isinstance(item, dict):
                                        if "text" in item:
                                            buf += item.get("text") or ""
                                        elif "json" in item:
                                            buf += _json_preview(
                                                item.get("json"),
                                                TOOL_RESULT_PREVIEW_MAX,
                                            )
                                    else:
                                        buf += str(item)
                            else:
                                buf += _json_preview(
                                    tr_part, TOOL_RESULT_PREVIEW_MAX
                                )
                            if len(buf) > TOOL_RESULT_PREVIEW_MAX:
                                buf = (
                                    buf[:TOOL_RESULT_PREVIEW_MAX]
                                    + f"... (+{len(buf) - TOOL_RESULT_PREVIEW_MAX} chars)"
                                )
                            tool_result_buffers[tid] = buf
                            yield {
                                "type": "tool_result",
                                "tool": tool_name_list.get(tid, ""),
                                "toolUseId": tid,
                                "data": buf,
                            }
                continue

            if "runtimeClientError" in event:
                msg = (event["runtimeClientError"] or {}).get("message") or "unknown error"
                raise RuntimeError(f"Harness runtime error: {msg}")
            if "internalServerException" in event:
                msg = (event.get("internalServerException") or {}).get("message") or str(
                    event["internalServerException"]
                )
                raise RuntimeError(f"Harness internal error: {msg}")
            if "validationException" in event:
                msg = (event.get("validationException") or {}).get("message") or str(
                    event["validationException"]
                )
                raise RuntimeError(f"Harness validation error: {msg}")
    except (ReadTimeoutError, EventStreamError, ClientError) as e:
        partial = "".join(chunks)
        raise RuntimeError(
            f"Harness stream interrupted (events={event_count}, "
            f"partial_chars={len(partial)}): {e}"
        ) from e

    final = "".join(chunks)
    logger.info(
        "harness stream done events=%s chars=%s",
        event_count,
        len(final),
    )
    return final


def iter_harness_text(
    prompt: str,
    *,
    session_id: str,
    actor_id: Optional[str] = None,
) -> Generator[str, None, str]:
    """Yield incremental full-text snapshots; return final text."""
    final = ""
    for event in iter_harness_events(
        prompt, session_id=session_id, actor_id=actor_id
    ):
        if event.get("type") == "token" and isinstance(event.get("text"), str):
            final = event["text"]
            yield final
    return final


def collect_harness_text(
    prompt: str,
    *,
    session_id: str,
    actor_id: Optional[str] = None,
) -> str:
    gen = iter_harness_events(prompt, session_id=session_id, actor_id=actor_id)
    final = ""
    try:
        while True:
            event = next(gen)
            if event.get("type") == "token" and isinstance(event.get("text"), str):
                final = event["text"]
    except StopIteration as stop:
        if isinstance(stop.value, str) and stop.value:
            final = stop.value
    return final
