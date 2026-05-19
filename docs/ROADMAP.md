# ROADMAP — yeson-meet

> 최종 갱신: 2026-05-19
> 단위: 1인 풀타임 기준 영업일. 검토/대기 시간 제외.  
> **MVP-α** 5 슬라이스 + **MVP-β** 7 묶음으로 분리. β는 우선순위에 따라 선택적으로 진행.

---

## 마일스톤 개요

### MVP-α (필수, 5 슬라이스)

| Slice | 명칭 | 기간 | 산출물 |
|---|---|---|---|
| 0 | 부트스트랩 | 2~2.5일 | 모노레포 + Docker Compose 골격 + 사이드카 통신·배포 결정 락 |
| 1 | Fake fixture → viewer 1줄 | 2일 | 자막 1줄 끝단 통과 (오디오·AI 없음) |
| 2 | 실제 오디오 캡처 | 1~2일 | Windows Voicemeeter 우선 → 서버 청크 push |
| 3 | Gemini Live 통합 | 2.5~3.5일 | 실제 영어→한국어 자막 viewer 표시 + `STTProvider` 추상화 |
| 4 | 회의 라이프사이클 | 2.5~3.5일 | 회의 시작·종료 + 간소 MD 리포트 + 5슬롯 레이아웃·토큰·라우팅 placeholder |
| 5 | 운영 가능 | 2~3일 | 사용자/부서/visibility + 오프라인 큐 |

**MVP-α 총합: 약 12~16.5 영업일 (2.5~3.5주, 1인).** Slice 0의 사이드카 결정 락 +0.5일, Slice 3의 `STTProvider` 추상화 +0.5~1일, Slice 4의 UI 5슬롯·토큰·라우팅 placeholder 1차 정의 +0.5일 반영.

---

## 팀 참여 매트릭스 (비코딩 팀)

> 시스템 부원·번역 담당자·참석자가 슬라이스별로 어디서 참여하는지. 누구도 코드 만지지 않음.  
> 자세한 역할 / 피드백 메커니즘은 `docs/WORKFLOW_COLLABORATION.md` 참조.

| Slice | 시스템 부원 | 번역 담당자 (북극성) | PD/TD/Staff 참석자 |
|---|---|---|---|
| **0 부트스트랩** | `SETUP_SERVER` Phase 1~4 (HW/OS/Docker/IP) | (대기) | (대기) |
| **1 fake fixture** | Phase 5~6 (서버 기동, `/health` 200) | (대기) | (대기) |
| **2 실제 오디오** | Windows 회의실 PC 1대 audio 셋업 (Voicemeeter 우선) | (대기) | (대기) |
| **3 Gemini** | 회의실 PC Root CA 신뢰 등록 | 📚 **첫 자막 품질 검수** (영어 영상으로) | (대기) |
| **4 라이프사이클** | (관망) | 🎙️ **모의 회의 진행** + MD 리포트 검수 | 📱 시범 viewer 접속 |
| **5 운영** | 회의실 PC 추가 + 운영 모니터링 | 🎬 **실제 클라이언트 회의 시범** | 📱 정식 viewer 사용 |

→ 너는 슬라이스 끝낼 때마다 해당 팀원에게 핸드오프 (Gemini Key / Device API Key / 일정).

### MVP-β (이후, 우선순위 분리)

| 묶음 | 명칭 | 예상 |
|---|---|---|
| β-1 | 번역자 UX 묶음 (단축키 / 용어집 / 북마크 / 메모 / Slow-down / 되감기 / 첫 실행 가이드 / 모의 회의) | ~1주 |
| β-2 | 고시인성 5단계 폰트 시스템 + 회의실 모니터 모드 | 3~4일 |
| β-3 | 키워드 / 액션 추출 + viewer "더 보기" 토글 | 4~5일 |
| β-4 | 사내 SDK 첫 릴리스 + PyQt5 PoC | 3~4일 |
| β-5 | 인스톨러 + 코드사인 + 자동 업데이트 | 3~4일 |
| β-6 | Google / Dropbox 사용자별 opt-in 통합 | 1주+ |
| β-7 | 운영 자동화 (백업 cron / Prometheus / Grafana) | 3~4일 |

---

## Slice 0 — 부트스트랩 (2~2.5일)

