# ROADMAP — yeson-meet

> 최종 갱신: 2026-05-19
> 단위: 1인 풀타임 기준 영업일. 검토/대기 시간 제외.  
> **MVP-α** 5 슬라이스 + **MVP-β** 7 묶음으로 분리. β는 우선순위에 따라 선택적으로 진행.

> **오디오 캡처 경로 트랙 (병행)**:
> - 본 ROADMAP 의 Slice 0~5 / β-1~β-7 는 **현재 MVP-α path (Voicemeeter/BlackHole + sounddevice)** 기준으로 일정/완료 기준이 적혀 있다.
> - **Native helper track (Phase 0~4)** 은 별도 트랙으로 `docs/NATIVE_DESKTOP_HELPER_PLAN.md` 와 `docs/plans/2026-05-27-native-audio.md` 에서 관리. Phase 0 baseline 측정 → Phase 1 macOS native → Phase 2 Windows native 순.
> - Native track 안정화 시점에 본 ROADMAP 의 Slice 2 산출물 / β-1 의 Voicemeeter 자동 감지 항목은 fallback 카테고리로 격하 후 재기술 필요.
> - **(2026-05-28) Phase 1 macOS native E2E 기능 검증 완료**: ScreenCaptureKit 캡처 → Gemini 자막 실측(planar 버그 수정 포함), native 실패 시 권한 배너 + 앱 종료 시 sidecar/helper 프로세스 정리. 정량 비교(Task 7 baseline)와 위 격하는 native-only 컷오버 시점까지 보류.
> - **(2026-05-28) Phase 1 macOS packaging seam 코드 완료**: 헬퍼 Tauri `externalBin` 번들링 + Python provider 기본값 `auto`→`native` 고정 + `tauri.macos.conf.json`에서 `build-release.sh` 자가-부트스트랩. Operator 검증(번들 `.app`에 헬퍼 포함 & 실행 자막) 후 Phase 1 packaging seam done → Phase 2 Windows WASAPI 진입.
> - **(2026-05-29) Phase 2 Windows WASAPI 캡처→자막 E2E 기능 검증 완료**: 실제 Windows VM에서 유튜브 소리 → **Voicemeeter 없이** → 한국어 자막 실측. cpal WASAPI loopback(F32) → 16k mono 640B → 서버 → Gemini 자막 viewer(서버 로그 `AI utterance published` 확인). 단일 exe 올인원 테스트 도구(`apps/native_helper_win`, macOS→windows-gnu 크로스컴파일)로 검증. 교훈: `native-tls`(SChannel)가 WS 바이너리 미전송 → `rustls`(ring) 교체로 해결. **(2026-05-29) Tauri 프로덕션 번들 와이어링 완료(3 additive, 코드/CI)**: `sidecar.rs::locate_bundled_native_helper()` Windows x86_64+`.exe` 분기, `tauri.windows.conf.json` externalBin에 헬퍼 추가, `windows-desktop.yml`에 네이티브 MSVC 헬퍼 빌드+복사 스텝. 맥 `cargo check`/JSON·YAML 검증 통과, MSVC 빌드·번들·런타임 히트는 CI/VM 대기. **Phase 2b 잔여**: 프로덕션 helper+sidecar Windows 직접 E2E, 기본장치 변경 추적(`device_watch.rs`), Job Object 고아정리. 설계: `docs/superpowers/specs|plans/2026-05-28-windows-wasapi-helper*.md`.
> - **(2026-06-04) Phase 2 프로덕션 번들 E2E 검증 완료**: CI 아티팩트 `yeson-meet-desktop-windows`(NSIS setup) 설치본으로 Windows 실기에서 `Tauri → sidecar(PyInstaller) → yeson-win-audio-helper.exe(WASAPI stdout-PCM) → 서버 → 자막` 까지 실측(런타임 `locate_bundled_native_helper` 히트 = 직전까지 유일 미검증 항목 닫힘). **Phase 2 닫힘.** **Phase 2b 잔여(2건)**: ① Job Object 고아정리(무음 중 sidecar 강제종료 시 helper 잔존 — Python-level `KILL_ON_JOB_CLOSE`), ② 기본장치 변경 추적(`device_watch.rs`). 별도 항목: Tauri 크래시 시 subtree 정리(Tauri-level job).
> - **(2026-06-10) Phase 2b 닫힘 — ①② 모두 E2E PASS**: ① Job Object 고아정리 — Windows 실기 Task 5b(무음 중 사이드카 강제종료→helper 사라짐) 통과. ② 기본 출력장치 변경 추적(`device_watch.rs`) — 회의 중 출력장치 강등(헤드폰/BT) 시 옛 장치가 무음만 받아 자막이 조용히 끊기던 문제를, **폴링**으로 기본장치 변경 감지 + 헬퍼 **인프로세스 재빌드**(stdout 유지, sidecar/server 무변경)로 새 장치 전환해 해결. Mac `cargo test` + windows-gnu 크로스 `cargo check` + **Windows 실기 E2E(전환→자막 재개) PASS**(CI run 27246298588). 장치 *제거*는 기존대로 fatal 유지(강등만 대상). **Mac은 ScreenCaptureKit 시스템믹스 탭이라 무관**. 별도 신규: Windows 사이드카 시작 시 cmd 콘솔 깜빡임 수정(`CREATE_NO_WINDOW`) **E2E PASS**. 설계: `docs/superpowers/specs|plans/2026-06-10-windows-default-device-watch*.md`.
> - **(2026-06-10) Phase 3 slice 1 코드 완료·Windows E2E 대기**: 실시간 캡처 상태칩(연결중/정상/무음/전송끊김) — 사이드카가 `CAPTURE_STATUS` stdout 마커를 워치독으로 emit → 데스크톱이 app-log 파싱해 자막 헤더 칩 표시. 캡처 실패는 기존 배너 유지. 무음=10초+정보성+비대칭 히스테리시스(화자 비의존). Mac: 사이드카 pytest 50 + 데스크톱 vitest 12 + tsc 클린 + 코드리뷰 APPROVE. Windows 4상태 라이브 E2E 대기. 비범위(후속): dBFS 레벨미터, 소스 선택. 설계·계획: `docs/superpowers/specs|plans/2026-06-10-capture-status-ux*.md`.
> - **(2026-06-10) Phase 3 slice 2 코드 완료·Mac 실증 OK·Windows 회귀 대기**: RMS 기반 무음 감지 — slice 1 무음(청크 존재 기반)이 Mac에선 안 떴음(실측: SCK 무음에도 풀레이트 청크). 무음을 청크 소리크기(RMS dBFS≥-45/번들-60)로 판정해 양 플랫폼 통일(`last_loud_at` 추적). 데스크톱 무변경. Mac 실측으로 무음 도달 확인, 사이드카 pytest 56+vitest 12. Windows 4상태 회귀 대기. 설계·계획: `docs/superpowers/specs|plans/2026-06-10-rms-silence-detection*.md`.
> - **(2026-06-15) Phase 3 slice 1+2 Windows E2E PASS ✅ — 캡처 상태칩 트랙 닫힘**: 새 CI 빌드 설치본으로 Windows 실기에서 4상태 전부 실증. ① 재생→🟢 정상 칩+자막, ② 일시정지(완전 무음) ~10초→🟡 무음, 재생 재개→🟢 즉시 복귀(비대칭 히스테리시스 확인), ③ 재생 중 Wi-Fi/네트워크 off→🔴 전송끊김, 복구→🟢/⚪ 복귀, ④ 기본 출력장치 비활성→칩 아닌 기존 경고 배너(실패=배너 담당). RMS dBFS 무음 판정이 Windows에서 회귀 없이 동작. 후속 후보(미착수): dBFS 레벨미터(RMS 이미 계산 중), 캡처 소스 선택(네이티브 기본장치 고정이라 sounddevice 전용), device 제거 시 자가치유(§7 보류), native-only 컷오버·codesign(Phase 4).
> - **(2026-06-15) Phase 3 slice 3 — dBFS 캡처 레벨미터: 코드 완료·단위 검증·Windows E2E 대기**: 상태칩 옆 6칸 세그먼트 음량 미터. 사이드카 워치독이 slice 2의 `pcm16_dbfs`를 1초 평균내 `CAPTURE_LEVEL <dbfs>` stdout 마커로 emit → Rust 포워더가 **전용 `capture-level` Tauri 이벤트**로 라우팅(app-log 미적재 → 진단 로그·저장 스냅샷 청결) → 데스크톱 `CaptureLevelMeter`. 미터는 active/silent에서만 렌더(connecting/transport_down은 칩이 전달, silent=빈 막대로 Windows 무음=청크0 케이스 흡수). dBFS [-54,-6]→6칸, 색상 등급 초록→노랑(-16↑)→빨강(-10↑). 마커 형식은 워치독이 단독 소유(main.py 콜백은 단순 print). 검증: 사이드카 pytest 63(레벨 평균/staleness/마커 -0.0 가드 + 워치독 async) + 데스크톱 vitest 19(dbfsToSegments 매핑 + segmentColorRole 색상) + tsc 클린 + cargo check 클린 + 코드리뷰 2건(스펙·품질) 반영(레벨미터 색상 빨강 도달불가 버그 수정). Windows 4상태 라이브 미터 E2E 대기. 설계·계획: `docs/superpowers/specs|plans/2026-06-15-capture-level-meter*.md`.
> - **(2026-06-15) Phase 4-A native-only 컷오버 완료(코드)**: sounddevice(BlackHole/Voicemeeter) 캡처 경로를 사이드카·Rust·데스크톱 setup UX·문서에서 제거. native(SCK/WASAPI)가 **유일** 캡처 경로. 제거: `sounddevice_source.py`/`capture.py`/`resample.py`/`device.py` + `sounddevice`·`samplerate` 의존성(numpy는 레벨미터·RMS가 써서 유지), `audioDeviceName` 엔드투엔드(데스크톱 모델·생성명령·검증·Rust `SidecarStartRequest`·`YESON_AUDIO_DEVICE_NAME` env). factory는 native 단독(헬퍼 없으면 FileNotFoundError, 제거된 provider값엔 경고). 데스크톱 setup 카피·도움말은 native-first(가상오디오 설치 안내 제거). 검증: 사이드카 pytest 51 + 데스크톱 vitest 19/tsc + cargo check 클린 + grep 게이트(sounddevice/audioDeviceName/DEVICE_NAME 0). 가상오디오 자료는 git 히스토리 보존. 아래 §S2 산출물·β-1 Voicemeeter 자동감지 항목은 이 컷오버로 **제거됨(히스토리 보존)**. 후속: B/C 코드서명(인증서 선행), ~~Rust voicemeeter 진단 바이너리 제거~~(✅ 2026-06-16 완료). 설계·계획: `docs/superpowers/specs|plans/2026-06-15-native-only-cutover*.md`.
> - **(2026-06-16) Phase 3 slice 3 레벨미터 + Phase 4-A 컷오버 Windows E2E PASS ✅ — 캡처 UX 트랙 종료, native-only 검증.** 새 CI 빌드 설치본으로 Windows 실기 실증. ① **레벨미터 4상태**: 재생→음량 따라 막대 채워짐(큰 소리에 상위 칸 노랑/빨강) / 일시정지→빈 막대 / 네트워크 off→미터 사라지고 칩만 / **설정 진단 로그에 `CAPTURE_LEVEL` 줄 없음 = 전용 `capture-level` 이벤트 채널이 app-log를 오염시키지 않음을 확인**(설계 의도 = 통과 조건). ② **컷오버 회귀**: 가상오디오(Voicemeeter/BlackHole) 없이 새 회의에서 native 캡처만으로 자막 정상. → Phase 3 캡처 상태 UX(칩+미터) 전부 닫힘, native-only 경로 Windows 실기 무회귀 확인. **다음 = Phase 4 B/C 코드서명·공증·installer**(인증서 확보가 선행, 코드 외 조달 게이트). B: Apple Developer ID + notarytool, C: Windows 코드서명 인증서.
> - **(2026-06-16) no_audio advisory (코드 완료·Windows E2E 대기)**: 캡처 상태머신에 5번째 상태 `no_audio` 추가 — 30초 넘게 큰 소리가 없으면(처음부터든 도중이든) 기존 🟡 무음 칩을 "출력장치 확인" 행동 유도 툴팁으로 격상. 06-08 "⚪ 연결중에 갇혀 이유 모름"(Windows 무음=패킷0) 블라인드를 닫음. 에러 아님·소리 나면 즉시 복귀. `connected_at` 기준점으로 "한 번도 소리 없음"까지 커버. Rust/헬퍼/CI 무변경(CAPTURE_STATUS는 app-log 경유). 검증: 사이드카 pytest 60(no_audio 전이·재연결 미리셋 회귀 + 기존 무회귀) + 데스크톱 vitest 19/tsc 클린 + 2단계 코드리뷰(스펙·품질) 반영. 자연스러운 말 끊김(<30초)은 격상 안 됨. 설계·계획: `docs/superpowers/specs|plans/2026-06-16-no-audio-advisory*.md`.

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
- [~] Windows 회의실 PC에서 영어 영상 1분 재생 → 서버 로그에 약 3000개 청크(20ms × 50/sec × 60s) 수신  ← **시스템 부원 협조 단계 대기 (PRD §10 락)**, SETUP_MEETING_PC.md §2 placeholder
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
- [~] 🔴 **회의 시간 안전 타이머** — 회의당 최대 N시간 (기본 3h) 도달 시 자동 종료 + alert (좀비 세션 비용 방지)  ← sidecar 오디오 ingress + lifespan background watchdog(`apps/server/ops/session_safety_scheduler.py`, env `YESON_MEETING_SAFETY_POLL_SECONDS` 기본 60s, ≤0 비활성)로 오디오 흐름과 무관하게 wall-clock 초과 세션 자동 종료 + operator alert + viewer `SessionEnded` 통지(공유 `enforce_meeting_duration_limit`). 단위/sweep/loop 테스트 완료. 3시간 라이브 E2E는 수동/운영자 검증 남음
- [~] 🟡 **partial→final 자막 안정화** — `is_final` 플래그 + viewer가 `seq` 키로 마지막 partial 교체  ← viewer state upsert + Gemini provider seq 재시작 보정(`AISequenceNormalizer`) 검증 완료, 1분 E2E에서 seq 1~8 partial/final 수신·DB 저장 완료. 브라우저 시각 깜빡임 E2E는 미완료
- [~] 🟡 **VAD 또는 RMS 임계값으로 무음 청크 차단** (비용 절감)  ← sidecar RMS silence gate 구현·단위 검증 완료 (`YESON_RMS_DBFS_THRESHOLD`, `YESON_RMS_SILENCE_GATE_ENABLED`), 실제 회의실 threshold 튜닝 미완료
- [x] 시스템 프롬프트에 "혼합 언어 한국어 그대로 두기" 명시

