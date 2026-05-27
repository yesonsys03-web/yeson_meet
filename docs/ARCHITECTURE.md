# ARCHITECTURE — yeson-meet

> 최종 갱신: 2026-05-14

---

## 1. 토폴로지

```
                            ┌─────────────────────────────────────────────┐
                            │   사내 서버 (Ubuntu 24.04 LTS)               │
                            │   Docker Compose 1식                         │
                            │                                              │
[회의실 PC: Win 우선/Mac 2순위] │   ┌──────────────────────────┐             │
  Tauri Desktop App          │   │ Caddy (HTTPS 리버스 프록시)│             │
  Python sidecar  ──HTTPS──► │   └────────┬─────────────────┘             │
   - 오디오 캡처              │            │                                │
   - 오디오 청크 ──WSS audio─►│   ┌────────▼──────────────────┐            │
   - 로컬 큐(SQLite)          │   │ FastAPI Gateway            │   ──WSS──►│ Gemini Live API
   ※ Gemini 직접 호출 X       │   │  - REST API                │  ◄────────│ (google-genai)
                              │   │  - WebSocket Hub           │           │   API Key는
                              │   │  - Gemini Live 클라이언트   │           │   서버에만
                              │   │  - Auth (JWT + DeviceKey)  │           │
                              │   │  - Integration Hub         │           │
                              │   └─┬───────────┬─────────────┘            │
                              │     ▼           ▼                          │
                              │ PostgreSQL    File Storage                 │
                              │   (회의 메타)   (오디오 / 리포트)            │
                              └─────────────────────────────────────────────┘
                                         ▲                ▲
                          HTTPS / WSS    │                │   HTTPS / WSS
                                         │                │
                                  Viewers                사내 PyQt5 툴
                                 (PC / 폰)               yeson-meet-sdk
```

## 2. 컴포넌트 책임

### 2.1 회의실 PC: 데스크톱 앱 (`apps/desktop`)
- Tauri 2 Rust shell + React UI
- Operator 콘솔: 회의 시작/종료, 상태, 검수, MD 리포트 다운로드
- 윈도우 / 트레이 / 자동 업데이트 (Tauri Updater)
- Python sidecar 라이프사이클 관리 (`externalBin`)
- **UI 레이아웃은 `ConsoleShell` + 5슬롯 컴포지션 (Header / Main / Side / Footer / Floating)** — 자세히 `docs/UI_DESIGN_SYSTEM.md`. 좌측 nav는 `/console/{meet,history,glossary,admin,settings}` 5칸 미리 박되 MVP-α는 `meet`·`settings`만 활성, 나머지는 placeholder.

### 2.2 회의실 PC: Python 사이드카 (`apps/client_sidecar`)

> **오디오 캡처 경로 단계**:
> - **현재 (MVP-α)**: `sounddevice` + Voicemeeter/BlackHole — 본 §2.2 가 기술하는 path
> - **계획 (Phase 1~2 native)**: `AudioSource` 추상화 + `NativePipeSource` 가 ScreenCaptureKit(Mac) / WASAPI(Win) helper 자식 프로세스에서 PCM 수신. 자세히 `docs/INTEGRATION_DESIGN.md` §3·§4 와 `docs/NATIVE_DESKTOP_HELPER_PLAN.md`. native 안정화 이후 sounddevice 경로는 fallback 으로 격하.

- 오디오 캡처: `sounddevice` → **Voicemeeter(Windows) 1순위**, BlackHole(Mac) 2순위
- **사내 서버에 오디오 청크 WSS push** (Gemini API 직접 호출 안 함 — 키는 서버에만)
- 메타 이벤트(시작/종료/하트비트): HTTPS POST
- **로컬 SQLite 큐**: 네트워크 끊김 시 오디오 청크 임시 보관 → 복구 시 일괄 재전송
- **오프라인 비상 녹음**: 서버 도달 불가가 길어지면 로컬 WAV로 백업 → 복구 시 서버로 업로드 (Phase 2.5+)
- 데스크톱 UI와는 Tauri의 sidecar IPC 또는 `127.0.0.1` WebSocket으로 통신
- Device API Key로 서버 인증, **Gemini API Key는 보유하지 않음**

