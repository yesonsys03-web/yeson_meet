# PRD — AI 회의 통역 대시보드 (yeson-meet)

> 최종 갱신: 2026-05-14

---

## 1. 비전

해외 클라이언트 영상 미팅에서 참석자들이 회의 흐름을 놓치지 않도록 돕는 **AI 회의 이해 보조 시스템**.

> "완벽한 자동 통역기"가 아니라, **회의 내용을 실시간으로 구조화하는 보조 도구**.

## 1.1 북극성 사용자 — 번역 담당자 (NORTH STAR)

> ⚠️ **이 섹션은 비전이다.** 여기 나열된 단축키·용어집·북마크·메모·Slow-down 카드·10초 되감기·5단계 폰트 토글은 **MVP-β-1, β-2** 묶음이며 **MVP-α에는 포함되지 않음**. MVP-α는 §7 참조 — "큰 글씨 기본값" 정도만.

> 이 시스템의 단 한 명을 위해 설계한다면, 그건 **번역 담당자**다.

번역 담당자는 회의 중 동시에 4가지를 한다.

1. 영어 듣기
2. 한국어로 통역해서 말하기
3. 회의 흐름 파악
4. 중요 사항 메모

→ UI가 작업을 **추가**하면 안 된다. UI가 **덜어줘야** 한다.

### 사용성 목표 (단계별)

| 단계 | 목표 | 측정 |
|---|---|---|
| **회의 전** | 30초 안에 회의 시작 | "회의 시작" 클릭부터 QR 표시까지 ≤30s |
| **회의 중** | 자막에 집중. UI에 손 안 가도 됨 | 한 회의 동안 마우스 클릭 0회로도 운영 가능 |
| **회의 후** | 한 번에 정리/배포 | 회의 종료 → 리포트 미리보기 ≤3s |

### 번역자 도움 기능 (MVP-β 비전)

- 📚 **용어집(Glossary)** — 사내 자주 쓰는 영어 용어 → 한국어 매핑. Gemini 프롬프트에 자동 주입해 자막 일관성 확보.
- ★ **북마크(Bookmark)** — `B` 키로 현재 시점 마킹. 회의록에서 강조 표시. viewer에는 노출 안 됨(운영자 전용).
- 📝 **인라인 메모** — `N` 키로 현재 발화에 메모 첨부. 회의록 자동 포함.
- 🐢 **"Please slow down" 카드** — `S` 키로 회의실 모니터·viewer에 큰 시각 알림. 번역자가 말로 "잠시만요" 하지 않아도 됨.
- ⏪ **10초 되감기** — `R` 키로 최근 자막 다시 보기 (실제 오디오는 라이브 유지).
- 🎨 **자막 큰 화면 모드** — `⌘ +/−`로 글자 크기 조절. 회의실 모니터 거리에 맞춤.
- 🎓 **모의 회의 모드** — Gemini API 호출 없이 사전 녹음으로 단축키·UI 연습.

### 한 키 단축키

| 키 | 동작 |
|---|---|
| `Space` | 자막 일시정지 / 재개 |
| `B` | 북마크 (현재 시점) |
| `N` | 인라인 메모 |
| `S` | "더 천천히 부탁" 카드 표시 |
| `G` | 용어집 열기 |
| `R` | 10초 되감기 표시 |
| `⌘ +/−` | 자막 글자 크기 |
| `⌘ E` | 회의 종료 (확인 후) |

### 고시인성 디자인 (High Visibility) — 필수 요건

> 작업자 연령대를 고려, **모든 텍스트는 크고 굵게**. 큰 글씨는 옵션이 아니라 **기본값**.

| 환경 | 자막 기본 크기 | 권장 범위 |
|---|---|---|
| 운영자 콘솔 (거리 50~80cm) | **44px (L)** | 24 / 32 / 44 / 56 / 72px (S·M·L·XL·XXL) |
| 회의실 대형 모니터 (거리 2~4m) | **56~72px (XL~XXL)** | 사이드 패널 자동 폴딩 |
| 폰 viewer | **26px** | 22 / 28 / 32px |
| 노트북 viewer | **30px** | 24 / 30 / 36px |