> 2026-05-18 코드 진행: `STTProvider`/`TranslationProvider` 인터페이스, `GeminiLiveProvider` lazy SDK adapter, sidecar audio→AI session wiring, AI utterance DB persist + viewer bus fan-out, Gemini config health endpoint, provider disconnect retry/backoff, AI publish latency structured log, Gemini usage token/cost structured log, viewer partial→final seq replacement, Gemini Live model/env 정정(`gemini-3.1-flash-live-preview`), AUDIO + `output_audio_transcription` 기반 실시간 transcript fan-out, `audio_stream_end` 전달, provider seq 재시작 보정, sidecar RMS silence gate 구현. 검증: `uv run pytest apps/server/tests -v` → 22 passed / 4 skipped, `uv run pytest apps/client_sidecar/tests -q` → 18 passed, `pnpm --filter @yeson-meet/web build` → pass, `git diff --check` → clean. 실제 Gemini API Key 기반 local synthetic E2E(서버+테스트 sidecar 동일 개발 머신)에서 59.37초 synthetic 영어 오디오 viewer seq 1~8 / DB utterance 8개 저장, phrase-end→first viewer subtitle P50 1419.8ms / max 1522.3ms. 단, LAN 회의실 PC↔서버 분리 지연·브라우저 렌더·실제 오디오 라우팅은 아직 별도 검증 필요.
>
> 2026-05-19 완료: Windows 앱 패키지 전 사전 점검용 desktop setup assistant를 `apps/desktop/src/setup/` 모듈 구조로 구현하고, `App.tsx`는 `SetupAssistant`만 렌더링하도록 유지. setup assistant에서 서버 `/api/v1/health`, Gemini `/api/v1/health/ai`, viewer URL 접근 최소 스모크를 제공하며, Device API Key는 localStorage에 저장하지 않고 PowerShell 명령 값은 escaping 처리. Tauri packaged origin(`http://tauri.localhost`)을 서버 CORS allowlist에 추가. 검증: `pnpm --filter @yeson-meet/desktop build:vite` → pass, `uv run pytest apps/server/tests -q` → 25 passed / 4 skipped, desktop LSP diagnostics → 0 errors, `GIT_MASTER=1 git diff --check -- apps/desktop/src apps/server/main.py` → clean.
>
> 2026-05-19 추가 진행: desktop 앱을 `DesktopConsole` shell로 전환하고 setup assistant / live meeting 탭을 분리. Live Meeting 탭에 operator login, session create/end/report API wiring, viewer URL 복사, QR 표시, Markdown report preview, operator JWT 기반 live subtitle preview(backfill + `/ws/operator?access=...`)를 구현. session create 성공 시 `session_id`/`viewer_url`을 setup assistant 저장값에 반영해 PowerShell sidecar handoff 값이 실제 회의 정보로 갱신되도록 연결. 검증: `pnpm --filter @yeson-meet/desktop build:vite` → pass, `uv run pytest apps/server/tests -q` → 25 passed / 4 skipped, desktop LSP diagnostics → 0 errors, `GIT_MASTER=1 git diff --check -- apps/server apps/desktop/src docs/ROADMAP.md` → clean.