#### 2.2.1 외부 구현 참고 경계
- Windows MVP-α 경로는 `sounddevice + Voicemeeter`를 우선 구현한다. `phuc-nt/my-translator`는 Windows/desktop 번역 앱 UX와 큰 자막 표시 방식을 참고하되, 클라이언트 직접 API 호출 구조는 따르지 않는다.
- Mac 2순위 경로는 우선 BlackHole로 검증한다. 이후 `himomohi/AirTranslate`의 `ScreenCaptureKit` 시스템 오디오 캡처 방식을 검토해 BlackHole 없는 Mac native capture 옵션을 β 이후 후보로 둔다.
- `kizuna-ai-lab/sokuji`는 provider abstraction, virtual microphone, 다중 플랫폼 오디오 라우팅을 장기 참고자료로만 사용한다.
- `SakiRinn/LiveCaptions-Translator`는 Windows 11 OS Live Captions hook 방식이라 캡처 코드 자체가 불필요한 운영 안정성 케이스로 참고. MVP-α 주 경로는 Voicemeeter 유지하되, β-1에서 Voicemeeter 미설치 PC fallback으로 검토.
- `collabora/WhisperLive`는 서버측 fallback STT 후보로만 둠. Gemini 장애·약관·비용 위험 plan B. β-3 이후 §2.3.1 `STTProvider`에 끼우는 형태로 검토.
- `ufal/whisper_streaming`의 LocalAgreement2 streaming 알고리즘은 partial→final 자막 안정화에 참고 (§12.3 🟡 항목). 알고리즘만 채택, 서버 구현은 자체.
- `CaptionArc`류 브라우저 확장은 회의 플랫폼 DOM 변화에 취약하므로 MVP-α 실시간 자막의 주 경로로 사용하지 않는다.

### 2.3 사내 서버: FastAPI 게이트웨이 (`apps/server`)
- REST API (`/api/v1`)
- WebSocket Hub (`/ws/sidecar`, `/ws/operator`, `/ws/viewer`, `/ws/sdk`)
- **Gemini Live API 클라이언트 (`google-genai`)** — 세션별 양방향 WebSocket 유지
  - 회의실 PC에서 받은 오디오 청크를 Gemini로 전달
  - MVP-α는 Gemini 응답을 파싱해 `utterance.transcribed` 이벤트만 생성
  - β-3에서 `keyword.detected`, `action.detected` 이벤트 생성으로 확장
  - **시스템 프롬프트 + 사용자별 용어집(Glossary)을 서버에서 주입** (PC마다 동기화 불필요)
  - 세션 만료/재연결/백오프 정책을 서버에서 일관 적용
- 인증: 사람 JWT + 회의실 PC Device API Key + 세션 토큰 (PIN은 β-3에서 추가)
- 도메인 이벤트 발행자 + PubSub 버스 (인메모리, 단일 노드 가정)
- 통합 어댑터 호출 (사용자별 Google OAuth 등 / 사내 webhook)
- 정적 web viewer 호스팅 (Caddy로 직접 서빙)
- **`GEMINI_API_KEY`는 서버 환경변수에만 존재. 회의실 PC에 배포 안 함.**

#### 2.3.1 STT / 번역 provider 추상화
- `STTProvider` / `TranslationProvider` 인터페이스를 **Slice 3에서 같이 도입**.
- 구현체는 MVP-α에서 **`GeminiLiveProvider` 1개만 유지**. 멀티 provider 운영 X.
- 목적: β 이후 Gemini 장애·약관·비용 위험 발생 시 plan B(`collabora/WhisperLive` 등 서버측 STT) 교체 비용 최소화.
- 인터페이스 ≠ 멀티 provider 운영. PRD §10 결정 로그와 일치.

```python
# apps/server/ai/providers.py
class STTProvider(Protocol):
    async def stream(
        self,
        audio: AsyncIterator[bytes],   # 16kHz mono PCM 20ms 청크
        lang_hint: str,                # 'en'
    ) -> AsyncIterator[PartialUtterance]: ...

class TranslationProvider(Protocol):
    async def translate(
        self,
        text: str,
        src: str,                      # 'en'
        dst: str,                      # 'ko'
        glossary: dict[str, str] | None = None,
    ) -> str: ...

# 구현체 (Gemini Live는 STT+번역을 한 번에 처리)
class GeminiLiveProvider(STTProvider, TranslationProvider):
    """`google-genai` Live API 1세션으로 STT + 번역 동시 수행."""
```

### 2.4 사내 서버: PostgreSQL
- 회의 메타데이터, 발화, 키워드, 액션, 사용자, 부서, 토큰

### 2.5 사내 서버: File Storage
- 경로: `/var/lib/yeson-meet/storage/<session_id>/{audio.wav, report.md}`
- Docker 볼륨으로 마운트
- 확장 시 MinIO 호환 어댑터로 교체 가능