**타이포그래피 원칙**
- 폰트: **Pretendard** 또는 **Noto Sans KR** (한글 가독성 최상).
- 자막은 항상 **Bold 700+**. 본문은 SemiBold (600).
- 어디서든 **최소 14px**. 12px 사용 금지.
- 대비비 **WCAG AAA (7:1)** 이상. 다크 모드 기본 (`#0f172a` 배경 + `#f1f5f9` 글씨).
- 줄간격 1.5+ (자막은 1.2~1.3 허용).
- 한 줄 글자 수 캡: 운영자 콘솔 ≤ 28자, 폰 ≤ 18자, 초과 시 자동 줄바꿈.
- `⌘ +/−` 폰트 5단계 즉시 토글.
- 회의실 대형 모니터 모드(XXL 강제 + 사이드 폴딩) 별도 단축키.
- 사용자별 폰트 크기 선호 저장. 다음 회의에서 자동 복원.

### 인지 부담 최소화 5원칙

1. **정상은 보이지 않는다** — 오디오·AI·서버 상태는 정상일 때 숨김. 문제일 때만 부각.
2. **자막이 메인, 나머지는 보조** — 자막 면적 70%+. 사이드 패널은 폴딩.
3. **한 키 우선, 마우스 보조** — 빈번한 작업은 모두 단축키. 마우스 없이도 운영 가능.
4. **모달 없음, 인라인 우선** — 메모/북마크/용어 모두 화면 안에서 즉시 처리. 흐름 끊지 않음.
5. **AI 지연을 인간이 모르게** — 이전 자막 유지 + 부드러운 인디케이터. 화면 깜빡임 X.
6. **회복은 한 번에** — 오류 시 1-click 복구 버튼. 기술 용어 X. 사람 언어로.

### 셋업·장애 자동화 (번역자 자력 해결)

- 첫 실행 시 Voicemeeter(Windows) **자동 감지** + 가이드 영상 링크. BlackHole(Mac)은 2순위 가이드로 유지.
- 서버 연결 실패 시 **"관리자에게 알리기"** 버튼 (사내 메신저 자동 통지).
- 마이크 무음 5초 이상 → 시각 경고 + 입력 장치 확인 가이드.
- AI 지연 ≥5초 → 자막 fade + 로딩 표시. 화면 안 멈춤.
- 회의 **자동 백업** — 실수로 종료해도 복구 가능.
- 에러 메시지는 모두 사람 언어. `"JWT expired"` X → `"다시 로그인이 필요해요"` O.

---

## 1.2 참석자 UX — 자막 우선, 토글로 확장 (Viewer)

> ⚠️ **MVP-α는 자막만.** 이 섹션의 "더 보기" 토글 / 키워드 / 액션 / 로그 패널은 **MVP-β-3** 묶음에 속하며 MVP-α에 없음. MVP-α viewer는 자막 풀스크린 + 큰 글씨 기본값만 제공하고, 동적 글자 크기 토글은 **MVP-β-2**에서 구현.

> 기본은 **자막 풀스크린**. 우상단 **"더 보기" 토글**로 키워드·액션·로그 펼침.

대부분의 참석자는 자막만 봐도 충분하다. 단, 더 자세히 보고 싶을 수 있는 사람을 위해 **토글 한 번**으로 확장.

### 기본 모드 (자막, MVP-α 기준)
- 자막 (크고 굵게, 다크 모드)
- 회의 제목 + LIVE 인디케이터 (미니 헤더)
- 큰 글씨 기본값 (폰 26px / 노트북 30px)

### MVP-β 확장
- 글자 크기 토글 (3단계, β-2)
- **"더 보기" 토글 버튼** (우상단, β-3)

### "더 보기" 토글 ON 시 추가 노출
- 핵심 키워드
- 액션 아이템
- 회의 로그 (최근 N개)
- 상세 상태 (오디오/AI/지연)
- 다시 토글 OFF → 자막 풀스크린 복귀

### 설계 근거
- 90%의 참석자는 자막만 보면 됨. 풀스크린이 기본인 이유.
- 일부 참석자(예: 후속 작업 책임자)는 키워드/액션을 즉시 확인하고 싶음. 토글로 해결.
- 화자 라벨은 MVP에서 제거 (화자 자동 구분 미구현).

### 연결 정책
- 서버는 viewer WebSocket에 **모든 도메인 이벤트** 전송 (utterance / keyword / action / status / session.*).
- 클라이언트가 토글 상태에 따라 표시만 제어. 별도 구독 필터링 없음 (MVP 단순화).
- 회의 종료 시 "회의 종료됨" 큰 메시지로 전환 후 자동 disconnect.
- 폰 자막 기본 26px / 노트북 30px (§1.1 고시인성 디자인 참조).
- 토글 상태는 사용자 브라우저 로컬에 저장. 다음 회의 진입 시 자동 복원.

