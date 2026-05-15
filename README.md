# yeson-meet

사내 LAN AI 회의 통역 대시보드 (MVP-α)

## Architecture

회의실 PC의 Python sidecar가 시스템 오디오를 캡처해 사내 서버로 WSS 전송하면, FastAPI 게이트웨이가 Gemini Live API를 호출해 실시간 영→한 자막을 생성한다. 자막은 PostgreSQL에 저장되고 WebSocket Hub를 통해 viewer(브라우저/폰)와 사내 PyQt5 SDK 클라이언트로 fan-out된다. Gemini API Key는 서버에만 존재한다.

## Prerequisites

- Node 22 LTS
- pnpm 9
- Python 3.12
- uv
- Rust toolchain
- Docker

## Dev Commands

| 명령 | 설명 |
|---|---|
| `pnpm install` | Node 의존성 설치 |
| `uv sync --all-packages` | Python 의존성 설치 (워크스페이스 멤버 포함) |
| `cp deploy/env.example .env && $EDITOR .env` | 시크릿 채우기 (`JWT_SECRET`, `DB_PASSWORD`) |
| `pnpm --filter @yeson-meet/web build` | web dist 빌드 (Caddy 정적 호스팅 전제) |
| `docker compose --env-file .env -f deploy/docker-compose.yml up -d` | server + db + caddy 기동 |
| `pnpm --filter @yeson-meet/web dev` | web viewer 개발 서버 (:5173) |
| `pnpm --filter @yeson-meet/desktop tauri:dev` | 데스크톱 앱 개발 모드 |
| `uv run python -m apps.client_sidecar.main` | Python sidecar 실행 |

## Docs

- [PRD](docs/PRD.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)
- [Deploy](docs/DEPLOY.md)
- [Workflow (Solo + AI)](docs/WORKFLOW_SOLO_AI.md)
- [UI Design System](docs/UI_DESIGN_SYSTEM.md)

## Status

MVP-α Slice 0 bootstrap