### 완료 기준
- [x] 영어 1분 synthetic 오디오 → 한국어 자막 viewer에 흐름  ← local synthetic E2E 통과: viewer WS 16개 partial/final 이벤트 수신, DB utterance seq 1~8 저장
- [~] 실제 회의실 PC↔서버 LAN 분리 환경에서 영어 1분 영상 재생 → 한국어 자막 viewer에 흐름  ← Mac BlackHole 청크 전송은 검증 완료(S2), Gemini 포함 LAN 분리 E2E는 **Windows 앱 패키지/실행 UX가 나온 뒤 진행**. 현재는 CLI 복잡도가 높아 desktop setup assistant로 서버 health, Gemini health, viewer URL 최소 스모크까지만 확인
- [x] local synthetic 자막 지연 P50 ≤ 2초  ← wall-clock phrase-end→first viewer subtitle P50 1419.8ms / max 1522.3ms, server→viewer P50 5.2ms / max 82.4ms
- [~] LAN 분리 환경 자막 지연 P50 ≤ 2초  ← Windows 회의실 앱으로 실제 운영 경로가 단순화된 뒤 측정
- [~] Gemini 세션 끊김 → 5초 안에 재연결, 자막 일부 손실 외 회의 진행 유지  ← provider disconnect retry 단위 검증 완료, 실제 Gemini WS 끊김 E2E 미완료
- [~] 좀비 회의 자동 종료 (3시간 도달 테스트)  ← sidecar ingress + lifespan background watchdog(오디오 흐름 없는 좀비 세션까지 커버) 단위/sweep/loop 검증 완료, wall-clock 3시간 라이브 E2E는 Windows 앱 검증 단계에서 측정
- [~] partial→final 갱신 시 viewer 깜빡임 없음  ← 동일 `seq` final이 partial을 교체하는 상태 로직 검증 완료, 브라우저 시각 E2E 미완료