---

## 2. 핵심 가치

- 해외 클라이언트의 영어 발화를 실시간으로 이해
- 통역 담당자의 부담 감소
- 핵심 키워드 / 일정 / 수정 요청 / 승인 사항 즉시 표시
- 회의 종료 후 자동 회의록 생성
- **회의 데이터의 사내 자산화** (검색·검토 가능)

## 3. 비목표 (의도적으로 안 하는 것)

- 완벽한 동시통역기가 아님. 의역/요약 우선.
- 본인 측 마이크 캡처 안 함 (MVP, 상대방 음성만).
- 외부 클라우드 저장 안 함. **모든 데이터 사내 서버.**
- 외부 클라이언트가 자기 폰으로 회의에 직접 참여하지 않음 (MVP, LAN-only).
- 화자 자동 구분(diarization) 안 함 (MVP).

## 4. 사용자 페르소나

| 페르소나 | 역할 | 주요 행동 |
|---|---|---|
| **운영자(PD / 번역부)** | 회의실 PC 데스크톱 앱 운영 | 시작·종료, 회의록 검수, 발송 트리거 |
| **참석자(시스템 / TD / Staff)** | 자기 PC 또는 폰으로 viewer 접속 | 실시간 자막·키워드·액션 시청 |
| **사내 PyQt5 툴 연동자** | 사내 자체 도구가 SDK로 우리 시스템 구독 | 액션 아이템 자동 반영, 회의 컨텍스트 push |
| **시스템 운영자** | 사내 서버 운영 | 백업·인증·계정 관리 |

## 5. 운영 시나리오

### 5.1 회의 시작 (MVP-α 기준)
1. 회의실 PC에서 데스크톱 앱(Tauri) 실행 후 로그인
2. "회의 시작" → 사내 서버에 세션 생성, **viewer 토큰** 발급
3. 데스크톱 UI에 **QR + 짧은 URL** 표시
4. 참석자가 QR 스캔 → 사내 서버 viewer URL 진입
5. (β-3 추가) **6자리 PIN** 표시 + viewer 페이지에서 PIN 입력 옵션

### 5.2 회의 중

> **오디오 캡처 경로 단계**:
> - **현재 (MVP-α)**: Windows + Voicemeeter / Mac + BlackHole — 본 §5.2 가 정의하는 경로
> - **계획 (Phase 1~2 native)**: BlackHole/Voicemeeter 없는 ScreenCaptureKit(Mac) / WASAPI(Win) 직접 캡처 — `docs/NATIVE_DESKTOP_HELPER_PLAN.md` + `docs/INTEGRATION_DESIGN.md` §3 참조. native 안정화 이후 §5.2 는 fallback 경로로 격하.
> - **진행 (2026-05-28)**: Phase 1 macOS native(ScreenCaptureKit) **E2E 기능 검증 완료** — Gemini 자막까지 실측, native 실패 시 권한 배너 포함. Windows(WASAPI)는 미구현. §5.2 fallback 격하는 native-only 컷오버 시점.

- 회의실 PC: **Windows + Voicemeeter를 MVP-α 1순위**로 시스템 오디오 수신. Mac + BlackHole은 2순위 검증 경로로 유지
- **오디오 청크를 사내 서버에 WSS push** (회의실 PC는 Gemini 직접 호출 X)
- 서버가 Gemini Live API 호출 → 자막 (MVP-α) / 키워드·액션 (β-3) 산출
- 서버: PostgreSQL 영구 저장 + viewer / SDK 클라이언트로 fan-out
- **MVP-α 참석자 화면**: 자막 풀스크린 (큰 글씨 기본)
- **β-3 추가**: viewer "더 보기" 토글로 키워드 / 액션 / 회의 로그 / 상태 패널 펼침

### 5.3 네트워크 끊김 (회의실 PC ↔ 서버)
- 회의실 PC가 결과를 **로컬 SQLite 큐**에 저장
- 복구 시 자동 재전송 (idempotent)
- 회의는 끊기지 않음. viewer는 끊김 동안 정지(timestamp gap 표시).