> 모든 디렉토리·파일 골격이 존재하고 `docker compose up`이 통과 + **사이드카 결정 2건이 PRD §10에 박힘**이면 합격.

### 산출물 (실제 파일/디렉토리)
- 루트: `pnpm-workspace.yaml`, `pyproject.toml` (uv workspace), `.gitignore`, `README.md`
- `apps/server/` — FastAPI 빈 `/health`, 기본 디렉토리(`api/`, `ws/`, `db/`, `ai/`, `integrations/`)
- `apps/web/` — Vite + React + TS + Tailwind, "Hello yeson-meet" 페이지
- `apps/desktop/` — Tauri 2 + React, 빈 윈도우
- `apps/client_sidecar/` — Python entrypoint(`main.py`), 기본 디렉토리(`audio/`, `transport/`, `queue/`)
- `packages/sdk-python/` — `yeson_meet_sdk/__init__.py` 빈 모듈
- `packages/ui/` — 빈 React 컴포넌트 패키지
- `deploy/docker-compose.yml` (postgres + server + caddy)
- `deploy/Caddyfile` (`tls internal` 또는 mkcert)
- `deploy/env.example`
- `apps/server/db/alembic.ini` + `versions/` 빈 폴더
- **결정 락 2건 PRD §10에 박기**: ① 사이드카 ↔ 데스크톱 통신 방식 (Tauri IPC vs 127.0.0.1 WS) ② 사이드카 배포 방식 (uv dev / PyInstaller / Tauri externalBin)

### 완료 기준 (tag `web_test`, 2026-05-15)
- [x] `docker compose up -d` → `curl -sk https://<SERVER_IP>/api/v1/health` returns `{"status":"ok"}`
- [x] `pnpm --filter desktop tauri dev` → 빈 윈도우 뜸
- [x] `pnpm --filter web dev` → "Hello yeson-meet" 페이지 브라우저에서 보임
- [x] `uv run python -m apps.client_sidecar.main` → "sidecar started" 콘솔 출력
- [x] `docker compose down -v` 후 다시 up — 정상 기동
- [x] 🔴 사이드카 통신·배포 결정 2건 PRD §10 결정 로그에 반영됨

---

## Slice 1 — Fake fixture → viewer 자막 1줄 (2일)

> 오디오·AI 없이 가짜 자막이 viewer에 도달하면 합격. 전송 골격 검증용.

### 산출물
- DB 마이그레이션 (Alembic): `app_user`, `device`, `session`, `session_token`, `utterance`
- 어드민 1명 / Device 1개 / Token 1개 시드 스크립트
- `/api/v1/auth/login` JWT 발급
- `/api/v1/devices` POST (관리자, API Key 평문 1회 반환)
- `/api/v1/sessions` POST (운영자, viewer token 반환)
- WS `/ws/sidecar` (Device Key 인증)
- WS `/ws/viewer?token=...` (읽기 전용, 도메인 이벤트 fan-out)
- Sidecar: 가짜 자막 발생기 (1초마다 `utterance.transcribed` JSON 발행)
- Viewer: WS 구독 → 자막 1줄 표시

### 완료 기준 (tag `s1_test`, 2026-05-15)
- [x] Sidecar 실행 → 1초마다 viewer에 자막 텍스트가 ≤ 200ms 안에 표시
- [x] DB의 `utterance` 테이블에 발화가 누적
- [x] viewer 새로고침 시 마지막 N개 복원 (단순 GET `/api/v1/sessions/{id}/utterances`)

---

## Slice 2 — 실제 오디오 캡처 (1~2일)

> 가짜 fixture를 실제 오디오 청크로 교체. AI는 아직 없음.

### 산출물
- Sidecar: `sounddevice` 통합, 16kHz mono 청크 분할(20ms 프레임)
- Windows: Voicemeeter Banana 입력 자동 감지 1순위, VB-Cable 대체 가능
- Mac: BlackHole 입력 자동 감지는 2순위 검증 경로로 유지
- WS `/ws/sidecar`에 binary 오디오 청크 push + 제어 JSON
- 서버: 청크 수신 → 단순 카운트 로깅 (총 바이트 / 초당 청크 수)
- 테스트 페이지: "초당 N 청크 수신 중 / 총 X MB"