---

## Slice 4 — 회의 라이프사이클 (2.5~3.5일)

> 회의 1회를 처음부터 끝까지 운영 가능 + UI 확장성 1차 자리 박기.

### 산출물
- 데스크톱 UI:
  - [x] 로그인 화면 — desktop operator email/password login + access token 저장
  - [x] 회의 시작 화면: 제목 / 클라이언트 라벨 / visibility (기본 `org`) — session create API wiring
  - [x] 라이브 콘솔: **자막 패널만** (큰 글씨 기본값) — operator JWT backfill + `/ws/operator` live subtitle preview
  - [x] 회의 종료 버튼 — session end + Markdown report load/preview
- **UI 확장성 1차 정의** (`docs/UI_DESIGN_SYSTEM.md` 참조):
  - `packages/ui/layout/AppShell.tsx` + `ConsoleShell.tsx` — 5슬롯 컴포지션
  - `packages/ui/src/tokens.css` — 디자인 토큰 1차 (color / spacing / typography 5단계)
  - 라우팅 placeholder: `/console/{history,glossary,admin}` "준비 중" 페이지 + nav에 disabled 항목
  - zustand 단일 store 스켈레톤 (`useMeetingStore`, `useUiStore`)
- [x] QR + URL 표시 (PIN은 β로 보류) — viewer URL 표시/복사 + QR 표시 완료
- [x] WS `/ws/operator` (JWT 인증, 운영자 전용)
- [x] `/api/v1/sessions/{id}/end` → MD 리포트 생성 (영어 원문 + 한국어 번역 로그)
- [x] `/api/v1/sessions/{id}/report` 다운로드
- [x] viewer: 회의 종료 시 "회의 종료됨" 화면
- [~] **좀비 세션 자동 종료** — sidecar ingress 최대 시간 초과 자동 종료는 완료. disconnect N분 scheduler 구현 완료: `disconnected_at` 컬럼 + WS lifecycle stamp/clear + 공유 워치독 sweep(`enforce_sidecar_disconnect_limit`, env `YESON_MEETING_DISCONNECT_GRACE_SECONDS` 기본 300s) + 재시작 re-stamp. 단위/sweep 테스트 완료. 라이브 disconnect E2E는 수동 검증 남음
- [~] **회의 종료 시 큐 flush + timeout** — 현재 dev sidecar는 서버 종료 이벤트 수신/flush 계약 없음. Slice 5 SQLite 오프라인 큐와 함께 구현
- [x] **같은 Device 동시 회의 방지** — sidecar 연결 시 동일 device의 다른 live session 거부
- [~] **MD 리포트 streaming 생성** — 현재 Markdown report 생성/다운로드 완료, 1시간+ streaming 최적화는 장시간 부하 측정 후 β/운영 안정화에서 수행
- [~] **QR 회의실 모니터 전체화면 모드** — 기본 QR 표시 완료, 2~4m 전체화면 모드는 β-2 회의실 모드와 함께 수행