### 2.6 Web Viewer (`apps/web`) — 자막 우선
- Vite + React + TypeScript + Tailwind + shadcn/ui
- 정적 빌드, Caddy가 서빙
- 라우트: `/v/<token>` (MVP-α) · `/v/<token>/{keyword,action,log}` (β-3) · `/v/?pin=<6자리>` (β-3 추가)
- **UI 레이아웃은 `AppShell` + 5슬롯 컴포지션 (Header / Main / Side / Footer / Floating)** — 자세히 `docs/UI_DESIGN_SYSTEM.md`. MVP-α는 Header 미니(제목+LIVE) + Main(자막) + 나머지 슬롯 비움.
- **MVP-α**: 자막 풀스크린 (다크 모드, 큰 글씨 기본값) + 회의 제목 / LIVE 인디케이터 미니 헤더 + 종료 시 "회의 종료됨" 화면
- **MVP-β-2**: 사용자 글자 크기 토글과 선호 저장 (composite 변경 X, 토큰 prop 교체만)
- **MVP-β-3**: `ToggleBar`로 키워드 / 액션 / 회의 로그 / 상세 상태 패널 확장, 토글 상태 `localStorage` 저장
- WebSocket은 MVP-α에서 `utterance.transcribed`, `session.ended` 중심으로 시작하고, β-3에서 keyword/action/status 이벤트 표시를 확장 (store slice 추가만)

### 2.7 사내 SDK (`packages/sdk-python`)
- 배포명: `yeson-meet-sdk`
- 어디 PyQt5 사내 툴이든 `pip install` 후 import
- WebSocket 클라이언트 + 이벤트 디스패처 + Qt signal/slot 브리지

---

## 3. 도메인 모델 (PostgreSQL DDL 초안)

> **MVP-α / β 분리**: 아래 DDL은 전체 도메인 모델이지만 슬라이스별로 도입 시점이 다름.  
> - **Slice 1**: `app_user`, `device`, `session`, `session_token`, `utterance`  
> - **Slice 5**: `department` + `app_user.department_id`, `app_user.role`  
> - **Slice 4**: `report`  
> - **β-1**: `glossary_term`, `bookmark`, `note` (이 DDL에 아직 없음 — β-1 시 추가)  
> - **β-3**: `keyword`, `action_item`  
> - **β-6**: `integration_binding`

```sql
CREATE TABLE department (
  id           BIGSERIAL PRIMARY KEY,
  name         TEXT UNIQUE NOT NULL,           -- '시스템','번역','PD','TD','Staff'
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE app_user (
  id            BIGSERIAL PRIMARY KEY,
  email         TEXT UNIQUE NOT NULL,
  name          TEXT NOT NULL,
  password_hash TEXT NOT NULL,                  -- bcrypt or argon2
  department_id BIGINT REFERENCES department(id),
  role          TEXT NOT NULL DEFAULT 'operator', -- 'admin'|'operator'|'viewer'
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE device (
  id           BIGSERIAL PRIMARY KEY,
  name         TEXT NOT NULL,                   -- '회의실A PC', '편집실 PC'
  api_key_hash TEXT NOT NULL,                   -- sha256
  is_active    BOOLEAN NOT NULL DEFAULT TRUE,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE session (
  id              BIGSERIAL PRIMARY KEY,
  external_id     UUID UNIQUE NOT NULL,
  owner_user_id   BIGINT NOT NULL REFERENCES app_user(id),
  device_id       BIGINT REFERENCES device(id),
  title           TEXT NOT NULL,
  client_label    TEXT,                          -- 예 'CLIENT-A'
  visibility      TEXT NOT NULL DEFAULT 'org',   -- 'private'|'dept:1,2'|'org'
  status          TEXT NOT NULL DEFAULT 'live',  -- 'live'|'ended'|'aborted'
  started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  ended_at        TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE session_token (
  id            BIGSERIAL PRIMARY KEY,
  session_id    BIGINT NOT NULL REFERENCES session(id) ON DELETE CASCADE,
  token         TEXT UNIQUE NOT NULL,            -- 32바이트 URL-safe base64
  pin           CHAR(6),                          -- MVP-α: NULL · β-3: 6자리 숫자 발급
  kind          TEXT NOT NULL,                   -- 'viewer'|'sdk'
  expires_at    TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE utterance (
  id           BIGSERIAL PRIMARY KEY,
  session_id   BIGINT NOT NULL REFERENCES session(id) ON DELETE CASCADE,
  seq          INTEGER NOT NULL,                 -- 회의실 PC가 발번, idempotency 키
  speaker      TEXT,                             -- MVP: 'CLIENT' 단일
  text_en      TEXT NOT NULL,
  text_ko      TEXT NOT NULL,
  started_at   TIMESTAMPTZ NOT NULL,
  ended_at     TIMESTAMPTZ NOT NULL,
  is_final     BOOLEAN NOT NULL DEFAULT FALSE,   -- partial→final 갱신
  UNIQUE (session_id, seq)
);

CREATE TABLE keyword (
  id           BIGSERIAL PRIMARY KEY,
  session_id   BIGINT NOT NULL REFERENCES session(id) ON DELETE CASCADE,
  utterance_id BIGINT REFERENCES utterance(id),
  text         TEXT NOT NULL,
  category     TEXT NOT NULL,                    -- 'schedule'|'retake'|'approval'|'issue'|'asset'
  detected_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE action_item (
  id           BIGSERIAL PRIMARY KEY,
  session_id   BIGINT NOT NULL REFERENCES session(id) ON DELETE CASCADE,
  utterance_id BIGINT REFERENCES utterance(id),
  text         TEXT NOT NULL,
  assignee     TEXT,
  due_at       DATE,
  status       TEXT NOT NULL DEFAULT 'open',     -- 'open'|'done'|'dismissed'
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE report (
  id           BIGSERIAL PRIMARY KEY,
  session_id   BIGINT UNIQUE NOT NULL REFERENCES session(id) ON DELETE CASCADE,
  storage_path TEXT NOT NULL,                    -- /var/lib/yeson-meet/storage/<id>/report.md
  generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_utterance_session_started ON utterance(session_id, started_at);
CREATE INDEX idx_keyword_session_category ON keyword(session_id, category);
CREATE INDEX idx_session_owner ON session(owner_user_id);
```