### 5.4 회의 종료
- 운영자가 "회의 종료" 클릭 → 서버에 종료 신호
- 서버가 **MD 리포트 생성** (요약·결정·액션·일정·키워드·원문 로그·번역 로그)
- 부서별 알림 (선택, 사내 SDK 또는 향후 통합)
- viewer는 "회의 종료됨" 화면 + (옵션) 리포트 다운로드 링크

## 6. 권한 모델

| 객체 | 속성 |
|---|---|
| **User** | id, name, email, department_id, role(owner/operator/viewer/admin) |
| **Department** | id, name (예: "시스템", "번역", "PD", "TD", "Staff") |
| **Session** | id, owner_user_id, title, visibility, started_at, ended_at |
| **Token** | session_id, kind(viewer/sdk/device), pin (β-3에서 사용), expires_at |

Session.visibility 값:
- `private` — owner와 명시 초대자만
- `dept:{ids}` — 지정 부서 전체
- `org` — 사내 전체 (기본값)

## 7. MVP 스코프 — α / β / OUT 분리

> **원칙**: MVP-α는 회의 1회를 끝까지 돌릴 수 있는 최소 슬라이스만. 번역자 UX 고급 기능·통합·viewer 토글·고시인성 토글 등은 MVP-β로 명시 분리. OUT은 영구 제외.  
> 상세 작업·완료 기준은 `docs/ROADMAP.md` 참조.

### MVP-α (5 슬라이스 · 약 2.5~3.5주, 1인 기준)

#### Slice 0 — 부트스트랩 (2~2.5일)
- 모노레포 골격 (`pnpm + uv workspace`)
- `apps/server` FastAPI 빈 `/health`
- `apps/web` Vite + React + Tailwind 빈 페이지
- `apps/desktop` Tauri 빈 윈도우
- `apps/client_sidecar` Python entrypoint
- `packages/sdk-python`, `packages/ui` 빈 패키지
- `deploy/docker-compose.yml` (postgres + server + caddy) + Caddyfile + env.example
- Alembic 초기화
- 사이드카 결정 락 2건: 데스크톱 통신 방식(Tauri IPC vs `127.0.0.1` WS), 배포 방식(uv dev / PyInstaller / Tauri `externalBin`)

#### Slice 1 — Fake fixture → viewer 자막 1줄 (2일)
- 서버 WS `/ws/sidecar` (Device Key), `/ws/viewer` (Token)
- 도메인 이벤트 1개 (`utterance.transcribed`)
- 사이드카: 가짜 자막 발생기 (1초마다 sample text)
- viewer: WS 구독 → 자막 1줄 표시
- DB: `session`, `utterance`, `app_user`, `device`, `session_token` 5개 테이블
- 어드민 1명 + Device 1개 + Token 1개 시드

#### Slice 2 — 실제 오디오 캡처 (1~2일)
- 사이드카 `sounddevice` 통합 — **Windows Voicemeeter 입력 우선**, Mac BlackHole 입력은 2순위
- 16kHz mono, 20ms 청크 → 서버 push
- 서버는 stub으로 청크 카운트만 로깅 (Gemini 아직 없음)

#### Slice 3 — Gemini Live 통합 (2.5~3.5일)
- 서버 `apps/server/ai/providers.py` — `STTProvider` / `TranslationProvider` 인터페이스 (ARCH §2.3.1)
- 서버 `apps/server/ai/gemini_live.py` — `GeminiLiveProvider` 구현체 (`google-genai` WebSocket 클라이언트)
- 청크 → Provider → 응답 파싱 → `utterance.transcribed` fan-out
- 시스템 프롬프트 **정적** (영→한, 2줄 캡, 기술 용어 영문 유지)
- 비용/지연 단순 로깅
- **Gemini API Key는 서버 환경변수에만** (회의실 PC 보유 X)

#### Slice 4 — 회의 라이프사이클 (2.5~3.5일)
- 데스크톱 UI: 로그인 / 회의 시작·종료 / QR + URL 표시
- 라이브 콘솔은 **자막 패널만** (큰 글씨 기본값)
- 회의 종료 → 간소 MD 리포트 (영어 원문 + 한국어 번역 로그)
- 다중 viewer fan-out 안정화
- UI 확장성 1차 정의: `AppShell` / `ConsoleShell` 5슬롯, `tokens.css`, `/console/{history,glossary,admin}` placeholder, `useMeetingStore` / `useUiStore` 스켈레톤 (`docs/UI_DESIGN_SYSTEM.md`)