### 기획 closeout (2026-05-19)
- [x] **현재 코드로 닫을 수 있는 Slice 4 산출물**: operator login, session create/end/report, viewer URL/QR, operator subtitle preview, `/ws/operator`, 같은 device 동시 live session guard.
- [x] **외부 환경 의존 검증은 명시 보류**: Windows 회의실 앱 패키지/실행 UX, 회의실 PC root CA 신뢰 등록, 실제 LAN viewer 다중 접속, 30분 모의 회의.
- [x] **다음 구현 묶음 경계**: disconnect heartbeat/scheduler와 sidecar flush는 Slice 5 SQLite 오프라인 큐/heartbeat 계약과 같이 설계한다.
- [x] **재개 트리거**: Windows 앱 실행 UX가 준비되면 `SETUP_MEETING_PC.md §1.5` 기록 템플릿으로 LAN P50, viewer 3대, 30분 모의 회의를 측정한다.

### 완료 기준
- [~] 30분 모의 회의(YouTube 영어 영상)를 시작 → 진행 → 종료  ← Windows 앱 실행 UX + 실제 회의실 PC 오디오 라우팅 준비 후 측정
- [x] MD 리포트 다운로드 → 발화 시간순 정렬 + 영어/한국어 매칭  ← `/api/v1/sessions/{id}/report` + desktop Markdown preview + server lifecycle tests 완료
- [~] viewer 3대(PC/폰/태블릿) 동시 자막 정상  ← viewer URL/QR 및 fan-out 구현 완료, 실제 다중 단말 LAN QA는 Windows 앱 검증 단계
- [~] 운영자 앱 강제 종료 시 5분 후 서버가 좀비 세션 정리  ← 최대 회의 시간 안전장치/동시 device guard 완료. heartbeat-free disconnect scheduler 코드 완료(`disconnected_at` + WS lifecycle stamp/clear + 공유 워치독 sweep `enforce_sidecar_disconnect_limit`, env `YESON_MEETING_DISCONNECT_GRACE_SECONDS` 기본 300s + 재시작 re-stamp, server-only). 라이브 disconnect E2E는 수동 검증 남음
- [~] 회의 종료 직후 sidecar 잔여 청크가 리포트에 포함됨  ← 종료/report 경로 완료, sidecar flush 계약은 SQLite 오프라인 큐와 함께 이월

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