---

## 4. 도메인 이벤트 (DomainEvent)

서버에서 발행되는 이벤트. 모든 viewer / SDK 클라이언트는 이걸 구독한다.

```python
# apps/server/domain/events.py
class DomainEvent(BaseModel):
    type: Literal[...]
    session_id: UUID
    occurred_at: datetime

class SessionStarted(DomainEvent):
    type: Literal["session.started"] = "session.started"
    title: str
    client_label: str | None
    owner: UserRef
    visibility: str

class UtteranceTranscribed(DomainEvent):
    type: Literal["utterance.transcribed"] = "utterance.transcribed"
    seq: int
    speaker: str | None
    text_en: str
    text_ko: str
    started_at: datetime
    ended_at: datetime
    is_final: bool

class KeywordDetected(DomainEvent):
    type: Literal["keyword.detected"] = "keyword.detected"
    text: str
    category: Literal["schedule","retake","approval","issue","asset"]
    utterance_seq: int | None

class ActionItemDetected(DomainEvent):
    type: Literal["action.detected"] = "action.detected"
    text: str
    assignee: str | None
    due_at: date | None
    utterance_seq: int | None

class StatusChanged(DomainEvent):
    type: Literal["status.changed"] = "status.changed"
    audio_ok: bool
    ai_ok: bool
    latency_ms: int
    queue_size: int  # 회의실 PC 큐

class SessionEnded(DomainEvent):
    type: Literal["session.ended"] = "session.ended"
    summary: str | None

class ReportGenerated(DomainEvent):
    type: Literal["report.generated"] = "report.generated"
    download_url: str
```

---

## 5. WebSocket 채널 / 이벤트 흐름

### 5.1 채널

| 경로 | 클라이언트 | 인증 | 권한 |
|---|---|---|---|
| `/ws/operator?token=<jwt>` | 데스크톱 앱 | JWT | 양방향 (이벤트 발행 + 구독) |
| `/ws/sidecar?key=<device-api-key>` | 회의실 PC sidecar | Device API Key | 양방향 (오디오 청크 binary + 제어 JSON) |
| `/ws/viewer?token=<session-token>` | viewer 브라우저 | Session Token | **읽기 전용** |
| `/ws/sdk?token=<sdk-token>` | 사내 PyQt5 툴 | Session 또는 사용자 토큰 | 읽기 + 제한된 push |

### 5.2 흐름 (회의 1 발화)

```
[Sidecar] --(WS /ws/sidecar: audio binary 청크)--> [Server]
                                                     │
                              [Server] --(WSS)----► [Gemini Live]
                              [Server] ◄---(WSS)--- [Gemini Live] (자막/키워드/액션 JSON)
                                                     │
                                                     ▼
                                            [DB write: utterance/keyword/action]
                                                     │
                                                     ▼
                                            [Hub fan-out]
                                                     │
                              ┌──────────────────────┼────────────────────────┐
                              ▼                      ▼                        ▼
                       [Operator WS]          [Viewer WS × N]           [SDK WS × N]
```

**핵심**: 오디오 청크는 회의실 PC → 서버로만 흐름. Gemini와의 통신은 서버가 단독으로 책임.

### 5.3 Slice 3 latency budget (capture → viewer)

목표는 **캡처 시점부터 viewer 화면 표시까지 P50 ≤ 2.0초**다. 실측은 영어 1분 영상 E2E에서 발화 단위로 기록하고, 4구간 중 어느 구간이 budget을 초과하는지 먼저 분리한다.