### 완료 기준 (tag `s2_test`, 2026-05-18 코드 ship)
- [ ] Windows 회의실 PC에서 영어 영상 1분 재생 → 서버 로그에 약 3000개 청크(20ms × 50/sec × 60s) 수신  ← **시스템 부원 협조 단계 대기 (PRD §10 락)**, SETUP_MEETING_PC.md §2 placeholder
- [x] Mac에서 동일 통과 (MVP-α 2순위, 일정 압박 시 β로 이월 가능)  ← **Intel Mac + BlackHole S2 PoC 통과 (2026-05-18)**: session `8390b139-5ff9-42f7-95ac-0c4aa8047c02`, AI-run `chunks_per_sec_1s=50~51` / `total_chunks=3522`, 사용자 직접 재현 `chunks/sec=49~51` / `total_chunks=26306`, 절차는 SETUP_MEETING_PC.md §1.1~1.4
- [~] 네트워크 끊김 5초 → 큐 누적 → 복구 시 흘림 없이 재전송  ← **부분 구현**: sidecar audio_ws 지수 백오프 재연결(1→30s) + 메모리-only `asyncio.Queue` (≈40s 버퍼) + drop counter 로그. 큐 영구화는 **Slice 5 SQLite로 위임** (audio_ws.py docstring + PRD §10 락 명시)

> 코드/문서 ship 상태: sidecar pytest 14/14, server pytest 4 passed + 4 skipped (binary 테스트 deadlock은 [TODO(s3-test-infra)](apps/server/tests/test_ws_sidecar_binary.py)). code-reviewer P0 0건·P1 3건 fix 반영, verifier APPROVE.

---

## Slice 3 — Gemini Live 통합 (2.5~3.5일)

> 실제 영어 음성 → 한국어 자막. Gemini API Key는 **서버에만**.

### 산출물
- [x] 서버 `apps/server/ai/providers.py` — `STTProvider` / `TranslationProvider` 인터페이스 (ARCH §2.3.1)
- [x] 서버 `apps/server/ai/gemini_live.py` — `GeminiLiveProvider` 구현체 (`google-genai` WebSocket 클라이언트)
- [~] 회의 세션별 Gemini WS 유지 (재연결 + 백오프)  ← `AudioLiveSession` provider disconnect retry/backoff 구현·테스트 완료, 실제 Gemini 장기 세션 회전은 실측 전
- [x] 청크 → Provider → 응답 파싱 → `utterance.transcribed` 이벤트 발행  ← 실제 Gemini key 기반 local synthetic E2E 통과(2026-05-18, 서버+테스트 sidecar 동일 개발 머신), 59.37초 synthetic 영어 오디오 8발화 → viewer seq 1~8 / DB utterance 8개 저장. LAN 회의실 PC↔서버 분리 실측은 완료 기준에 별도 유지
- [x] 시스템 프롬프트 정적 (영→한, 2줄 캡, 기술 용어 영문 유지)
- [~] 비용/지연 단순 로그 (Prometheus는 β-7)  ← AI publish latency structured log + Gemini usage token/cost structured log 완료, 실제 Gemini 응답 usage metadata/E2E 비용 검증은 미완료
- [x] **latency budget 4구간 분해 문서**: 캡처→서버 WSS / 서버→Gemini / Gemini→파싱 / 서버→viewer (P50 ≤ 2초 미달 시 partial subtitle 전략 즉시 도입)  ← ARCH §5.3 문서화 + local synthetic E2E 계측 완료: phrase-end→first viewer subtitle P50 1419.8ms / max 1522.3ms, server→viewer P50 5.2ms. 실제 LAN 구간은 회의실 PC 분리 검증 필요
- [x] 🔴 **API Key health check** — 서버 시작 시 + 실패 시 운영자 알림 (ARCH §12.3)  ← `/api/v1/health/ai` + startup log + `/api/v1/operator/alerts` critical alert 완료
- [~] 🔴 **회의 시간 안전 타이머** — 회의당 최대 N시간 (기본 3h) 도달 시 자동 종료 + alert (좀비 세션 비용 방지)  ← sidecar 오디오 ingress에서 `YESON_MEETING_MAX_DURATION_HOURS` 초과 세션 자동 종료 + operator alert 단위 검증 완료, background scheduler/3시간 E2E는 미완료
- [~] 🟡 **partial→final 자막 안정화** — `is_final` 플래그 + viewer가 `seq` 키로 마지막 partial 교체  ← viewer state upsert + Gemini provider seq 재시작 보정(`AISequenceNormalizer`) 검증 완료, 1분 E2E에서 seq 1~8 partial/final 수신·DB 저장 완료. 브라우저 시각 깜빡임 E2E는 미완료
- [~] 🟡 **VAD 또는 RMS 임계값으로 무음 청크 차단** (비용 절감)  ← sidecar RMS silence gate 구현·단위 검증 완료 (`YESON_RMS_DBFS_THRESHOLD`, `YESON_RMS_SILENCE_GATE_ENABLED`), 실제 회의실 threshold 튜닝 미완료
- [x] 시스템 프롬프트에 "혼합 언어 한국어 그대로 두기" 명시