> (2026-05-28) **macOS standalone sidecar local/internal beta seam** — PyInstaller lean 번들 + truststore(OS 신뢰저장소) TLS (`topyeson`). 설계: `docs/superpowers/specs/2026-05-28-standalone-mac-sidecar-design.md`. 잔여: MSI/DMG·codesign·notarization·Updater·Windows sidecar·CA 배포 자동화.

- [ ] Tauri MSI 빌드 스크립트 1순위, DMG 빌드 스크립트 2순위
- [ ] PyInstaller로 sidecar 단일 실행파일 — macOS local/internal beta seam ✅ 2026-05-28 (lean native-only); Windows ⏳ Phase 2
- [ ] macOS 코드사인 + 노타리제이션
- [ ] Windows 코드사인
- [ ] Tauri Updater (GitHub Releases 또는 사내 정적 서버)
- [ ] 사내 root CA 인증서 배포 자동화 — 미완료. 이번 slice 는 truststore 로 **소비 경로만** OS 신뢰저장소에 맞춤; 회의실 PC Keychain root CA 등록/Always Trust 절차는 별도 운영 과제

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

- [~] Tunnel 모드 (Cloudflare Tunnel) — Quick Tunnel(임시·계정불필) 코드/런북 완료: cloudflared compose 서비스(profile opt-in) + Caddy :8080 origin + `tunnel-quick.sh`가 `VIEWER_BASE` 자동 설정. 라이브 터널 E2E는 운영자 수동. Named Tunnel(고정 도메인)·SSO/PIN은 후속
- [ ] HTTPS 공개 도메인 + 외부 인증 모델 (사내 SSO 또는 매직링크)
- [ ] Google OAuth verification (Production 모드)
- [ ] MCP 익스포트 (Claude/외부 AI가 회의 컨텍스트 호출)
- [ ] 화자 자동 구분 (diarization)
- [ ] 양방향 마이크 캡처 + 에코 제어
- [ ] 회의 검색 (full-text)
- [ ] 모바일 네이티브 viewer (Capacitor 또는 RN)
- [ ] 다국어 회의(영중일혼합) 지원