| 구간 | 목표 P50 | 측정 기준 | 초과 시 조치 |
|---|---:|---|---|
| 1. 캡처 → 서버 WSS | ≤ 150ms | sidecar chunk 생성/전송 시각 → server binary frame 수신 시각 | 회의실 PC CPU, Voicemeeter/BlackHole 라우팅, sidecar queue/backoff 확인 |
| 2. 서버 → Gemini | ≤ 250ms | server 수신 시각 → Gemini Live send 완료 | Gemini WS 재연결 상태, chunk batch/flush 지연 확인 |
| 3. Gemini → 파싱 | ≤ 1,300ms | Gemini 응답 도착 → `TranslatedUtterance` 파싱 완료 | partial 응답 우선 표시, prompt/모델/네트워크 상태 확인 |
| 4. 서버 → viewer | ≤ 300ms | `utterance.transcribed` 발생 → viewer WS 수신/렌더 | Hub fan-out, DB write, viewer reconnect/backfill 확인 |

운영 기준: E2E P50이 2.0초를 넘거나 3구간(Gemini→파싱)이 반복적으로 1.3초를 넘으면 **partial subtitle 전략을 즉시 켠다**. 이미 같은 `seq`의 partial→final 교체를 지원하므로, final만 기다리지 말고 partial을 viewer에 먼저 fan-out한다.

2026-05-18 S3 local synthetic 실측: 실제 `GEMINI_API_KEY`가 주입된 서버에서, 서버와 테스트 sidecar가 같은 개발 머신에 있는 상태로 59.37초 synthetic 영어 오디오(8발화)를 `/ws/sidecar`로 전송하고 `/ws/viewer`로 수신했다. 결과는 viewer partial/final 이벤트 16개, DB utterance seq 1~8 저장, phrase-end→first viewer subtitle P50 **1419.8ms** / max **1522.3ms**, server→viewer P50 **5.2ms** / max **82.4ms**로 local synthetic 목표(P50 ≤ 2.0초)를 통과했다. 이 수치는 Gemini 처리 + 서버 fan-out 검증 근거이며, 실제 회의실 PC↔서버 LAN 분리 환경의 캡처→서버 WSS 지연, Wi-Fi/스위치 jitter, TLS/Caddy 경유, 브라우저 렌더 지연은 별도 실측해야 한다.

### 5.4 메시지 포맷

모든 WS 메시지는 JSON 문자열.

```json
{ "type": "utterance.transcribed", "session_id": "uuid", "occurred_at": "...", "seq": 42, "text_en": "...", "text_ko": "...", "is_final": true, ... }
```

---

## 6. 인증 흐름

### 6.1 운영자 로그인 (사람)
```
POST /api/v1/auth/login  { email, password }
→ 200 { access_token (JWT, 24h), refresh_token (30d) }
```
- HS256 + 환경변수 `JWT_SECRET`
- `refresh_token`은 PostgreSQL 저장 + 회전

### 6.2 회의실 PC 등록 (Device)
```
- 관리자 UI에서 디바이스 생성 → 1회성 API Key 평문 발급
- 회의실 PC에 환경변수 또는 keychain 저장
- 서버는 sha256 해시만 저장
```

### 6.3 세션 토큰 (Viewer)
```
POST /api/v1/sessions  (Operator JWT)
# MVP-α 응답
→ { session_id, viewer_url: "https://<SERVER_IP>/v/<token>" }

# β-3 응답 (PIN 추가)
→ { session_id, viewer_url: "https://<SERVER_IP>/v/<token>", pin: "473829" }
```
- viewer 토큰은 세션 종료 시 즉시 만료
- **MVP-α**: QR / URL 진입만 지원
- **β-3 추가**: 6자리 PIN, viewer 페이지에서 토큰 대신 입력 가능

### 6.4 LDAP / SSO 어댑터 (인터페이스만)
```python
class AuthProvider(Protocol):
    async def authenticate(self, identifier: str, secret: str) -> User | None: ...
```
MVP는 `LocalAuthProvider`(DB). 향후 `LDAPProvider` 추가.

---

## 7. 접근 모드 추상화 (LAN / Tunnel)

```python
class URLProvider(Protocol):
    def viewer_url(self, token: str) -> str: ...

class LANProvider:                  # MVP 기본
    def __init__(self, base_url: str): self.base = base_url   # https://<SERVER_IP>
    def viewer_url(self, token): return f"{self.base}/v/{token}"

class TunnelProvider:                # Phase 5+
    def __init__(self, public_base: str): ...
```

`ACCESS_MODE` 환경변수: `lan` | `tunnel` | `localhost`.

CORS / Origin 화이트리스트도 같은 설정으로 분기.

---

## 8. 통합 SDK 구조

### 8.1 Integration Protocol (서버측 플러그인)
```python
class Integration(Protocol):
    name: str
    config_schema: type[BaseModel]
    
    async def on_event(self, event: DomainEvent, config: BaseModel) -> None: ...
    async def fetch_context(self, hint: str, config: BaseModel) -> dict | None: ...
    async def health_check(self) -> bool: ...
```