#### Slice 5 — 운영 가능 (2~3일)
- 사용자 / 부서 모델 + 시드(시스템·번역·PD·TD·Staff)
- `visibility=org` 기본 + 권한 검증 미들웨어
- 오프라인 큐 (Sidecar SQLite, 복구 시 idempotent 재전송)
- 회의실 PC 2대 + viewer 3~5대 동시 안정 검증

### MVP-β (이후 묶음 · 우선순위 분리)

> α 완료 후 시범 운영 → 번역 담당자 피드백을 받고 β 순서 재정렬. 묶음별 ~1주 내외.

#### β-1 — 번역자 UX 묶음 (§1.1 비전 구현)
- 한 키 단축키 시스템 (Space / B / N / S / G / R / ⌘± / ⌘E)
- 용어집(Glossary) v1 — Gemini 프롬프트 동적 주입
- 북마크 (B), 인라인 메모 (N), Slow-down 카드 (S), 10초 되감기 (R)
- 첫 실행 1분 가이드 + Voicemeeter 자동 감지 1순위, BlackHole 자동 감지는 2순위
- 모의 회의 모드 (Gemini 없이 fixture 재생)
- 에러 메시지 사람 언어화
- AI 지연 graceful 처리

#### β-2 — 고시인성 5단계 폰트 시스템
- 자막 5단계 (S 24 / M 32 / **L 44** / XL 56 / XXL 72)
- 회의실 대형 모니터 모드 (XXL 강제 + 사이드 폴딩)
- Pretendard / Noto Sans KR 임베드, WCAG AAA 7:1
- 사용자별 폰트 선호 저장·복원
- ※ **MVP-α는 "큰 글씨 기본값"만** (운영자 44px, 폰 26px) — 동적 토글은 β-2

#### β-3 — 키워드 / 액션 추출 + viewer 토글
- Gemini 응답에 키워드 5카테고리 + 액션 아이템 필드 추가
- `keyword`, `action_item` 테이블 + DomainEvent
- viewer "더 보기" 토글 — 키워드 / 액션 / 로그 / 상태 펼침
- PIN 입력 페이지 + 모바일 UX
- ※ **MVP-α viewer는 자막만 표시**

#### β-4 — 사내 SDK 첫 릴리스
- `yeson-meet-sdk` v0.1 — WebSocket 구독 + DomainEvent dataclass
- `QtMeetingBridge` (PyQt5 signal/slot)
- 사내 PyQt5 툴 1개 PoC 통합

#### β-5 — 인스톨러 + 자동 업데이트
- Tauri MSI / DMG, PyInstaller로 sidecar 단일 실행파일
- macOS 노타리제이션 + Windows 코드사인
- Tauri Updater

#### β-6 — Google / Dropbox 사용자별 opt-in
- Google Calendar Inbound (회의 매칭)
- Gmail App Password SMTP (개인 발송)
- Drive / Dropbox 폴더 동기화 (선택)

#### β-7 — 운영 자동화
- nightly 백업 cron, 90일 보관 만료 cron
- Prometheus / Grafana
- 헬스 체크 실패 알림

### OUT (의도적으로 안 함)
- 외부 클라우드 저장 (모든 데이터는 사내 서버)
- 본인 측 마이크 캡처 (상대방 음성만)
- 화자 자동 구분 (diarization) — Phase 5+ 검토
- 외부 클라이언트 자기 폰으로 회의 직접 참여 (LAN-only)
- 자동 메일 발송 (사용자 검수 후 수동만)
- Slack / Notion / ShotGrid 통합 — 사내 SDK 활용으로 대체
- MCP 익스포트 — Phase 5+ 검토
- 회의 음성 외부 분석 / 학습 데이터 활용

## 8. 비기능 요구사항

| 항목 | 목표 |
|---|---|
| 자막 지연 (캡처 → viewer 화면) | ≤ 2.0초 (P50), ≤ 3.5초 (P95) |
| viewer 동시 접속 | 회의당 30명 |
| 회의 데이터 보관 | 기본 90일 (정책 조정 가능) |
| 회의실 PC ↔ 서버 끊김 허용 | 30분 (로컬 큐 보존) |
| 인증 토큰 수명 | 세션 종료 시 즉시 만료 |
| 자막 글자 잘림 | 폰 가로 모드에서 1줄 ≤ 40자 |
| 사내 서버 가용률 | 단일 노드 기준 99% (백업·재시작 운영 시간 제외) |

