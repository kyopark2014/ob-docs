---
name: use-vault
description: ob-docs vault의 마크다운 노트 규칙·경로·저장 형식을 안내합니다. 노트 읽기/수정, vault, 메모 저장, 위키링크, 백링크 요청 시 사용합니다.
---

# use-vault (ob-docs)

ob-docs vault의 마크다운 노트 규칙과 저장 형식을 정의합니다.

## Open Agent (Harness) — 중요

이 harness에는 **code interpreter가 없습니다.**  
`read_vault.py` / `write_vault.py`를 실행하지 마세요.

- **읽기**: 사용자 메시지에 포함된 선택 노트 본문을 사용하세요.
- **저장**: 응답에 `VAULT_WRITE` 마커를 넣으면 서버가 vault에 저장합니다.

```
<<<VAULT_WRITE Meeting/Note.md>>>
# Note

본문 전체 (YAML frontmatter 금지)
<<<END_VAULT_WRITE>>>
```

## When to Use

- vault 경로·노트 본문 규칙 확인
- 선택 노트 요약·설명·수정 형식 안내
- 위키링크·백링크 등 vault 관례

## Critical Rules

1. Open Agent에서는 스크립트/`curl`/S3 sync를 실행하지 마세요.
2. 경로는 vault 상대경로입니다. 예: `Meeting/Weekly-Sync.md`
3. 수정 시 선택 노트 경로만 `VAULT_WRITE`로 덮어쓰세요.
4. **노트 본문**: YAML frontmatter를 넣지 마세요. `# 제목` 한 줄로 시작하세요.
5. code interpreter / shell / AWS 자격증명 점검을 하지 마세요.

## Local / CI environments (optional scripts)

code interpreter가 있는 환경에서만 아래 스크립트를 사용할 수 있습니다.

| 스크립트 | 용도 |
| --- | --- |
| `scripts/read_vault.py` | health / tree / list / read / search / graph / backlinks |
| `scripts/write_vault.py` | write / append / mkdir / rename / delete / rebuild |

마운트 경로 예: `/home/.agents/skills/s3/use-vault/scripts/...`

## Environment / config.json

| 키 / 변수 | 설명 |
| --- | --- |
| `config.json` → `ob_docs_url` / `sharing_url` | vault API base (스크립트용) |
| `config.json` → `shared_project_name` | Secrets Manager prefix |
| `VAULT_AGENT_TOKEN` | 스크립트 인증 (Open Agent VAULT_WRITE 경로에는 불필요) |
