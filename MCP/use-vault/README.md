# use-vault MCP (Streamable HTTP)

ob-note vault의 마크다운 노트를 조회·검색·생성·수정하는 MCP 서버입니다.  
`skills/use-vault`와 동일한 vault HTTP API를 `VaultAgent` 인증으로 호출합니다.

## Tools

모든 도구에 **`actor_id`**(계정 로그인 ID / email)가 필요합니다. system prompt의 계정 ID를 그대로 넘기세요.

| Tool | API | 설명 |
|------|-----|------|
| `vault_health` | `GET /api/health` | 헬스 |
| `vault_tree` | `GET /api/files/tree` | 파일 트리 |
| `vault_list` | `GET /api/files/list` | prefix/ext 목록 |
| `vault_read` | `GET /api/files/read` | 노트 본문 |
| `vault_search` | `GET /api/search` | 전문 검색 |
| `vault_graph` | `GET /api/graph` | 위키링크 그래프 |
| `vault_backlinks` | `GET /api/graph/backlinks` | 백링크 |
| `vault_write` | `PUT /api/files/write` | 덮어쓰기 |
| `vault_append` | `POST /api/files/append` | 이어쓰기 |
| `vault_mkdir` | `POST /api/files/mkdir` | 폴더 생성 |
| `vault_rename` | `POST /api/files/rename` | 이름/경로 변경 |
| `vault_delete` | `POST /api/files/delete` | 삭제 |
| `vault_rebuild` | `POST /api/graph/rebuild` | 그래프 재빌드 |

노트 규칙: vault 상대경로, YAML frontmatter 금지(`# 제목`으로 시작), 새 노트는 주제 폴더 아래만 (`Category/Note.md`).

## Environment

| 변수 | 설명 |
|------|------|
| `OB_DOCS_URL` | vault API base (예: `https://vault.my-agentic-ai.click`) |
| `SHARING_URL` | `OB_DOCS_URL` 미설정 시 fallback |
| `VAULT_AGENT_TOKEN` | Agent HMAC (`Secrets Manager` `ob-note/vault-agent-token`) |
| `PROJECT_NAME` | 기본 `ob-note` |
| `AWS_REGION` | Secrets Manager 조회용 (기본 `us-west-2`) |

로컬 개발에서는 `SESSION_SIGNING_KEY` 또는 loopback(`http://127.0.0.1:8502`)도 가능합니다.  
`config.json`의 `ob_docs_url` / `region` / `project_name`이 env 미설정 시 사용됩니다.

## Local

```bash
cd MCP/use-vault
export OB_DOCS_URL=https://vault.my-agentic-ai.click
export VAULT_AGENT_TOKEN=...   # or rely on Secrets Manager / SESSION_SIGNING_KEY
pip install -r requirements.txt
python -m mcp_server_use_vault
# → http://0.0.0.0:8000/mcp
```

## Docker

```bash
cd MCP/use-vault
docker build -t use-vault-mcp .
docker run --rm -p 8000:8000 \
  -e OB_DOCS_URL=https://vault.my-agentic-ai.click \
  -e VAULT_AGENT_TOKEN=... \
  use-vault-mcp
```

## 다른 앱에서 연결 (remote_mcp)

```json
{
  "type": "remote_mcp",
  "config": {
    "remoteMcp": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

Cursor 등 MCP 클라이언트에서는 Streamable HTTP URL로 `http://localhost:8000/mcp`를 등록하면 됩니다.

## AgentCore Runtime 배포

ob-note 루트에서:

```bash
python create_mcp.py
```

ECR + AgentCore Runtime(MCP, IAM SigV4)만 배포합니다 (Gateway 없음).  
자세한 설정·`mcp.json` 예시는 루트 [README.md ### Vault MCP](../../README.md#vault-mcp)를 보세요.