### 영상 자막 스튜디오 (Video Caption Studio)

> (2026-07-03) `video_caption_studio` 브랜치. 업로드/유튜브 영상 → faster-whisper 로컬 전사 → Gemini 배치 번역(용어사전) → 검수 뷰(플레이어+오버레이+세그먼트 편집) → 스타일 조정 후 ffmpeg burn-in → MP4/SRT 다운로드.

- [x] T1~T13: 서버 도메인(모델 매니저/업로드/유튜브 입수/전사/번역/burn-in) + API + 클라 탭·검수 뷰·스타일 조정 UI 구현
- [x] T14: `fetch-ffmpeg.sh`(cloudflared 패턴 복제, evermeet/BtbN/johnvansickle 정적 바이너리 벤더링) + `build-server.sh`/`build-server.ps1` PyInstaller `--collect-all faster_whisper/ctranslate2/av/onnxruntime/yt_dlp` + `tauri.conf.json` `binaries/ffmpeg-*` 리소스 glob + `server_process.rs::locate_bundled_ffmpeg()` → `YESON_FFMPEG_BIN` 주입 + 프로즌 재동결·자동화 스모크(curl 인증 게이트) 완료
- [ ] T14 수동 E2E (자동화 세션 불가 — 운영자 확인 필요): 클라 "영상 자막" 탭 로그인→모델 목록, tiny 모델 실다운로드, 로컬 mp4 업로드→전사/번역/검수 진행, 검수 뷰 플레이어+오버레이+세킹+텍스트 수정, 스타일 조정→burn→MP4 다운로드 자막 위치 일치, 유튜브 URL 실입수 1건, 서버 pytest 전체 + 클라 vitest 전체 재확인

