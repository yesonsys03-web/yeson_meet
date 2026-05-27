# yeson-meet 통합 설계 (Integration Design)

> 작성일: 2026-05-27
> 상위 방향 문서: `docs/NATIVE_DESKTOP_HELPER_PLAN.md` (제품/기술 방향)
> 이 문서: 그 방향 위에서의 구체적 통합 아키텍처 설계 (Phase 0~1 진입 직전 결정 사항)
> 범위: 클라이언트(Tauri 대시보드 + Native audio helper + Python sidecar) ↔ Server ↔ 외부 PyQt5 앱들의 관계

---

## 0. 한 줄 요약

yeson-meet은 **Tauri/React 대시보드(번역 자막·노트 워크플로 전용)** + **Audio sidecar(Python, native helper로 캡처 레이어 교체)** + **외부 PyQt5 앱들을 launcher 패턴으로 통합** + **서버가 회의 데이터의 single source of truth** 라는 구조로 간다. Voicemeeter/BlackHole은 즉시 폐기하지 않고 compatibility fallback으로 남긴다.

---

## 1. 전체 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│  yeson-meet Dashboard (Tauri shell + React UI)              │
│  주된 UI 도메인: 번역 자막 + 회의 노트 정리                    │
│  ┌──────────────┬──────────────┬──────────────┐            │
│  │ Live Subtitle│ Note Viewer  │ Meeting List │ Settings…  │
│  └──────────────┴──────────────┴──────────────┘            │
│  ┌──────────────────────────────────────────────┐          │
│  │  Launcher Panel — apps.json 기반              │          │
│  │  [Note App] [Tool B] [Tool C] …               │          │
│  └──────────────────────────────────────────────┘          │
└─────────────────────────────────────────────────────────────┘
        │ spawn / control            │ WS
        ▼                            ▼
┌─────────────────┐         ┌──────────────────────────────┐
│ 외부 PyQt5 앱들  │         │ Audio Sidecar (Python)        │
│ (독립 윈도우)    │         │  ← Native Helper의 PCM 받음    │
│ - 공유 인증     │         │  - WS / JWT / retry / encoding │
│ - 공유 디자인   │         │    그대로                       │
│ - 워크플로 연계 │         └──────────────────────────────┘
└─────────────────┘                       │ WS
                                          ▼
                       ┌──────────────────────────────────┐
                       │ Server (FastAPI)                  │
                       │  · Subtitle (Gemini Live)         │
                       │  · Note generation (LLM)          │
                       │  · Export (Word/PDF/Excel) [후속] │
                       │  · Knowledge base [후속]          │
                       │  · 회의 데이터 single source       │
                       └──────────────────────────────────┘
