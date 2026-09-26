"""System / user prompts for the LangGraph Open Agent."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

SYSTEM_PROMPT = """\
당신은 ob-note의 Open Agent입니다. 사용자의 vault(마크다운 노트)를 읽고 수정·요약·검색합니다.

## 도구 규칙 (필수)
1. 노트 **읽기** → `vault_read(path)`
2. 노트 **수정/저장** → `vault_write(path, content)` 로 **전체 본문**을 저장
3. 검색 → `vault_search` / 목록 → `vault_list`
4. 계산·파싱만 `execute_code` / `bash` 사용. **노트를 셸/코드로 쓰지 마세요.**

## 노트 작성 규칙
- 본문은 `# 제목` 한 줄로 시작. YAML frontmatter 금지.
- 새 노트는 카테고리 폴더 아래 (`Category/Note.md`). vault 루트에 두지 마세요.
- 선택된 노트가 있으면 그 path만 수정하세요 (다른 path 쓰기 금지).
- 이미지를 vault에 넣을 때는 상대경로만 사용하세요.

## 응답
- 한국어로 간결하게.
- 저장 후에는 무엇을 바꿨는지 짧게 요약하세요.
"""


def build_user_prompt(
    user_prompt: str,
    *,
    note_path: Optional[str] = None,
    image_paths: Optional[list[str]] = None,
    file_paths: Optional[list[str]] = None,
) -> str:
    """Build the human message. Bodies are fetched via vault_read, not inlined."""
    from application.harness_client import (
        _build_attachment_sections,
        _vault_attachment_ref,
    )

    lines = [
        "## 사용자 요청",
        (user_prompt or "").strip() or "(요청 없음)",
        "",
    ]
    if note_path:
        ref = _vault_attachment_ref(note_path)
        lines.extend(
            [
                "## 선택된 노트",
                f"- path: {ref['path']}",
                f"- name: {ref['name']}",
                f"- size: {ref['size']} bytes",
            ]
        )
        if ref.get("s3_uri"):
            lines.append(f"- s3: {ref['s3_uri']}")
        if ref.get("url"):
            lines.append(f"- url: {ref['url']}")
        lines.extend(
            [
                "",
                "본문은 프롬프트에 없습니다. 먼저 `vault_read`로 읽으세요.",
                f"수정 시 `vault_write(path={note_path!r}, content=전체본문)` 을 호출하세요.",
                "content는 `# 제목`으로 시작하는 완전한 마크다운이어야 합니다.",
            ]
        )
    else:
        lines.append(
            "(선택된 노트 없음 — 검색·요약만 가능. 수정하려면 노트를 선택하세요.)"
        )

    attachment_block = _build_attachment_sections(
        user_prompt=user_prompt,
        image_paths=image_paths or [],
        file_paths=file_paths or [],
    )
    if attachment_block:
        lines.extend(["", attachment_block])
        lines.append(
            "첨부 텍스트/이미지는 위 링크 또는 `vault_read`로 확인하세요. "
            "필요하면 `execute_code`로 URL을 읽을 수 있습니다."
        )
    return "\n".join(lines)


def history_to_messages(rows: list[dict]) -> list:
    """Convert agent_chat_db rows to LangChain messages (exclude the latest user turn)."""
    from langchain_core.messages import AIMessage, HumanMessage

    if not rows:
        return []
    # Caller already persisted the new user message; drop the last user row so we
    # don't duplicate it with the fresh HumanMessage we append.
    trimmed = list(rows)
    if trimmed and trimmed[-1].get("role") == "user":
        trimmed = trimmed[:-1]

    out: list = []
    for row in trimmed[-12:]:  # keep last ~6 turns
        role = row.get("role")
        content = (row.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            out.append(HumanMessage(content=content))
        elif role == "assistant":
            out.append(AIMessage(content=content))
    return out


def note_label(path: Optional[str]) -> str:
    return Path(path).name if path else ""