### 스토리보드 PDF 번역 (Storyboard PDF Translate)

> (2026-07-29) `feat/pdf-translate-slice1` 브랜치. 영문 납품 문서(대본/스토리보드/컬러노트/리드시트 PDF) → 포맷 프로파일 자동 감지 → Dialog/Action Notes 블록 번역(자막메이커 번역 엔진 스택 재사용) → 원본 위 한국어 FreeText 주석 오버레이 → 프리뷰(페이지 PNG)·번역 PDF 다운로드. 설계 근거: `docs/pdf-translation-feasibility-2026-07-29.md`(확정 결정 섹션 포함), 계획: `docs/superpowers/plans/2026-07-29-pdf-translate-slice1.md`.

- [x] Task 1~9: PDF 백엔드 격리(`domain/pdf_translate/backend.py` 인터페이스 + `backend_mupdf.py` PyMuPDF 구현, AGPL 교체점 1파일) + 포맷 프로파일 플러그인(`profiles/`, Storyboard Pro 1종 구현) + 번역 프로바이더 `prompt_builder` 주입(기본값=기존 자막 프롬프트, 잠금 테스트로 무변경 보장) + PDF 전용 프롬프트·리질리언트 배치 번역(`translate_blocks.py`) + `PdfJob` 모델 + alembic `0007_pdf_jobs` + 잡 러너(`pdf_tasks.py`/`pdf_run.py`, video_captions 패턴 미러) + API `/pdf-jobs` 라우트(업로드/상태/취소/삭제) + Tauri 업로드 커맨드(Rust) + 프런트 최상위 "스토리보드 번역" 탭(A안) + `PdfTranslatePanel`(업로드·목록·진행률·취소·삭제) 구현
- [x] Task 10: 프리뷰(원본/번역 페이지 PNG 토글, 페이지 이동) + 번역 PDF 다운로드 UI
- [x] Task 11: 동결 번들 반영 — `build-server.sh`/`build-server.ps1` `--collect-all pymupdf --hidden-import fitz`(+ cv2 전례와 동형의 uv 캐시 미실체화 가드) + PDF 셀프테스트(`YESON_PDF_SELFTEST=1`, 1페이지 한글 FreeText 주석 왕복+PNG 래스터 검증) + `smoke-server-bundle.sh` 통합 + 문서 갱신. 로컬(Intel Mac) 재동결+스모크 PASS 확인
- [ ] Task 11 남은 실물 E2E (수동, 사람 확인 필요): `GABE01_A3_FinalShipped.pdf`(129MB) 업로드→추출→번역→프리뷰→"번역 PDF 저장" 왕복 + macOS 미리보기/Acrobat에서 한글 주석 렌더 확인(어피어런스 폰트 이식성) + 수작업 번역본과 배치 비교 + 번역 중 취소→재업로드 + `EASA04_ColorNotes_V04.pdf`로 "지원하지 않는 PDF 포맷" 오류 확인 + Windows 왕복
- [ ] 슬라이스 1 범위 밖(후속): 대본형(Final Draft)·컬러노트·리드시트 프로파일 추가(구조는 `profiles/`에 준비됨), 컬러노트·리드시트 한국어 배치 방식 결정(본문 삽입 vs 주석 통일), 기존 수작업 번역본에서 원문-번역 쌍 추출 → few-shot/용어집 보강

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