### 8.2 사용자별 통합 바인딩
```sql
CREATE TABLE integration_binding (
  id            BIGSERIAL PRIMARY KEY,
  user_id       BIGINT NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
  provider      TEXT NOT NULL,              -- 'google'|'dropbox'|'inhouse-foo'
  config_json   JSONB NOT NULL,
  secret_ref    TEXT,                       -- vault 또는 keychain 경로
  enabled       BOOLEAN NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 8.3 사내 SDK (`yeson-meet-sdk`)
```python
# packages/sdk-python/yeson_meet_sdk/__init__.py
from yeson_meet_sdk import MeetingClient, events

client = MeetingClient(
    base_url="https://<SERVER_IP>",
    token=os.environ["YESON_MEET_SDK_TOKEN"],
)

# 콜백 등록
@client.on(events.UtteranceTranscribed)
async def _(evt): print(evt.text_ko)

@client.on(events.ActionItemDetected)
async def _(evt): my_local_db.insert(evt)

# PyQt5 브리지
from yeson_meet_sdk.qt_bridge import QtMeetingBridge
bridge = QtMeetingBridge(client)
bridge.utterance.connect(my_qt_slot)
```

배포: 사내 PyPI 또는 `pip install git+ssh://...`. semver 엄수.

---

## 9. 오프라인 큐 (회의실 PC)

```
[Capture] → [Local Queue (SQLite WAL)] → [Sender Worker] → [Server]
                       │                          │
                       │  ← retry on failure ─────┘
                       │
                       └─ persist if network down
```

- 큐 테이블: `pending_event(id, seq, payload_json, attempts, last_error, created_at)`
- 정책: 지수 백오프 (1s, 2s, 4s, 8s … 최대 60s)
- 최대 보존 시간: 30분 (그 이상은 alert + drop)
- 서버 측 idempotency: `(session_id, seq)` UNIQUE 제약으로 중복 방지

---

## 10. 모노레포 구조

```
yeson-meet/
├── apps/
│   ├── desktop/              # Tauri 2 + React (operator UI)
│   │   ├── src-tauri/        # Rust shell
│   │   └── src/              # React (packages/ui 재사용)
│   ├── client_sidecar/       # Python: 오디오 캡처 + 서버 전송 + 큐
│   │   ├── audio/            # sounddevice 캡처
│   │   ├── transport/        # WSS audio + HTTPS meta
│   │   ├── queue/            # 오프라인 SQLite 큐
│   │   ├── recorder/         # 비상 WAV 녹음 (Phase 2.5+)
│   │   ├── config/
│   │   └── main.py
│   ├── server/               # FastAPI 사내 서버
│   │   ├── auth/
│   │   ├── api/v1/
│   │   ├── ws/               # /ws/sidecar /ws/operator /ws/viewer /ws/sdk
│   │   ├── ai/               # ★ Gemini Live 클라이언트 (서버에만)
│   │   ├── domain/           # DomainEvent, Bus
│   │   ├── db/               # SQLAlchemy + Alembic
│   │   ├── storage/
│   │   ├── integrations/
│   │   └── main.py
│   └── web/                  # Vite + React (viewer)
├── packages/
│   ├── ui/                   # 공통 React 컴포넌트 (자막/키워드/액션 등)
│   └── sdk-python/           # yeson-meet-sdk
│       └── yeson_meet_sdk/
├── deploy/
│   ├── docker-compose.yml
│   ├── Caddyfile
│   ├── env.example
│   └── postgres/init.sql
├── docs/
│   ├── PRD.md
│   ├── ARCHITECTURE.md
│   ├── ROADMAP.md
│   └── DEPLOY.md
├── pyproject.toml            # uv workspace (server, sidecar, sdk)
├── pnpm-workspace.yaml       # ui, web, desktop
└── README.md
```

---

## 11. 기술 결정 요약표

| 영역 | 선택 | 이유 |
|---|---|---|
| 데스크톱 shell | Tauri 2 | 가볍고 단일 인스톨러, sidecar 공식 지원 |
| 프론트엔드 | React + TS + Tailwind + shadcn/ui | HTML 목업 그대로 이식 가능, 생태계 압도적 |
| 백엔드 | FastAPI + uvicorn | 비동기 WebSocket, Pydantic 모델, AI 라이브러리 친화 |
| AI | Gemini Live 2.5 Flash | 한국어 처리 우수, 실시간 적합, 비용 합리적 |
| Gemini 호출 위치 | **사내 서버** (회의실 PC 아님) | API Key 1곳 관리, 키 노출 위험 ↓, 사용량 중앙 추적, 시스템 프롬프트/용어집 일관 주입 |
| DB | PostgreSQL 16 | 표준, JSON/Full-text 지원, 운영 도구 풍부 |
| 큐 (회의실 PC) | SQLite WAL | 임시 보관용으로 충분, 별도 인프라 불필요 |
| 통신 | HTTPS + WSS | 사내망이라도 암호화 |
| 인증 | JWT + Device Key + Session Token | 다층 보안, SSO 없이도 운영 가능 |
| 배포 | Docker Compose | 단일 노드 운영, 이전 용이 |
| 리버스 프록시 | Caddy | 자동 인증서, 설정 단순 |
| 사내 SDK | Python (`yeson-meet-sdk`) | 사내 툴이 PyQt5 기반 |