> 2026-05-18 코드 진행: `STTProvider`/`TranslationProvider` 인터페이스, `GeminiLiveProvider` lazy SDK adapter, sidecar audio→AI session wiring, AI utterance DB persist + viewer bus fan-out, Gemini config health endpoint, provider disconnect retry/backoff, AI publish latency structured log, Gemini usage token/cost structured log, viewer partial→final seq replacement, Gemini Live model/env 정정(`gemini-3.1-flash-live-preview`), AUDIO + `output_audio_transcription` 기반 실시간 transcript fan-out, `audio_stream_end` 전달, provider seq 재시작 보정, sidecar RMS silence gate 구현. 검증: `uv run pytest apps/server/tests -v` → 22 passed / 4 skipped, `uv run pytest apps/client_sidecar/tests -q` → 18 passed, `pnpm --filter @yeson-meet/web build` → pass, `git diff --check` → clean. 실제 Gemini API Key 기반 local synthetic E2E(서버+테스트 sidecar 동일 개발 머신)에서 59.37초 synthetic 영어 오디오 viewer seq 1~8 / DB utterance 8개 저장, phrase-end→first viewer subtitle P50 1419.8ms / max 1522.3ms. 단, LAN 회의실 PC↔서버 분리 지연·브라우저 렌더·실제 오디오 라우팅은 아직 별도 검증 필요.
>
> 2026-05-19 완료: Windows 앱 패키지 전 사전 점검용 desktop setup assistant를 `apps/desktop/src/setup/` 모듈 구조로 구현하고, `App.tsx`는 `SetupAssistant`만 렌더링하도록 유지. setup assistant에서 서버 `/api/v1/health`, Gemini `/api/v1/health/ai`, viewer URL 접근 최소 스모크를 제공하며, Device API Key는 localStorage에 저장하지 않고 PowerShell 명령 값은 escaping 처리. Tauri packaged origin(`http://tauri.localhost`)을 서버 CORS allowlist에 추가. 검증: `pnpm --filter @yeson-meet/desktop build:vite` → pass, `uv run pytest apps/server/tests -q` → 25 passed / 4 skipped, desktop LSP diagnostics → 0 errors, `GIT_MASTER=1 git diff --check -- apps/desktop/src apps/server/main.py` → clean.

### 완료 기준
- [x] 영어 1분 synthetic 오디오 → 한국어 자막 viewer에 흐름  ← local synthetic E2E 통과: viewer WS 16개 partial/final 이벤트 수신, DB utterance seq 1~8 저장
- [~] 실제 회의실 PC↔서버 LAN 분리 환경에서 영어 1분 영상 재생 → 한국어 자막 viewer에 흐름  ← Mac BlackHole 청크 전송은 검증 완료(S2), Gemini 포함 LAN 분리 E2E는 **Windows 앱 패키지/실행 UX가 나온 뒤 진행**. 현재는 CLI 복잡도가 높아 desktop setup assistant로 서버 health, Gemini health, viewer URL 최소 스모크까지만 확인
- [x] local synthetic 자막 지연 P50 ≤ 2초  ← wall-clock phrase-end→first viewer subtitle P50 1419.8ms / max 1522.3ms, server→viewer P50 5.2ms / max 82.4ms
- [ ] LAN 분리 환경 자막 지연 P50 ≤ 2초  ← Windows 회의실 앱으로 실제 운영 경로가 단순화된 뒤 측정
- [~] Gemini 세션 끊김 → 5초 안에 재연결, 자막 일부 손실 외 회의 진행 유지  ← provider disconnect retry 단위 검증 완료, 실제 Gemini WS 끊김 E2E 미완료
- [ ] 좀비 회의 자동 종료 (3시간 도달 테스트)
- [~] partial→final 갱신 시 viewer 깜빡임 없음  ← 동일 `seq` final이 partial을 교체하는 상태 로직 검증 완료, 브라우저 시각 E2E 미완료

