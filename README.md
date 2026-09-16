# ob-docs

Obsidian형 **Local-first Plain Text** vault 웹 앱입니다.  
노트는 `.md`가 Source of Truth이고, 설정은 `.vault/`에 격리되며, 그래프·검색·백링크는 파생 캐시입니다.

agentic-work와 **같은 CloudFront / ALB / S3 버킷**을 공유합니다.

| 항목 | 값 |
|---|---|
| 접속 경로 | `{cloudfront}/vault` |
| React 앱 | [`web/`](web/) (`base: /vault/`) |
| Vault mount | S3 `vault/` → `/mnt/vault` |
| 로컬 working copy | `data/vault/` |
| 설정 폴더 | `.vault/` (`.obsidian` 대체) |

## 아키텍처

```text
CloudFront
  └─ ALB
       ├─ /vault*  → ob-docs ECS  (/mnt/vault ← S3 Files vault/)
       └─ /*       → agentic-work ECS
```

- **mount 모드 (ECS)**: `/mnt/vault`에 직접 읽고 씀 (S3 Files가 비동기 동기화)
- **local 모드**: `data/vault/`만 사용
- **s3 모드 (옵션)**: `VAULT_S3_ENABLE=1`일 때 로컬 ↔ `s3://{bucket}/vault/` 주기적 sync

## 빠른 시작 (로컬)

```bash
cd ob-docs
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
chmod +x run_local.sh
./run_local.sh
```

브라우저: [http://localhost:8502/vault](http://localhost:8502/vault)

로컬에서는 `ALLOW_LOCAL_AUTH_BYPASS=1`로 세션 없이 동작합니다.

프론트만 개발할 때:

```bash
# 터미널 1 — API
PYTHONPATH=. ALLOW_LOCAL_AUTH_BYPASS=1 uvicorn application.server:app --port 8502

# 터미널 2 — Vite
cd web && npm install && npm run dev
# http://localhost:5174/vault/
```

## Vault 구조

```text
data/vault/                 # 또는 /mnt/vault
├── 00-Inbox/
├── notes/
├── attachments/
└── .vault/
    ├── app.json            # 환경설정
    ├── workspace.json      # 레이아웃 (gitignore 권장)
    ├── graph.json          # 파생 그래프
    └── cache/              # 인덱스 (삭제 후 재생성)
```

위키링크 `[[Note]]`, frontmatter(`aliases`, `tags`)를 파싱해 그래프·검색·백링크를 만듭니다.

## API

| Method | Path | 설명 |
|---|---|---|
| GET | `/vault/api/health` | 헬스체크 |
| GET | `/vault/api/session` | 공유 세션 확인 |
| GET | `/vault/api/files/tree` | 파일 트리 |
| GET | `/vault/api/files/read?path=` | 노트 읽기 |
| PUT | `/vault/api/files/write` | 노트 저장 |
| GET | `/vault/api/search?q=` | 검색 |
| GET | `/vault/api/graph` | 위키링크 그래프 |
| POST | `/vault/api/graph/rebuild` | 인덱스 재생성 |

## 인증

agentic-work와 동일한 `agent_user_id` HMAC 쿠키를 검증합니다.

- Secrets Manager 키: `{sharedProjectName}/session-signing-key` (기본 `agentic-work/session-signing-key`)
- 또는 환경변수 `SESSION_SIGNING_KEY`
- 미인증 시 agentic-work 로그인 URL로 안내

## ECS / ALB 연동 (agentic-work installer 확장)

1. **S3 Files**: 공유 버킷에 prefix `vault/` Access Point 생성 → ECS 태스크에 `/mnt/vault` 마운트
2. **ECS 서비스**: 이 이미지, 포트 `8502`, health `/vault/api/health`
3. **ALB listener rule**: path `/vault*` → ob-docs target group (default보다 높은 priority)
4. CloudFront default origin이 ALB이므로 `/vault`는 ALB rule만으로 전달됩니다.  
   (`/images|/docs|/artifacts`처럼 S3 signed path에 vault를 넣지 마세요 — vault **콘텐츠**는 S3 Files, SPA는 ECS)

환경변수 예:

```bash
APP_CONFIG_JSON='{...config.json...}'
SESSION_SIGNING_KEY=...   # agentic-work와 동일
VAULT_MOUNT=/mnt/vault
```

## agentic-work 연동 (향후)

현재는 별도 서비스입니다. 이후 agentic-work에서 `my-vault` skill / MCP로 이 vault의 노트를 CRUD할 예정입니다.

```text
agentic-work ──skill:my-vault──▶ ob-docs API ──▶ /mnt/vault (.md)
```

## Docker

```bash
docker build -t ob-docs .
docker run --rm -p 8502:8502 \
  -e ALLOW_LOCAL_AUTH_BYPASS=1 \
  -v "$PWD/data/vault:/app/data/vault" \
  ob-docs
```

## 배포 (shared agentic-work ALB)

```bash
python installer.py
```

배포 후 URL: https://cowork.my-agentic-ai.click/vault

installer가 수행하는 일:
1. ECR `ecr-for-ob-docs` 빌드/푸시
2. ALB rule `/vault*` → `TG-for-ob-docs` (CloudFront origin header 조건 유지)
3. ECS `service-for-ob-docs` on `cluster-for-agentic-work`
4. 샘플 vault를 `s3://…/vault/`에 seed (`VAULT_S3_ENABLE=1` sync)