```

핵심 원칙:
- 대시보드 = **번역 자막·노트 워크플로 전용 UI**. 모든 기능의 허브가 되려고 시도하지 않는다.
- 외부 PyQt5 앱 = **launcher로 spawn된 독립 윈도우**. 대시보드 안 임베드는 기술적으로 포기.
- Audio sidecar = **단일 사이드카**. 일반화하지 않는다. 다른 미래 서비스는 server-side에 둔다.
- 서버 = **회의 데이터 단일 진리원**. 클라이언트 앱들끼리 직접 통신하지 않는다.

---

## 2. Launcher 패턴 (외부 PyQt5 앱 통합)

별도 윈도우라는 제약 위에서 **시각·인증·워크플로·윈도우 관리** 네 축으로 융합감을 만든다.

### 2.1 등록 — `apps.json`

위치:
- macOS: `~/Library/Application Support/yeson-meet/apps.json`
- Windows (후속): `%APPDATA%\yeson-meet\apps.json`

스키마 (예시):

```jsonc
{
  "version": 1,
  "apps": [
    {
      "id": "note-editor",
      "name": "회의 노트 편집기",
      "description": "회의 자막 기반 노트 작성·편집",
      "icon_path": "/Applications/yeson-note.app/Contents/Resources/icon.png",
      "executable": "/Applications/yeson-note.app/Contents/MacOS/yeson-note",
      "args_template": ["--note-id", "${context.note_id}"],
      "env": {
        "YESON_API_BASE":        "${shared.server_url}",
        "YESON_AUTH_TOKEN_FILE": "${shared.auth_token_path}"
      },
      "singleton": true,
      "min_dashboard_version": "0.1.0"
    }
  ]
}
```

치환 토큰:
- `${shared.*}` — 대시보드 런타임이 주입 (server URL, 토큰 경로, 로케일 등). 누락 시 launcher가 거부하고 명시적 에러.
- `${context.*}` — launch 시점 호출자가 전달 (note_id, meeting_id 등). 누락 시 해당 인자/env entry를 **결과에서 제외**하고 launch 계속 진행. (예: note_id 없이 노트 앱 실행 시 `--note-id` 자체가 빠짐 → 앱은 신규 노트 모드로 동작)

### 2.2 실행 메커니즘

Tauri Rust 측 (의사 코드):

```rust
fn launch_app(app_id: &str, context: Context) -> Result<()> {
    let cfg = registry.find(app_id)?;
    if cfg.singleton && registry.is_running(app_id) {
        bring_to_front(app_id);
        return Ok(());
    }
    let child = Command::new(&cfg.executable)
        .args(interpolate_args(&cfg.args_template, &context))
        .envs(interpolate_env(&cfg.env, &shared_state))
        .spawn()?;
    registry.track(app_id, child.id(), Instant::now());
    Ok(());
}
```

플랫폼별 bring-to-front:
- macOS: `osascript -e 'tell application "<name>" to activate'` 또는 `NSRunningApplication`
- Windows (후속): `SetForegroundWindow` via Win32 API

### 2.3 융합 메커니즘

**(a) 비주얼 일관성**
- 공유 design tokens JSON (`~/Library/Application Support/yeson-meet/design-tokens.json`) — color/font/spacing 정의
- 각 PyQt5 앱 빌드 시 design tokens → Qt stylesheet 변환
- 윈도우 타이틀 접두: `"yeson · "` 통일
- 같은 아이콘 패밀리 (대시보드와 동일 디자이너 자산)

**(b) 인증/세션 공유**
- 대시보드 로그인 → `~/.yeson-meet/auth.json` 저장 (JWT, 만료 시각, server URL)
- 권한: macOS `chmod 600`, Windows ACL 사용자 read-only
- PyQt5 앱 시작 시 `YESON_AUTH_TOKEN_FILE` 환경변수 경로에서 토큰 읽음
- 만료 임박 시 대시보드가 refresh, 파일을 atomic write로 갱신
- 로그아웃 시 대시보드가 파일 삭제 + 실행 중 앱들에 종료 신호(IPC 또는 SIGTERM)

**(c) 워크플로 연계**
- 정방향(대시보드 → 앱): `args_template`로 context 전달. 예: 노트 편집 클릭 → `--note-id <id>`
- 역방향(앱 → 대시보드): server API 호출 → server가 WS broadcast → 대시보드 구독자(예: NoteViewer)가 즉시 갱신
- 직접 IPC는 v1 범위 밖. 모든 데이터 흐름은 server 경유.

**(d) 윈도우 관리 통합**
- Registry: spawned PID·앱 ID·시작 시각 추적
- Dashboard 사이드바에 running 상태 점(dot) 표시
- 클릭 시 bring-to-front
- 대시보드 종료 시 자식 앱 처리 기본 정책: **자식은 살려둠** (사용자가 명시 종료). 옵션 토글 제공.

---

## 3. Audio Native Helper 통합

### 3.1 파이프라인

```
[OS 시스템 오디오]
        ↓
[Native Helper 프로세스]
   · Phase 1 (macOS 14.2+):  Swift,  ScreenCaptureKit
   · Phase 2 (Windows):       Rust,   WASAPI loopback
   · 출력 contract: 16 kHz mono PCM s16le, 20 ms chunks (640 bytes)
   · ⚠️ TODO(Phase 1 진입 시): macOS 버전 게이트는 **런타임** 으로 처리한다 — Tauri `tauri.conf.json bundle.macOS.minimumSystemVersion` 은 **`"11.0"` 그대로** (그래야 11~14.1 Mac 도 앱 설치 후 BlackHole fallback 안내가 가능). Swift helper Package.swift `platforms: [.macOS(.v14)]` 로 helper 만 14+ 한정 + sidecar 의 `make_source()` 에서 OS 버전·helper 존재 둘 다 체크해 미만이면 sounddevice fallback. (`docs/NATIVE_DESKTOP_HELPER_PLAN.md` §4.2 의 "14.2 미만은 BlackHole compatibility mode 안내" 정책과 정합.)
        ↓ binary pipe (stdout)
[Python sidecar — 기존 코드, 입력 source만 추상화]
   · WS / JWT / retry / encoding 변경 없음
        ↓ WS