---

## Slice 4 — 회의 라이프사이클 (2.5~3.5일)

> 회의 1회를 처음부터 끝까지 운영 가능 + UI 확장성 1차 자리 박기.

### 산출물
- 데스크톱 UI:
  - 로그인 화면
  - 회의 시작 화면: 제목 / 클라이언트 라벨 / visibility (기본 `org`)
  - 라이브 콘솔: **자막 패널만** (큰 글씨 기본값)
  - 회의 종료 버튼
- **UI 확장성 1차 정의** (`docs/UI_DESIGN_SYSTEM.md` 참조):
  - `packages/ui/layout/AppShell.tsx` + `ConsoleShell.tsx` — 5슬롯 컴포지션
  - `packages/ui/src/tokens.css` — 디자인 토큰 1차 (color / spacing / typography 5단계)
  - 라우팅 placeholder: `/console/{history,glossary,admin}` "준비 중" 페이지 + nav에 disabled 항목
  - zustand 단일 store 스켈레톤 (`useMeetingStore`, `useUiStore`)
- QR + URL 표시 (PIN은 β로 보류)
- WS `/ws/operator` (JWT 인증, 운영자 전용)
- `/api/v1/sessions/{id}/end` → MD 리포트 생성 (영어 원문 + 한국어 번역 로그)
- `/api/v1/sessions/{id}/report` 다운로드
- viewer: 회의 종료 시 "회의 종료됨" 화면
- 🔴 **좀비 세션 자동 종료** — sidecar disconnect 감지 후 N분(기본 5분) 미복귀 시 서버가 세션 강제 종료 (ARCH §12.4)
- 🔴 **회의 종료 시 큐 flush + timeout** — 사이드카 잔여 큐를 종료 명령 후 최대 30초 대기, 이후 강제 종료 + 로그
- 🔴 **같은 Device 동시 회의 방지** — 활성 세션 1개 제약, UI에 "라이브 중" 표시
- 🟡 **MD 리포트 streaming 생성** — 1시간+ 회의에서 메모리 안전
- 🟡 **QR 회의실 모니터 전체화면 모드** — 거리 2~4m에서 폰 스캔 가능

### 완료 기준
- [ ] 30분 모의 회의(YouTube 영어 영상)를 시작 → 진행 → 종료
- [ ] MD 리포트 다운로드 → 발화 시간순 정렬 + 영어/한국어 매칭
- [ ] viewer 3대(PC/폰/태블릿) 동시 자막 정상
- [ ] 운영자 앱 강제 종료 시 5분 후 서버가 좀비 세션 정리
- [ ] 회의 종료 직후 sidecar 잔여 청크가 리포트에 포함됨

---

## Slice 5 — 운영 가능 (2~3일)

> 다중 회의실, 권한 격리, 끊김 견딤.

### 산출물
- DB: `department`, `app_user.department_id`, `app_user.role`
- 부서 시드 (시스템·번역·PD·TD·Staff)
- `visibility` 검증 미들웨어 (`org` / `dept:{ids}` / `private`)
- 오프라인 큐: Sidecar SQLite에 청크 영구 저장 → 복구 시 idempotent 재전송 (서버 측 `(session_id, seq)` UNIQUE)
- 회의실 PC 2대 + **viewer 30 conn 부하 시뮬**(Locust 또는 `websockets` 스크립트, 1대 PC에서 동시 연결) + **실 viewer 5~8대 병행** 검증 — 시뮬은 서버 CPU·메모리·WS pool·DB write 부하 측정, 실측은 모바일 Wi-Fi suspend / iOS Safari WS 재연결 / 자막 가독성 확인용
- 🔴 **viewer 백그라운드 복귀 시 따라잡기** — `?since=<seq>` 쿼리로 누락 자막 backfill (탭 비활성 → focus)
- 🔴 **idempotency 확정** — `(session_id, seq) UNIQUE` + ON CONFLICT DO NOTHING로 중복 청크 안전 처리
- 🔴 **끊김 30분+ 시 비상 WAV dump** — 큐가 임계 도달 시 로컬 WAV 저장 + 운영자 alert
- 🟡 **권한 스냅샷** — 진행 중 회의는 시작 시점 권한 고정 (회의 중 부서 이동해도 viewer 안 끊김)
- 🟡 **연결 카운트 / IP·UA 로깅** — 같은 토큰 다중 viewer 추적