---

## 12. 엣지케이스 / 운영 함정

> 구현 시 미리 알아두면 운영 사고를 줄이는 항목. 영역·슬라이스별로 정리.

### 12.1 Slice 0~1 — 인프라
| 케이스 | 영향 | 처리 |
|---|---|---|
| Root CA 미등록 폰 첫 접속 | viewer 인증서 경고 | 셋업 가이드 명시 + 첫 진입 시 `.crt` 다운로드 페이지 |
| 회의실 PC NTP 미동기 | 토큰 검증 실패 | 사이드카 부팅 시 시계 동기 검증 + 경고 |
| Docker 컨테이너 재시작 | 진행 중 회의 끊김 | `restart: unless-stopped` + 사이드카 백오프 |
| Caddy 자체 CA 재발급 | 클라이언트 캐시 mismatch | `./data/caddy` 볼륨 영구 보존, 절대 삭제 X |

### 12.2 Slice 2 — 오디오 캡처
| 케이스 | 영향 | 처리 |
|---|---|---|
| Voicemeeter/BlackHole 비활성 | 무음 캡처 | 시작 시 입력 enum 검증, 침묵 5초 감지 시 경고 |
| 회의 중 헤드셋 연결/해제 | 시스템 출력 자동 전환 → 우회 가능 | 멀티 출력 강제, 변경 감지 시 alert |
| 샘플레이트/채널 mismatch | Gemini 거부 / 품질 저하 | 16kHz mono 강제 리샘플 |
| 무음 60초+ | 빈 청크 무한 push, 비용 낭비 | VAD 또는 RMS 임계값으로 무음 차단 |
| 클리핑 (오버 볼륨) | 인식 저하 | 사이드카 RMS 모니터, 경고 |
| 사이드카 프로세스 죽음 | 회의 정지 | Tauri sidecar lifecycle (재시작) |

### 12.3 Slice 3 — Gemini Live (가장 많은 엣지)
| 케이스 | 영향 | 처리 |
|---|---|---|
| API Key 무효/만료 | 모든 회의 정지 | 서버 시작 시 health check + `/api/v1/operator/alerts` critical 알림 |
| Quota 초과 | 회의 중 끊김 | 응답 코드 모니터, viewer "AI 일시 정지" + alert |
| Gemini 응답 ≥10초 지연 | 자막 멈춤 | 타임아웃 + 이전 자막 유지 |
| usage metadata 누락 | 비용 추정 불가 | token/cost 로그는 best-effort, 응답에 usage metadata가 없으면 E2E 비용 검증에서 보완 |
| Gemini WS 세션 만료 (장기 회의) | 끊김 | 자동 재연결 + 세션 회전, 끊김 동안 큐 보존 |
| partial → final 갱신 | 자막 깜빡임 | `is_final` 플래그로 마지막 partial 교체 (`seq` 키) |
| 응답 JSON 깨짐 | 자막 누락 | 강건한 파서 + 로그, 다음 응답 정상 처리 |
| 빠른 발화 / 겹침 | 순서 꼬임 | `seq` 단조 증가 + `started_at` 보조 |
| 비영어 발화 (한국어 섞임) | 프롬프트 가정 어긋남 | 시스템 프롬프트에 "혼합 언어 한국어 그대로" 명시 |
| 동시 회의 → Gemini 한도 | 회의 시작 실패 | 활성 세션 카운트, 한도 초과 거부 |
| 비용 폭주 (좀비 세션) | 청구 충격 | `YESON_MEETING_MAX_DURATION_HOURS` 초과 시 sidecar ingress 차단 + 자동 종료 + operator alert |

