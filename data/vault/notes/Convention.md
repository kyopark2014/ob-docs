---
title: Vault convention (T-Box)
date: 2026-09-16
tags: [convention, t-box]
aliases: [규약, convention]
status: active
---

# Vault convention

## 폴더

- `00-Inbox/` — 수집함
- `notes/` — 정리된 노트
- `attachments/` — 이미지·PDF 등 바이너리
- `.vault/` — 앱 설정·캐시 (노트와 분리)

## Frontmatter

```yaml
title: string
date: YYYY-MM-DD
tags: [tag1, tag2]
aliases: [별칭]
status: draft | active | archived
```

## 링크

- `[[Note]]` — 노트 링크 (이름 기준)
- `[[Note#Heading]]` — 헤딩
- `[[Note|표시명]]` — 별칭 표시
- `![[image.png]]` — 임베드

시작점: [[Welcome]]