## 9. 보안 / 프라이버시 원칙

- **모든 회의 데이터는 사내 서버에만 저장.** 외부 클라우드 절대 X.
- 시크릿(Gemini API key, OAuth token)은 **OS keychain** 또는 서버 환경변수 vault.
- 회의 음성은 개인정보로 취급. 보관 기간 만료 후 자동 삭제.
- HTTPS 강제. 사내 CA 또는 mkcert로 인증서 발급.
- viewer 접근은 **토큰 필요** (MVP-α). β-3에서 6자리 PIN 입력 옵션 추가. 익명 접근 가능하되 토큰은 필수.

## 10. 결정 로그

| 결정 | 값 |
|---|---|
| 데스크톱 스택 | Tauri 2 + React + TypeScript + Tailwind + shadcn/ui |
| 사이드카 / 서버 언어 | Python 3.12 + FastAPI + websockets + sounddevice + google-genai |
| 데이터 SSOT | **사내 Ubuntu Server 24.04 LTS** |
| 데이터베이스 | PostgreSQL 16 |
| 오브젝트 저장 | MVP: 파일시스템 볼륨 / 확장: MinIO 호환 |
| 리버스 프록시 | Caddy (사내 CA 인증서) |
| 인증 | JWT (HS256) + Device API Key + 세션 토큰 (PIN은 β-3 추가) |
| 사내 SSO | 없음 가정. LDAP·SAML 어댑터는 인터페이스만 추상화 |
| Web 접근 모드 | MVP: LAN-only / 확장: Tunnel (Cloudflare·ngrok) 옵션 |
| 외부 통합 | MVP 0개 (인터페이스만) / Phase 4+: 사용자별 Google opt-in |
| 사내 통합 | `yeson-meet-sdk` Python 패키지 (PyQt5 친화) |
| 사이드카 ↔ 데스크톱 통신 방식 | **🔒 Slice 0 락 (2026-05-15)**: `127.0.0.1` localhost WebSocket (port `27800`, JSON 메시지). 이유: dev/prod 동일 인터페이스, Tauri sidecar IPC가 PyInstaller 번들 시 의존성 복잡, debugging 용이 |
| 사이드카 배포 방식 | **🔒 Slice 0 락 (2026-05-15)**: dev = `uv run python -m apps.client_sidecar.main` (별도 프로세스). prod = β-5에서 PyInstaller로 단일 실행파일 + Tauri `externalBin` 등록. MVP-α(S0~S5)는 dev 모드만 사용. PyInstaller spec 작성은 β-5 묶음 |
| 회의실 PC OS 우선순위 | **Windows 1순위**, Mac 2순위. MVP-α 검증은 Windows 회의실 PC 기준으로 먼저 통과 |
| 가상 오디오 | Win: Voicemeeter Banana 권장 (VB-Cable 대체 가능) / Mac: BlackHole 2순위 |
| 외부 GitHub 레퍼런스 활용 | 제품 구조는 yeson-meet 자체 설계 유지. GitHub 솔루션은 오디오 캡처·자막 UX·provider 패턴만 참고하고 통째로 이식하지 않음 |
| Gemini 모델 | `gemini-live-2.5-flash-preview` |
| **Gemini 호출 위치** | **사내 서버** (회의실 PC 아님). API Key는 서버 1곳에만 |
| **STT/번역 추상화** | `STTProvider` / `TranslationProvider` 인터페이스 분리 (ARCH §2.3.1). **구현체는 `GeminiLiveProvider` 1개만 유지**, fallback(WhisperLive 등)은 β 이후 검토. 인터페이스 ≠ 멀티 provider 운영 |
| **UI 확장성 정책** | 5슬롯 컴포지션(Header/Main/Side/Footer/Floating) + 디자인 토큰 CSS 변수 + 3층 컴포넌트(primitive→composite→layout) + 라우팅 placeholder + zustand 단일 store. **새 기능은 슬롯·토큰·composite·store slice 추가만**, 자막 메인 흐름 안 깨짐. 상세 `docs/UI_DESIGN_SYSTEM.md` |
| **MVP 분할** | α(5 슬라이스, 2.5~3.5주 / 약 12~16.5영업일) · β(7 묶음, 우선순위 분리) · OUT(영구 제외) — §7 / ROADMAP 참조 |
| **MVP-α viewer 정책** | 자막 풀스크린만 표시. 키워드 / 액션 / 로그 / 토글은 β-3 |
| **MVP-α 번역자 UX 정책** | 큰 글씨 기본값 외 단축키 / 용어집 / 북마크 / 메모 / Slow-down 등은 β-1 |
| 자막 정책 | 최대 2줄, 한 줄 ≤ 40자, 영어 기술 용어는 영문 유지 |
| 키워드 카테고리 | 일정 / 리테이크 / 승인 / 이슈 / 자산 (5종) |
| **Slice 1 — DomainEvent v0** | **🔒 Slice 1 락 (2026-05-15)**: MVP-α S1은 `utterance.transcribed` **1종만** 발행. `session.started / session.ended / status.changed / keyword.detected / action.detected` 등은 S2~β-3에서 추가. JSON 직렬화는 `apps/server/domain/events.py` Pydantic 모델에서 단일 SSOT (ARCH §4 참조). 모든 viewer/SDK 클라이언트는 type 필드로 dispatch |
| **Slice 1 — DB 테이블 5종** | **🔒 Slice 1 락 (2026-05-15)**: `app_user / device / session / session_token / utterance`. 컬럼은 ARCH §3 초안 그대로 (department/role은 S5, glossary/bookmark/note는 β-1, keyword/action_item은 β-3에서 추가). Alembic 단일 마이그레이션 파일 `0001_initial.py`로 묶음 |
| **Slice 1 — REST 엔드포인트 3개** | **🔒 Slice 1 락 (2026-05-15)**: ① `POST /api/v1/auth/login` (email+password → JWT 24h + refresh 30d). ② `POST /api/v1/devices` (admin JWT, 1회성 API Key 평문 반환, sha256만 DB 저장). ③ `POST /api/v1/sessions` (operator JWT → viewer_url 반환). 그 외 `/api/v1/sessions/{id}/end`, `/report` 등은 S4 |
| **Slice 1 — viewer 백필 시그니처** | **🔒 Slice 1 락 (2026-05-15)**: `GET /api/v1/sessions/{id}/utterances?since=<seq>&limit=50` — 응답은 `{utterances: [{seq, text_en, text_ko, started_at, is_final, ...}]}`. `since` 생략 시 최근 50개, `since=N` 지정 시 `seq > N` 필터. S5 viewer 백그라운드 복귀 따라잡기와 호환 (`(session_id, seq) UNIQUE` 활용) |
| **Slice 2 — Mac BlackHole PoC 우선** | **🔒 Slice 2 락 (2026-05-15)**: Solo dev S2 PoC 검증은 **Intel Mac + BlackHole** 경로로 먼저 통과. ROADMAP §S2 완료 기준 1번(Windows 회의실 PC에서 영어 영상 1분 재생 → 3000 청크 수신)은 시스템 부원 협조 시점에 별도 검증. MVP-α 정식 우선순위 (Windows 1순위, Mac 2순위)는 유지하되, S2 PoC 완료 기준은 Mac 통과로 인정 |
| **Slice 2 — 오디오 청크 바이너리 포맷** | **🔒 Slice 2 락 (2026-05-15)**: 16 kHz / mono / **PCM signed int16 little-endian** / 20 ms 프레임 = **320 samples = 640 bytes per chunk**. 모든 오디오는 WebSocket **binary frame** (`ws.send_bytes`)으로 송신. 입력 디바이스의 native sample-rate/채널은 sidecar에서 16kHz mono로 강제 리샘플 후 송신 |
| **Slice 2 — Sidecar 모드 스위치** | **🔒 Slice 2 락 (2026-05-15)**: 환경변수 `YESON_SIDECAR_MODE=fixture\|audio` (default `audio`부터 S2). `fixture`는 S1 PRD 부록 B 5종 round-robin 유지 (β-1 모의 회의 모드 토대). `audio`는 sounddevice 캡처. **모드 mid-session 전환 금지** (재시작 필요) |
| **Slice 2 — Sidecar↔Server WS 프로토콜 확장** | **🔒 Slice 2 락 (2026-05-15)**: 동일 `/ws/sidecar` connection에서 **binary frame = 오디오 청크**, **text frame = JSON 제어 메시지**. 제어 메시지 type 락 (S2 진입시): `audio.started` (sample_rate, channels, format, started_at) / `chunk_meta` (선택, seq + started_at, S3에서 본격 사용) / `audio.stopped`. S2 서버는 binary 청크 카운트/바이트만 로깅 (Gemini 호출 X, DB save X — S3). 시간 동기는 server 도착 시각 기준 (sidecar 시계 신뢰 X) |