### 완료 기준
- [ ] 회의 A(시스템 부서 visibility) → 번역 부서 사용자가 접근 거부 확인
- [ ] 회의실 PC 네트워크 5분 끊김 → 복구 시 자막 누락 0
- [ ] 동시 회의 2건 + viewer 30 conn 시뮬 + 실 viewer 5~8대 → CPU/메모리/지연/큐 길이 정상 (PRD §8 비기능 30명 목표 충족)
- [ ] viewer 탭 백그라운드 1분 후 복귀 → 누락 자막 자동 backfill
- [ ] 같은 청크 재전송 시 DB 중복 없음 (UNIQUE 검증)
- [ ] 끊김 35분 후 비상 WAV 생성 + 복구 시 일괄 업로드 + 재처리

---

## MVP-α 검증 후 결정 지점

Slice 5 통과 시점에 다음을 결정한 뒤 β로 진입:

1. 실제 회의 1주일 시범 운영 → 번역 담당자 피드백 수집
2. β 우선순위 재정렬 (담당자 피드백이 β-1 우선이라면 그쪽부터)
3. Gemini Live 실측 비용 → 운영 예산 책정
4. 사내 서버 부하 — 미니PC로 충분한지

---

## MVP-β 묶음별 상세

### β-1 — 번역자 UX 묶음 (약 1주)

> §1.1 북극성 비전 구현체. MVP-α에선 의도적으로 제외했던 항목.

- [ ] 한 키 단축키 시스템 (Space / B / N / S / G / R / ⌘± / ⌘E)
- [ ] `glossary_term` 테이블 + 관리 UI
- [ ] Gemini 프롬프트에 사용자별 용어집 동적 주입
- [ ] `bookmark` 테이블 + B 키 + 회의록 강조
- [ ] `note` 테이블 + N 키 + 회의록 포함
- [ ] "Please slow down" 카드 + S 키 + viewer 동기 표시
- [ ] 10초 되감기 UI (R 키)
- [ ] 첫 실행 1분 가이드 (스킵 가능)
- [ ] Voicemeeter 자동 감지 + Windows 가이드 다이얼로그 1순위, BlackHole 가이드는 2순위
- [ ] 모의 회의 모드 (Gemini 없이 fixture 재생)
- [ ] 에러 메시지 카피라이팅 (사람 언어화)
- [ ] AI 지연 graceful 처리 (자막 fade, 화면 안 멈춤)

### β-2 — 고시인성 5단계 폰트 시스템 (3~4일)

> MVP-α는 "큰 글씨 기본값"만. β-2에서 동적 토글 + 회의실 모드.

- [ ] 자막 5단계 (S 24 / M 32 / **L 44** / XL 56 / XXL 72px)
- [ ] 회의실 대형 모니터 모드 (XXL 강제 + 사이드 폴딩)
- [ ] Pretendard / Noto Sans KR 임베드
- [ ] WCAG AAA 7:1 대비 검증
- [ ] 12px 미만 사용 금지 (디자인 토큰 강제)
- [ ] 사용자별 폰트 선호 저장·복원
- [ ] 폰 viewer 글자 크기 토글 (22 / 28 / 32px)

### β-3 — 키워드 / 액션 추출 + viewer 토글 (4~5일)

> MVP-α viewer는 자막만. β-3에서 추출 + "더 보기" 토글.