[Server FastAPI → Gemini Live → 자막 emit]
```

### 3.2 변경 범위

추가:
- `apps/client_sidecar/audio/native_pipe.py` — native helper 프로세스 spawn + stdout PCM stream 읽음 + stderr JSON 이벤트 수신
- Native helper 바이너리 두 종 (Mac/Win), 각각 별도 빌드 산출물

라이프사이클 책임 (결정):
- Tauri Rust → Python sidecar 관리 (`externalBin`, 기존 `ARCHITECTURE.md` §2.1 그대로)
- Python sidecar → Native helper 관리 (spawn / stdout readexactly / 종료 시 SIGTERM + wait, kill fallback)
- 즉 native helper는 항상 sidecar의 자식 프로세스다. Tauri가 helper를 직접 spawn하지 않는다 — IPC pipe가 sidecar 프로세스 안에 닫혀 있게 해 권한·재시작·실패 복구를 sidecar 1곳에서 처리하기 위함.

수정:
- `apps/client_sidecar/audio/device.py` — `AudioSource` 추상화, `SoundDeviceSource` / `NativePipeSource` 분기
- 환경변수: `YESON_AUDIO_PROVIDER=native|sounddevice|auto` (기본 `auto`: native 시도 → 실패 시 sounddevice)

Fallback 정책:
- Native 권한 거부, 미지원 OS 버전, 프로세스 spawn 실패 시 → BlackHole/Voicemeeter compatibility mode 자동 전환
- 대시보드 UI에 fallback 발생을 명시 (사용자에게 "왜" 보여줌)

---

## 4. `AudioCapture` 인터페이스 윤곽

Native helper 측 contract. Swift/Rust 양 구현이 따른다. Phase 1에서 정의, Phase 2가 두 번째 구현체로 끼워 들어감.

```text
trait AudioCapture {
    // Lifecycle
    fn start() -> Result<(), CaptureError>
    fn stop()
    fn dispose()

    // Permission (OS에 따라 NotApplicable일 수 있음)
    enum PermissionStatus { Granted, Denied, NotDetermined, Restricted, NotApplicable }
    fn permission_status() -> PermissionStatus
    async fn request_permission() -> PermissionStatus

    // Configuration
    enum CaptureTarget { SystemDefault, Device(String), App(String) /* bundle_id */ }
    fn set_target(target: CaptureTarget) -> Result<(), CaptureError>
    fn list_targets() -> Vec<CaptureTarget>