---

## 11. 외부 GitHub 레퍼런스 활용 원칙

> 결론: **기획안 중심 설계 + 검증된 오픈소스 패턴 참고**. 사내 서버 중심 구조, 권한, 저장, viewer fan-out, Gemini Key 서버 보관 원칙은 외부 프로젝트로 대체하지 않는다.

| 레퍼런스 | 활용 위치 | 적용 방침 |
|---|---|---|
| `phuc-nt/my-translator` | Windows-first 데스크톱 UX, 큰 자막, 시스템 오디오 앱 흐름 | Tauri 앱 UX와 운영자용 overlay/글자 크기 패턴 참고. API 직접 호출 구조는 사용하지 않음 |
| `himomohi/AirTranslate` | Mac 2순위 시스템 오디오 캡처, 플로팅 자막 | ScreenCaptureKit 기반 Mac 직접 캡처와 floating caption UX 참고. MVP-α 기본 경로는 Windows + Voicemeeter 유지 |
| `kizuna-ai-lab/sokuji` | 장기 provider 추상화, 가상 마이크, 다중 플랫폼 확장 | β 이후 provider 구조와 고급 오디오 라우팅 참고. MVP-α에는 과도한 기능을 들여오지 않음 |
| `SakiRinn/LiveCaptions-Translator` | Windows 11 OS Live Captions hook 방식 (캡처 코드 자체 불필요) | MVP-α 주 경로는 Voicemeeter 유지. β-1에서 Voicemeeter 미설치 PC fallback / 운영 안정성 케이스로 검토 |
| `collabora/WhisperLive` | 서버측 fallback STT (faster-whisper / TensorRT-Whisper) | **Gemini 장애·약관·비용 위험 plan B**. β-3 이후 `STTProvider` 인터페이스(ARCH §2.3.1)에 끼우는 형태로만 검토 |
| `ufal/whisper_streaming` | LocalAgreement2 streaming 알고리즘 | partial→final 자막 안정화 알고리즘 참고 (ARCH §12.3 🟡 항목 해결). 알고리즘만 채택, 서버 구현은 자체 |
| `CaptionArc` 계열 browser extension | 브라우저 회의 caption capture 보조 모드 | Google Meet/Teams/Zoom DOM 의존 방식이므로 MVP 메인 경로에서 제외. β 이후 브라우저 확장 옵션 검토 |