- [ ] Gemini 응답에 키워드/액션 필드 추가
- [ ] 키워드 5카테고리 분류 (`schedule` / `retake` / `approval` / `issue` / `asset`)
- [ ] 액션 아이템 추출 (assignee / due 옵션)
- [ ] `keyword`, `action_item` 테이블 + 마이그레이션
- [ ] WS Hub: `keyword.detected`, `action.detected` fan-out
- [ ] 데스크톱 라이브 콘솔: 키워드 / 액션 / 로그 / 상태 사이드 패널
- [ ] Viewer "더 보기" 토글 (우상단) → 키워드/액션/로그/상태 펼침
- [ ] localStorage 토글 상태 저장·복원
- [ ] PIN 입력 페이지 + 모바일 UX (PIN도 여기서 추가)

### β-4 — 사내 SDK 첫 릴리스 (3~4일)

- [ ] `MeetingClient` 핵심 API (WebSocket 구독)
- [ ] `events` 모듈 (DomainEvent dataclass)
- [ ] `qt_bridge.QtMeetingBridge` (PyQt5 signal/slot)
- [ ] 토큰 관리 (env / keyring)
- [ ] 자동 재연결 + 백오프
- [ ] 예제 PyQt5 위젯
- [ ] 사내 PyPI 또는 `git+ssh://` 배포
- [ ] semver 0.1.0 태그
- [ ] 사내 PyQt5 툴 1개 선정 + PoC 통합

### β-5 — 인스톨러 + 자동 업데이트 (3~4일)

- [ ] Tauri MSI 빌드 스크립트 1순위, DMG 빌드 스크립트 2순위
- [ ] PyInstaller로 sidecar 단일 실행파일
- [ ] macOS 코드사인 + 노타리제이션
- [ ] Windows 코드사인
- [ ] Tauri Updater (GitHub Releases 또는 사내 정적 서버)
- [ ] 사내 root CA 인증서 배포 자동화

### β-6 — Google / Dropbox 사용자별 opt-in (1주+)

- [ ] OAuth 공통 모듈 (Testing 모드 우선)
- [ ] Google Calendar Inbound — 회의 매칭 (가치 1순위)
- [ ] Gmail App Password SMTP — 사용자별 발송 (가치 2순위)
- [ ] Google Drive 폴더 동기화 (선택)
- [ ] Dropbox 폴더 동기화 (선택)
- [ ] 통합 설정 GUI (사용자 단위)

### β-7 — 운영 자동화 (3~4일)

- [ ] nightly 백업 cron (pg_dump + storage rsync → NAS)
- [ ] 90일 보관 만료 cron (오디오 파일 삭제)
- [ ] Prometheus exporter (FastAPI middleware)
- [ ] Grafana 대시보드 (활성 세션 / viewer 동시 접속 / AI 지연 / 큐 길이)
- [ ] 알림 (헬스 체크 실패 시 사내 메신저 webhook)

---

## Phase 5+ — 외부 배포 / 고도 기능 (장기 보류)

- [ ] Tunnel 모드 (Cloudflare Tunnel)
- [ ] HTTPS 공개 도메인 + 외부 인증 모델 (사내 SSO 또는 매직링크)
- [ ] Google OAuth verification (Production 모드)
- [ ] MCP 익스포트 (Claude/외부 AI가 회의 컨텍스트 호출)
- [ ] 화자 자동 구분 (diarization)
- [ ] 양방향 마이크 캡처 + 에코 제어
- [ ] 회의 검색 (full-text)
- [ ] 모바일 네이티브 viewer (Capacitor 또는 RN)
- [ ] 다국어 회의(영중일혼합) 지원

---

## 위험 / 미해결 항목

| 항목 | 상태 | 비고 |
|---|---|---|
| Gemini Live 시간당 비용 추정 | ⚠️ 미산정 | Slice 3 후 실측 |
| 사내 서버 하드웨어 결정 | ⚠️ 미결정 | 미니PC vs 사내 가상화 |
| 회의 음성 보관 법무·HR 검토 | ⚠️ 미완료 | 외부 배포 전 필수 |
| Apple 노타리제이션 사전 검증 | ⚠️ β-5 시 | 인증서 준비 |
| 사내 SDK 첫 통합 대상 PyQt5 툴 선정 | ⚠️ 미결정 | β-4 시작 전 |
| 회의실 PC OS 표준화 | ✅ 결정 | Windows 회의실 PC 1순위, Mac 2순위 |
| 사내 SSO 존재 여부 | ⚠️ 미확인 | 없으면 자체 JWT 유지 |