    // Constants — 모든 구현이 보장
    const SAMPLE_RATE: u32 = 16_000
    const CHANNELS:    u8  = 1
    const FORMAT:      &str = "pcm_s16le"
    const FRAME_MS:    u32 = 20    // → 640 bytes per frame
}
```

IPC contract (helper → sidecar):
- **데이터 채널**: stdout binary pipe. PCM frames 연속.
- **제어 채널**: stderr JSON lines. 예: `{"event":"permission_denied"}`, `{"event":"device_changed","id":"BuiltInMicrophoneDevice"}`, `{"event":"error","code":"E_PERMISSION","msg":"..."}`
- **명령 채널** (v2 옵션): stdin JSON lines. 예: `{"cmd":"set_target","id":"..."}` — 필요해지면 추가.

설계 원칙:
- **변환 책임 (결정)**: 샘플레이트 변환·mono downmix는 **helper 안에서** 처리하고 stdout으로 16 kHz mono s16le 640 B chunk를 그대로 출력한다. 이유는 (a) OS native API(ScreenCaptureKit/WASAPI)가 native sample-rate/채널을 알 수 있는 가장 가까운 지점이고, (b) sidecar 공통 코드가 OS별 분기 없이 stdout 바이트를 그대로 WS binary frame으로 전달할 수 있기 때문이다.
- 인터페이스는 캡처 레이어에만 한정. sidecar 전송·queue·재연결·인증은 helper 밖 공통 코드(`apps/client_sidecar/transport/*`)에서 그대로 → Phase 2에서 재사용.
- Phase 2 Windows 구현체도 같은 출력 contract(16 kHz mono s16le 640 B/20 ms)를 지켜야 sidecar 공통 코드가 분기 없이 재사용된다.
- Apple Silicon 우선, Intel Mac은 비-우선(필요 시 후속).

---

## 5. Phase 0 — Baseline 측정 정의

Phase 1 Native 캡처 도입 전에, **현재 BlackHole/Voicemeeter 환경의 품질 숫자를 잡는다.** 이 숫자는 한 번 Native로 옮기면 다시 못 측정한다.

### 5.1 시나리오

| # | 이름 | 길이 | 화자/언어 |
|---|------|------|-----------|
| 1 | Zoom 1:1 EN→KO | 5분 | 1인, 영어 |
| 2 | Teams 3+ mixed | 10분 | 3인 이상, 한/영 혼재 |
| 3 | YouTube TED EN | 10분 | 1인 (재생), 표준 영어 |
| 4 | Silent room | 5분 | 무음 (오발신 체크) |

### 5.2 지표

| key | 정의 |
|-----|------|
| `subtitle_first_token_ms` | 첫 발화 시작 → 첫 자막 토큰 표시 |
| `subtitle_full_p50_ms` | 발화 종료 → final 자막. P50 |
| `subtitle_full_p95_ms` | 동일. P95 |
| `chunks_per_sec_sustained` | 평균 sidecar → server 청크 전송율 |
| `audio_queue_drop_count` | "lossy drop" 누적 카운트 |
| `gemini_segments_per_minute` | Live API segment cycle 횟수 (TPM 추정용) |

### 5.3 수집

- 서버 측: 기존 로그에서 대부분 추출 가능 (`Gemini Live first subtitle yielded`, `AI utterance published`, `Audio queue lossy drop` 등). 집계 스크립트 추가.
- 클라이언트 측: 자막 도착 시간 — React `performance.now()` 기반 timing 코드 추가 (Phase 0 산출물).
- 저장 형식: `docs/baselines/2026-MM-DD-<scenario>.json` — 시나리오 1회당 1파일. **스키마는 `docs/baselines/schema.md` 에 frozen** — Phase 0 측정 시작 전에 alignment 확인 필수. Phase 1 native 재측정도 동일 스키마 따른다.

Phase 1 검증 시 동일 시나리오를 Native 캡처에서 재측정 → 회귀·개선 정량 비교.

---

## 6. 회의 후 자동 노트 생성 + React 뷰어

### 6.1 데이터 흐름

```
[회의 종료 트리거]
        ↓
[Server: Note Generation Service]
   1. Session의 전체 transcript 모음 (이미 DB에 보존)
   2. LLM 호출 (요약 + 구조화)
   3. MD 파일 생성: frontmatter + 구조화 본문
   4. STORAGE_ROOT/notes/<meeting_id>.md 저장
   5. DB의 Note 메타데이터 갱신
        ↓
[Dashboard React Panel: NoteViewer]
   - 노트 목록 (서버 API)
   - MD fetch → react-markdown HTML 렌더
   - read-only v1 → 편집 v2
```

### 6.2 트리거 모델

Primary 자동:
- 사용자가 대시보드에서 "회의 종료" 클릭, **또는**
- Sidecar가 정상 stop signal 받음 (사용자가 명시 stop)
→ server가 즉시 generation kick-off

Fallback 수동:
- 자동 트리거가 안 됐다면 (앱을 그냥 닫음, 네트워크 단절 등) **회의 목록 패널의 해당 회의 행에서 "[노트 생성]" 클릭**
→ 같은 server endpoint로 generation

서버 측 상태:
- Meeting 레코드에 `note_status: pending | generating | ready | failed`
- `pending`이면 [노트 생성] 버튼 활성
- `generating`이면 진행 표시, 중복 클릭 무시
- `ready`면 "다시 생성?" 확인 모달
- `failed`면 재시도 버튼

### 6.3 MD 파일 구조

```markdown
---
meeting_id: 9f3e21...
title: Q2 OKR 검토 회의
date: 2026-05-27
start_time: "14:00"
duration_minutes: 47
languages: [en, ko]
participants: [host, guest1, guest2]
source: yeson-meet auto-generated
generator_version: 1
---

# Q2 OKR 검토 회의

## 핵심 요약
...

## 주요 논의
- ...

## 결정 사항
- [x] ...

## Action Items
- [ ] **(담당: A)** 다음 주까지 ...

## 미해결 질문
- ...
```

### 6.4 서버 추가 영역

- `apps/server/services/note_generator.py` — LLM 프롬프트 + transcript→MD 변환
- `apps/server/api/notes.py`:
  - `GET    /api/meetings`               — 회의 목록 (note_status 포함)
  - `POST   /api/meetings/{id}/notes`    — 노트 생성 트리거. 기본은 `ready`/`generating` 상태일 때 작업 생략하고 기존 ID 반환. `?force=true` 쿼리 시 기존 노트를 archive(또는 versioned overwrite)하고 재생성. UI는 `ready`일 때 확인 모달 후 force=true로 호출.
  - `GET    /api/notes/{id}`             — 메타데이터
  - `GET    /api/notes/{id}/raw`         — MD 본문
  - `PUT    /api/notes/{id}` *(v2)*      — 편집 저장
- Storage: `STORAGE_ROOT/notes/<meeting_id>.md`
- DB 모델: `Note(id, meeting_id, title, created_at, updated_at, path, tags[], note_status)`

### 6.5 Dashboard React 추가

- 라우트:
  - `/meetings` — 회의 목록 (날짜·길이·언어·노트 상태·액션)
  - `/notes/:id` — 노트 뷰어
- 의존 라이브러리:
  - `react-markdown` — MD 렌더
  - `remark-gfm` — 체크박스·표 등 GFM 지원
  - `rehype-highlight` — 코드 하이라이트
- 스타일: 섹션 2 (a)의 공유 design tokens 적용 — 헤딩 위계 시각화, action item 콜아웃 박스, 결정 사항 강조

---

## 7. 진행 순서 (이 spec 이후)

1. **Phase 0 baseline 측정 인프라 구축** — 집계 스크립트 + 클라이언트 timing 코드. 4개 시나리오 실측.
2. **Phase 1 — macOS Native capture PoC**:
   - Swift ScreenCaptureKit helper 작성, IPC contract 따름
   - Python sidecar의 `AudioSource` 추상화 + `NativePipeSource` 구현
   - Tauri Rust의 native helper 라이프사이클 관리
   - 4개 시나리오 재측정, baseline과 비교
3. **노트 생성 서비스 v1** — server-side LLM 파이프라인, MD 저장, DB 모델, Trigger 두 경로(자동·수동), React `NoteViewer` 패널.
4. **Launcher 패턴 MVP** — `apps.json` 로더, spawn 메커니즘, 공유 auth 파일, 첫 PyQt5 앱 1개 연결로 end-to-end 검증.
5. **Phase 2 — Windows WASAPI capture** — `AudioCapture` 인터페이스의 두 번째 구현체. Phase 1의 sidecar/Tauri 코드 그대로 재사용 검증.

Phase 1과 노트 생성·Launcher MVP는 종속 관계가 약해 병렬 진행 가능. 단 디자인 토큰(섹션 2 (a))은 Launcher 시작 전에 결정 필요.

---

## 8. 부록 — 다른 문서와의 관계

| 문서 | 역할 | 이 spec과의 관계 |
|------|------|------------------|
| `docs/NATIVE_DESKTOP_HELPER_PLAN.md` | 제품/기술 방향 (왜·언제) | 상위. 본 spec은 그 방향의 구체 통합 설계 |
| `docs/PRD.md` | 제품 요구사항 | 회의 노트·KB·익스포트 요구는 PRD가 정의 |
| `docs/ROADMAP.md` | 일정/슬라이스 | Phase 0~2 진행이 ROADMAP 슬라이스에 반영돼야 함 |
| `docs/ARCHITECTURE.md` | 시스템 구조 | 본 spec 채택 시 ARCHITECTURE 갱신 필요 |
| `docs/SETUP_MEETING_PC.md` | 설치 가이드 | Native 캡처 도입 후 BlackHole/Voicemeeter 안내가 fallback section으로 이동 |

---

## 9. 열린 결정 사항 (이 spec 범위 밖)

- 노트 생성에 쓸 LLM 모델/프로바이더 (Gemini 별도 모델? Claude? 모델별 비용/품질 비교 별도 평가)
- 디자인 tokens의 구체 값 (디자인 단계에서 결정)
- 회의 데이터 보존 기간·삭제 정책
- 외부 PyQt5 앱들의 공유 디자인 시스템 통일 비용 산정
- 코드 서명·notarization 절차 (Phase 4 — 본 spec 범위 밖)
- Knowledge base 검색 인덱스/벡터 DB 선택
- 익스포트(Word/PDF/Excel) 렌더 도구 선택

---

## 10. 용어 정의

- **대시보드** — yeson-meet Tauri/React 클라이언트 본체
- **Audio sidecar** — `apps/client_sidecar/`의 Python 프로세스. 캡처 외 모든 통신·인증·재시도 담당
- **Native helper** — OS 네이티브 캡처 API를 호출하는 작은 보조 프로세스 (Swift on Mac, 추후 Rust on Win)
- **Launcher 패턴** — 대시보드가 외부 PyQt5 앱을 spawn·관리하는 방식
- **사이드카** — 별도 프로세스로 분리돼 dashboard와 통신하는 보조 서비스 (audio sidecar는 사이드카 패턴의 한 예)
- **회의** (`meeting`) — 한 번의 자막/번역 세션 단위. transcript와 1:1 매핑
- **노트** (`note`) — 한 회의 종료 후 생성된 MD 산출물
- **Phase 0/1/2** — `NATIVE_DESKTOP_HELPER_PLAN.md`의 단계 명명을 그대로 따름
