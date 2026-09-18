# ob-docs

Obsidian형 **Local-first Plain Text** vault 웹 앱입니다.  
노트는 `.md`가 Source of Truth이고, 설정은 `.vault/`에 격리되며, 그래프·검색·백링크는 파생 캐시입니다.

agentic-work와 **같은 CloudFront / ALB / S3 버킷**을 공유할 수 있습니다.  
agentic-work가 없어도 `python installer.py`만으로 공유 인프라를 만들고 `/vault`를 배포할 수 있습니다.

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
- **s3 모드 (옵션)**: `VAULT_S3_ENABLE=1`일 때 로컬 ↔ `s3://{bucket}/vault/` sync
  - 저장/삭제 시 pending 큐(`.vault/pending_s3_ops.json`, S3에도 미러)에 쌓은 뒤 flush
  - Settings **Sync**: pending 업로드를 먼저 끝낸 다음, S3에서 **변경분만** 내려받음
  - 부팅 시에도 pending flush → incremental pull 순서

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
    ├── shares.json         # public 공유 토큰 레지스트리
    ├── graph.json          # 파생 그래프
    └── cache/              # 인덱스 (삭제 후 재생성)
```

위키링크 `[[Note]]`, frontmatter(`aliases`, `tags`)를 파싱해 그래프·검색·백링크를 만듭니다.

**좌측 rail → Graph** (agentic-work Wiki 메뉴를 Notes로 이식)

| 메뉴 | 동작 |
|---|---|
| Sync | vault markdown 위키링크 인덱스를 갱신하고 Notes Graph HTML 생성 |
| Rebuild | 캐시를 비우고 전체 재빌드 |
| Graph | Notes Graph 모달 (agentic-work와 동일한 Force Atlas / Neo4j Explore / Holistic View · 검색·범례) |
| Configure | 포함할 폴더·미해결 링크 표시 설정 |

## API

| Method | Path | 설명 |
|---|---|---|
| GET | `/vault/api/health` | 헬스체크 |
| GET | `/vault/api/session` | 공유 세션 확인 |
| GET | `/vault/api/files/tree` | 파일 트리 |
| GET | `/vault/api/files/list?prefix=&ext=` | 플랫 파일 목록 |
| GET | `/vault/api/files/read?path=` | 노트 읽기 |
| PUT | `/vault/api/files/write` | 노트 저장(덮어쓰기) |
| POST | `/vault/api/files/append` | 노트 이어쓰기 |
| POST | `/vault/api/files/sync` | pending flush 후 S3→로컬 incremental pull |
| GET | `/vault/api/files/sync` | pending 큐 상태 |
| POST | `/vault/api/files/share` | 노트 public 공유 링크 생성/재사용 |
| GET | `/vault/api/files/shares` | 공유 목록 |
| POST | `/vault/api/files/share/delete` | 공유 토큰 삭제 |
| GET | `/vault/s/{token}` | **공개** markdown viewer (쿠키 불필요) |
| GET | `/vault/s/{token}/raw?path=` | **공개** 상대 이미지/첨부 |
| POST | `/vault/api/files/mkdir` | 폴더 생성 |
| POST | `/vault/api/files/rename` | 이동/이름변경 |
| POST | `/vault/api/files/delete` | 삭제 |
| GET | `/vault/api/search?q=` | 검색 |
| GET | `/vault/api/graph` | Notes 위키링크 그래프 JSON |
| GET | `/vault/api/graph/status` | Notes 그래프 동기화 상태 |
| POST | `/vault/api/graph/sync` | Notes Sync (`?full=1` = Rebuild) |
| POST | `/vault/api/graph/rebuild` | Notes 전체 재빌드 |
| GET | `/vault/api/graph/graph` | Notes Graph HTML (iframe) |
| PATCH | `/vault/api/graph/pattern` | 그래프 뷰 패턴 전환 |
| GET/PUT | `/vault/api/graph/sources` | Notes Configure (포함 폴더) |
| GET | `/vault/api/graph/backlinks` | 백링크 |
| GET | `/vault/api/agent/health` | Open Agent harness 설정 여부 |
| GET | `/vault/api/agent/models` | 선택 가능한 모델 목록 |
| GET | `/vault/api/agent/note-meta?path=` | 에이전트 칩용 노트 메타 |
| POST | `/vault/api/agent/chat` | Open Agent SSE (`token` / `tool` / `note_updated` / `done`) |

인증: `agent_user_id` 쿠키, `Authorization: Bearer <session>`, 또는 AgentCore용 `Authorization: VaultAgent v1.<payload>.<sig>` (`agentic-work/vault-agent-token`).

- Secrets Manager 키: `{sharedProjectName}/session-signing-key` (웹 세션)
- Secrets Manager 키: `{sharedProjectName}/vault-agent-token` (my-vaults skill / AgentCore)
- 또는 환경변수 `SESSION_SIGNING_KEY` / `VAULT_AGENT_TOKEN`
- 미인증 시 agentic-work 로그인 URL로 안내
- `/vault/s/*` 공개 viewer만 세션 없이 접근 가능

Open Agent 사용법·동작은 [Agent로 Note 수정하기](#agent로-note-수정하기)를 보세요.

## Agent로 Note 수정하기

선택한 마크다운 노트를 **Bedrock AgentCore InvokeHarness**로 요약·수정합니다.  
code interpreter는 쓰지 않고, 웹 검색(Exa)과 vault 저장 마커(`VAULT_WRITE`)만 사용합니다.

### 여는 방법

| 진입점 | 동작 |
|---|---|
| 노트 우클릭 → **Open agent** | 문서 창 오른쪽에 Agent 패널 오픈 |
| 문서 툴바 **Agent** 아이콘 | 현재 열린 노트로 동일하게 오픈 |

패널 UI는 agentic-work 채팅과 비슷한 톤(타임라인 · tool 카드 · 입력창)입니다.

### 구성 (Harness)

| 항목 | 내용 |
|---|---|
| Runtime | AgentCore **InvokeHarness** (`HARNESS_ARN`, installer가 `ob_docs` harness 생성) |
| Skill | **use-vault** — vault 경로·본문 규칙·저장 형식 지침 (S3 `skills/use-vault/`) |
| MCP / tools | **websearch** — Exa `remote_mcp` (`exa`만, code interpreter 없음) |
| 모델 | 좌측 rail 하단 **Model** 아이콘(Settings 바로 위)에서 선택 (기본 `Claude 4.6 Sonnet`, localStorage 저장) |
| 세션 | 채팅 `session_id`로 harness 대화 이어가기 |

`python installer.py`가 skill을 S3에 올리고 harness를 프로비저닝한 뒤 `HARNESS_ARN`을 config/ECS에 넣습니다.

### 요청 흐름

```text
UI (Agent 패널)
  → POST /vault/api/agent/chat  (SSE)
  → build_user_prompt (선택 노트 본문 + 첨부)
  → InvokeHarness (model override + exa + use-vault)
  → 스트림: token / text / tool / tool_result
  → 응답의 VAULT_WRITE 파싱 → vault 파일 저장
  → note_updated → 열린 탭 다시 로드
```

1. **선택 노트**: 입력창 칩으로 경로·크기가 보이고, 본문 전체가 프롬프트에 포함됩니다.
2. **모델**: rail Model에서 고른 display name이 `model_name`으로 전달되고, 서버가 Bedrock `modelId`로 변환해 InvokeHarness `model`에 넣습니다.
3. **웹 검색**: 필요 시 harness가 Exa MCP를 호출합니다. UI에는 harness-work 스타일 **tool / tool_result** 카드가 타임라인에 표시됩니다.
4. **최종 답변**: tool 카드 **아래**에 텍스트가 오도록 서버·클라이언트가 타임라인을 맞춥니다.

### 노트 저장 (`VAULT_WRITE`)

code interpreter가 없으므로 에이전트는 스크립트로 vault에 쓰지 않습니다.  
응답에 아래 마커를 넣으면 **ob-docs 서버**가 선택 노트 경로만 덮어씁니다.

```text
<<<VAULT_WRITE Meeting/Weekly-Sync.md>>>
# Weekly Sync

본문 전체 (YAML frontmatter 금지)
<<<END_VAULT_WRITE>>>
```

- 경로에 공백이 있어도 파싱됩니다.
- 선택 노트와 **다른 경로**로의 WRITE는 무시됩니다.
- 저장 후 UI에 `vault_write` tool 카드가 보이고, Preview/Edit 탭이 갱신됩니다.
- 스트리밍 중 불완전 마커·완성 마커 본문은 채팅에 노출되지 않습니다.

### 첨부 (사진 / Load files)

입력창 **+** 는 메시지 입력 전과 관계없이 항상 사용할 수 있습니다.

| 메뉴 | 동작 |
|---|---|
| **사진 첨부** | 이미지 선택 또는 Ctrl/⌘+V 붙여넣기 → **현재 노트와 같은 폴더**에 저장 → 미리보기 칩 |
| **Load files** | 문서/텍스트 등을 같은 폴더에 올린 뒤 칩으로 표시 |

전송 시:

- 이미지: Bedrock Converse로 설명을 뽑아 프롬프트에 합침 (InvokeHarness는 텍스트 전용)
- 텍스트형 파일: 본문을 프롬프트에 포함
- 바이너리: 경로·크기만 안내

파일명은 충돌 방지를 위해 `타임스탬프-원본이름` 형식으로 저장됩니다.

### Preview와 Mermaid

노트 Preview에서 mermaid 펜스 코드 블록(예: flowchart)은 다이어그램으로 렌더링됩니다. Edit 모드에서는 원문 그대로입니다.

### API 요약

| Method | Path | 설명 |
|---|---|---|
| GET | `/vault/api/agent/health` | harness·skill·모델 기본값 |
| GET | `/vault/api/agent/models` | 선택 가능 모델 목록 |
| GET | `/vault/api/agent/note-meta?path=` | 칩용 name/size |
| POST | `/vault/api/agent/chat` | SSE 채팅 (`prompt`, `note_path`, `session_id`, `model_name`, `image_paths`, `file_paths`) |

SSE 이벤트 예: `session`, `token`, `text`, `tool`, `tool_result`, `note_updated`, `done`, `error`.

## 노트의 public 공유

로그인된 사용자가 markdown 노트를 **쿠키 없이** 볼 수 있는 CloudFront URL로 공유합니다. agentic-work markdown viewer와 비슷한 HTML 페이지를 서버가 렌더합니다.

**UI**

1. 노트 우클릭 → **Share public link** → 새 탭에서 공개 페이지 오픈
2. Settings → **Shared List** → 제목 · 공유 시각 · URL 목록, **Link**(열기) / **삭제**

**노트 삭제·이동**

- 노트(또는 폴더) **삭제** 시 해당 경로의 public share 는 Shared List / `shares.json` 에서 함께 제거되고 S3 레지스트리에 즉시 반영됩니다. 공개 URL은 더 이상 열리지 않습니다.
- 노트 **이동·이름 변경** 시 share 경로가 새 위치로 갱신되고, 노트 본문도 S3에 다시 올려 CloudFront에서도 이어집니다.

**URL 형식** (`config.json`의 `sharing_url`)

```text
https://cowork.my-agentic-ai.click/vault/s/{token}
```

**생성 흐름** (인증 필요)

```text
POST /vault/api/files/share  { "path": "folder/Note.md" }
  → .vault/shares.json 에 token 등록 (같은 경로면 기존 token 재사용)
  → s3 모드면 shares.json 을 S3 vault/ 에도 반영
  → { url, url_path, token, title, created_at } 반환
```

레지스트리 예:

```json
{
  "shares": {
    "gvFUVyCeSgTS9g9n-qHC2nTv": {
      "path": "agent/A2A on AWS AgentCore.md",
      "title": "A2A on AWS AgentCore",
      "created_at": 1789570000.0
    }
  }
}
```

**접속 흐름** (인증 불필요)

```text
브라우저
  → CloudFront (sharing_url)
  → ALB /vault* → ob-docs ECS
  → GET /vault/s/{token}
  → shares.json 에서 token → vault 상대경로 조회
  → .md 를 HTML markdown viewer 로 반환
```

- 본문 상대 이미지(`![](img.png)`)는 `/vault/s/{token}/raw?path=…` 로 다시 쓰여 공개 제공됩니다.
- SPA catch-all(`/vault/{path}`)은 `api/`, `s/` 를 제외합니다. 공개 URL이 vault 앱 전체가 보이면 **구버전 배포**이거나 롤아웃 전일 수 있습니다.

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

## agentic-work 연동

agentic-work의 `my-vaults` skill이 이 API로 vault 노트를 CRUD합니다.

```text
agentic-work ──skill:my-vaults──▶ ob-docs API ──▶ /mnt/vault (.md)
```

## Docker

```bash
docker build -t ob-docs .
docker run --rm -p 8502:8502 \
  -e ALLOW_LOCAL_AUTH_BYPASS=1 \
  -v "$PWD/data/vault:/app/data/vault" \
  ob-docs
```

## 배포 (installer)

agentic-work와 **같은 리소스 이름**을 사용합니다. `config.json`이 없거나 일부만 있어도
`installer.py`가 부족한 공유 인프라를 만들어 준 뒤 ob-docs를 배포합니다.

```bash
python installer.py
```

배포 후 URL: `{sharing_url}/vault`  
(기존 환경 예: https://cowork.my-agentic-ai.click/vault)

### config.json

- 없으면 생성합니다. `accountId` / `region` / `s3_bucket` 등은 STS·기본값으로 채웁니다.
- 옆에 `../agentic-work/application/config.json`이 있으면 `s3_bucket`, `sharing_url`,
  `google_client_id` 등을 병합합니다.
- Google 로그인에는 `google_client_id`가 필요합니다 (비어 있으면 배포는 되지만 로그인 불가).

### installer가 수행하는 일

0. **공유 인프라 ensure** (`shared_infra.py`, agentic-work와 동일 네이밍)
   - S3 `storage-for-agentic-work-{account}-{region}`
   - Secrets: `agentic-work/cloudfront-alb-origin-header`, `session-signing-key`
   - IAM: `role-ecs-{task,execution}-for-agentic-work-{region}`
   - ECS cluster `cluster-for-agentic-work`
   - 기존 ALB/ECS가 없으면 최소 VPC + `alb-for-agentic-work` 생성
1. **skills 업로드** (`use-vault` → `s3://…/skills/`)
2. **AgentCore Harness** (`ob_docs`, skill=`use-vault`, MCP=`websearch`, **no code interpreter**) → `HARNESS_ARN`
3. ECR `ecr-for-ob-docs` 빌드/푸시
4. ALB rule `/vault*` → `TG-for-ob-docs`  
   (CloudFront HTTPS면 origin header 조건 유지, ALB HTTP면 path only)
5. ECS `service-for-ob-docs` on `cluster-for-agentic-work` (`APP_CONFIG_JSON`에 `HARNESS_ARN`)
6. 샘플 vault를 `s3://…/vault/`에 seed (`VAULT_S3_ENABLE=1` sync)

### 제거 (uninstaller)

```bash
python uninstaller.py
python uninstaller.py --yes
```

- **항상 삭제**: `service-for-ob-docs`, `TG-for-ob-docs`, `/vault*` listener rule, ECR, 로그 그룹, `vault-agent-token`
- **공유 중**(agentic-work 서비스가 살아 있으면) ALB·VPC·클러스터·S3·IAM·origin/session secret은 **유지**
- **공유가 없으면** installer가 만든 공유 이름 인프라까지 삭제
- 공유 중인데 vault 객체만 지우려면: `--purge-vault-prefix`