### 12.4 Slice 4 — 회의 라이프사이클
| 케이스 | 영향 | 처리 |
|---|---|---|
| 운영자가 종료 안 하고 앱 종료 | 좀비 세션 | sidecar disconnect 감지 N분 후 자동 종료 |
| 데스크톱 앱 충돌 | 데이터 손실 위험 | 서버가 마스터, 앱 재실행 시 활성 세션 복원 |
| 같은 Device 두 회의 동시 시도 | 충돌 | Device당 1회의 제한, UI에 "라이브 중" 표시 |
| 종료 시 큐 잔여 | 마지막 발화 손실 | flush 대기, timeout 시 강제 종료 + 로그 |
| MD 리포트 생성 실패 | 다운로드 불가 | 재시도 가능한 백그라운드 job |
| 1시간+ 회의 큰 리포트 | 메모리/크기 | streaming 생성 |
| QR 회의실 모니터 거리 (2~4m) | 폰 카메라 스캔 실패 | QR 전체 화면 모드 |

### 12.5 Slice 5 — 다중 viewer / 권한 / 오프라인
| 케이스 | 영향 | 처리 |
|---|---|---|
| viewer 백그라운드 (탭 비활성) | WS suspend | onfocus 재연결 + `?since=<seq>` 따라잡기 |
| 새 viewer 진입 시 과거 자막 | 메모리 폭주 | 최근 N=50건 + "전체 보기" 옵션 |
| 토큰이 회의 종료 전 만료 | 갑자기 끊김 | 토큰 수명 = 세션 종료까지 (절대 시간 아님) |
| 토큰 leak (외부 공유) | 사외 노출 | LAN 격리 1차 방어 + β-3 PIN + audit log |
| 30명 동시 viewer | WS Hub 부하 | FastAPI WS pool 검증, 부하 테스트 (Slice 5 완료 기준) |
| 끊김 30분 초과 | 큐 디스크 가득 | 30분+ 큐는 비상 WAV dump + alert |
| 재전송 중복 | DB 중복 | `(session_id, seq) UNIQUE` + ON CONFLICT DO NOTHING |
| 시계 어긋남 | 정렬 꼬임 | `seq` 우선 정렬, `started_at`은 표시용 |
| 권한 변경 (부서 이동) 회의 중 | viewer 차단? | 진행 회의는 시작 시 권한 스냅샷 |
| 같은 token 다중 viewer | 카운트 모호 | connection_id 기반 카운트, IP/UA 로깅 |

### 12.6 보안 / 운영 (슬라이스 무관)
| 케이스 | 영향 | 처리 |
|---|---|---|
| 로그 시크릿 노출 | 키 leak | Pydantic `SecretStr` + 로깅 필터 |
| 약한 비밀번호 | 무차별 대입 | 최소 12자 + fail2ban / 앱 rate limit |
| 백업 중 DB 락 | 회의 끊김 | `pg_dump` (락 최소) + 야간 시간 |
| 디스크 가득 (오디오 누적) | 회의 시작 실패 | 사용량 모니터 80% 알림 |
| VLAN 격리 / AP isolation | 폰 접근 불가 | 셋업 가이드에 네트워크 정책 확인 단계 |
| 고정 IP 미설정 | 모든 QR/Device 무효 | 셋업 24h 후 IP 재확인 검증 |
| Gemini 약관 변경 | 데이터 정책 위배 | Phase 별 약관 재검토 (`PRD §9`) |

### 12.7 데이터 무결성
| 케이스 | 영향 | 처리 |
|---|---|---|
| 사이드카가 session 시작 전 청크 push | 고아 데이터 | `session.status=live` 검증 후 수락 |
| 종료된 session에 청크 push | 잔여 큐 늦게 도달 | `ended_at` 이후 청크 drop + 로그 |
| utterance.seq 점프 (gap) | 표시 정상, 백엔드 gap | gap만 로그, 표시 영향 없음 |
| MD 리포트 생성 중 새 발화 | 일관성 | 종료 시점 snapshot 기준 |
| 보관 만료 (파일 삭제) vs DB | 파일 없음 / DB 있음 | 파일만 삭제, DB는 텍스트 메타만 N년 보존 옵션 |

### 12.8 우선순위 — MVP-α 안에 반드시

🔴 **반드시 처리** (없으면 운영 불가)
1. 사이드카 ↔ 서버 재연결 백오프 + 오프라인 큐
2. Gemini WS 세션 재연결
3. `(session_id, seq) UNIQUE` idempotency
4. 회의 종료 시 큐 flush + 좀비 세션 자동 종료
5. viewer 백그라운드 → 복귀 시 따라잡기
6. 회의 비용/시간 타이머 + N시간 자동 종료 안전장치

🟡 **권장** (없으면 불안)
1. Caddy CA 볼륨 보존 정책
2. 디스크 사용량 80% 알림
3. NTP 동기 검증
4. partial → final 자막 안정화
5. VAD로 무음 청크 차단 (비용 절감)

🟢 **β 또는 운영 중 대응**
1. 사용자 행동 UX (β-1)
2. 비영어 혼합 발화 (프롬프트 정제)
3. 30명+ 부하 (Slice 5 검증)