### 채택하지 않는 것
- 외부 앱의 클라이언트 직접 API Key 저장 / 직접 OpenAI·Soniox·Gemini 호출 구조
- 브라우저 DOM caption scraping을 MVP 실시간 자막의 주 경로로 사용하는 방식
- 개인용 로컬 transcript 저장만 있는 구조

### 채택하는 것
- Windows 회의실 PC에서 안정적인 시스템 오디오 입력을 잡는 운영 UX
- Mac 2순위에서 BlackHole 대체 가능성을 열어두는 ScreenCaptureKit 참고
- 자막 가독성, floating overlay, partial/final 갱신 UX 패턴
- provider 분리 설계 아이디어

---

## 부록 A — Gemini 시스템 프롬프트 (초안)

```
You are a real-time meeting assistant for a Korean animation/VFX studio.

Rules:
- Translate English speech into concise Korean subtitle-style text.
- Preserve technical keywords in English (layout, retake, delivery, render, etc.).
- Maximum 2 short lines per utterance, each ≤ 40 Korean characters.
- Prioritize actionable information: schedules, requests, revisions, approvals, issues.
- Classify detected keywords into one of: schedule, retake, approval, issue, asset.
- Extract action items with optional assignee and due date when explicit.
- Output strict JSON per the event schema. Never invent facts.
```

## 부록 B — 자막 예시

| 영어 원문 | 한국어 출력 |
|---|---|
| Can we finalize the layout revisions before Thursday? | layout 수정본 목요일 전 확정 가능? |
| The delivery might slip by one day because of render issues. | render 문제로 delivery 하루 지연 가능성 |
| Please send the BG fix by Friday. | 금요일까지 BG 수정본 전달 요청 |
