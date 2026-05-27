# SETUP_MEETING_PC — 회의실 PC 셋업

> MVP-α S2: Intel Mac + BlackHole 와 Windows + Voicemeeter Banana 두 갈래 모두 지원. Windows 자동 라우팅은 yeson-meet-desktop S2~S4 슬라이스에서 추가 예정 — 그 전까지는 §2의 수동 절차로 진행.

> **단계 표시**:
> - 본 가이드 = **current MVP-α path** (외부 가상 오디오 장치 기반).
> - **Planned (Phase 1~2 native)**: ScreenCaptureKit(Mac) / WASAPI(Win) 직접 캡처로 BlackHole·Voicemeeter 설치 자체가 불필요해질 예정. `docs/NATIVE_DESKTOP_HELPER_PLAN.md` + `docs/INTEGRATION_DESIGN.md` §3 참조. native 안정화 시 본 가이드는 fallback section 으로 이동한다.

## 1. macOS (Intel Mac, S2 PoC 우선)

### 1.0 진행 로그

| 시간 | 단계 | 상태 | 근거 |
|---|---|---|---|
| 2026-05-18 | Intel Mac 확인 | 완료 | `uname -m` → `x86_64`, CPU → Intel Core i9-9900K |
| 2026-05-18 | BlackHole 2ch 인식 | 완료 | 재부팅 후 `system_profiler SPAudioDataType` → `BlackHole 2ch`, `sounddevice.query_devices()` → index `8 BlackHole 2ch` |
| 2026-05-18 | BlackHole 2ch 설치 | 차단 | `brew install --cask blackhole-2ch` 다운로드 성공 후 macOS 관리자 암호 입력 필요로 중단 |
| 2026-05-18 | BlackHole GUI 설치 | 완료(사용자 확인) | cached pkg를 `open`으로 실행했고 Installer가 restart 필요 메시지 표시 |
| 2026-05-18 | 설치 후 재확인 | 완료 | macOS restart 후 OS/Python 양쪽에서 BlackHole 2ch 인식 확인 |
| 2026-05-18 | sidecar→server 청크 E2E | 완료 | session `8390b139-5ff9-42f7-95ac-0c4aa8047c02`: 65초 폴링 `chunks_per_sec_1s=50`, peak `51`, top-up 후 `total_chunks=3522`, `total_bytes=2254080`, `age_ms=2` |
| 2026-05-18 | 사용자 직접 재현 | 완료 | Multi-Output Device + BlackHole 2ch로 `chunks/sec=49~51`, `total_chunks=26306`, `total_bytes=16835840`, `age_ms=2~22` 확인 |

### 1.1 BlackHole 2ch 설치

1. https://existential.audio/blackhole/ → BlackHole 2ch 다운로드 (Apple Silicon은 16ch도 OK)
2. 설치 후 `system_profiler SPAudioDataType | grep BlackHole` 로 확인

### 1.2 Multi-Output Device 생성 (음성 들으면서 캡처)

목적: 영상/회의 소리를 **내장 스피커**로 들으면서 동시에 **BlackHole로 라우팅**

1. Spotlight → "Audio MIDI Setup" 실행
2. 좌측 하단 `+` → **Create Multi-Output Device**
3. 새 디바이스에 체크: ✅ Built-in Output (또는 헤드폰), ✅ BlackHole 2ch
4. **Drift Correction**: Built-in Output에 ✅ (BlackHole은 X)
5. **Master Device**: Built-in Output
6. 시스템 환경설정 → 사운드 → 출력 → 방금 만든 **Multi-Output Device** 선택

### 1.3 sidecar 동작 확인

```bash
# 1) seed (한 번만)
docker compose --env-file .env -f deploy/docker-compose.yml up -d
uv run python -m apps.server.db.seed

# 2) admin login + device 발급
# (자세한 절차는 README 참조)

# 3) sidecar audio 모드 (BlackHole 자동 인식)
YESON_DEVICE_API_KEY=<plaintext-key> \
YESON_SESSION_ID=<session-uuid> \
YESON_SIDECAR_MODE=audio \
uv run python -m apps.client_sidecar.main

# 4) admin page 모니터링
# 브라우저: http://localhost:5173/admin/audio-stats?session=<uuid>&token=<jwt-access>
# 영어 영상 1분 재생 → 초당 ≈ 50 청크 / 총 ≈ 3000 청크 확인
```

### 1.4 트러블슈팅

- `BlackHole input not found` 에러: `YESON_AUDIO_DEVICE_NAME` env 변경 또는 `YESON_AUDIO_DEVICE_INDEX` 지정
- 청크 누락: Drift Correction 미설정 시 발생 가능 → Audio MIDI Setup 재확인
- 무음: Multi-Output Device가 시스템 출력으로 선택됐는지 확인 (control center에서 음원 라우팅 확인)

### 1.5 LAN 분리 Gemini E2E 지연 검증 (Windows 앱 전까지 보류)

목적: local synthetic E2E가 아니라 **회의실 PC(sidecar) ↔ 서버 ↔ viewer(폰/브라우저)**가 분리된 실제 LAN에서 자막 지연 P50 ≤ 2초를 확인한다.

현재 판단: MVP-α CLI sidecar로도 검증은 가능하지만, 회의실 PC 담당자 입장에서는 환경변수·인증서·오디오 라우팅·터미널 실행이 한 번에 겹쳐 너무 복잡하다. 따라서 **full LAN Gemini E2E는 Windows 앱 패키지/실행 UX가 나온 뒤 진행**한다. 그 전까지는 아래 서버/WSS 최소 스모크만 확인한다.

#### Windows 앱 전 최소 스모크

- 서버 PC에서 `https://<server-host>/api/v1/health`가 200인지 확인한다.
- 회의실 PC/폰에서 `https://<server-host>` 또는 viewer URL 접속이 되는지 확인한다.
- 필요 시 root CA 신뢰 등록만 미리 검증한다.
- 영어 1분 영상 → 실제 자막 P50 측정은 Windows 앱이 나온 뒤 진행한다.

#### 네트워크 전제

- 회의실 PC는 서버와 유선 LAN 연결(10Gb NIC link-up이면 충분, 대역폭 병목 아님).
- viewer 폰/노트북은 회의실 Wi-Fi/AP에 접속하되, 서버의 HTTPS/WSS 주소에 접근 가능해야 한다.
- 게스트 Wi-Fi 또는 AP client isolation이 켜져 있으면 viewer가 서버에 접근하지 못할 수 있다.
- 서버와 회의실 PC는 NTP/자동 시간 동기화가 켜져 있어야 latency 로그 비교가 가능하다.

#### 서버에서 준비

```bash
# 1) 서버 + DB + Caddy 기동
docker compose --env-file .env -f deploy/docker-compose.yml up -d

# 2) Gemini health 확인 — 키 값은 출력하지 않고 configured 여부만 확인
curl -fsS http://127.0.0.1:8000/api/v1/health/ai

# 3) 운영자 로그인/세션 생성/device key 발급
# TODO(S4): 데스크톱 UI가 붙기 전까지는 seed 또는 API 호출로 발급.
# 산출물로 아래 3개 값을 회의실 PC/폰 테스트에 사용한다.
# - YESON_DEVICE_API_KEY=<plaintext-device-key>
# - YESON_SESSION_ID=<session-uuid>
# - VIEWER_URL=https://<server-host>/v/<viewer-token>
```

#### 회의실 PC에서 실행

```bash
# SERVER_WS_BASE는 서버 주소 기준. Caddy/TLS 경유 시 wss://<server-host> 사용.
SERVER_WS_BASE=wss://<server-host> \
YESON_DEVICE_API_KEY=<plaintext-device-key> \
YESON_SESSION_ID=<session-uuid> \
YESON_SIDECAR_MODE=audio \
uv run python -m apps.client_sidecar.main
```

1. 회의실 PC 출력 장치를 Multi-Output Device(스피커 + BlackHole)로 설정한다.
2. 영어 1분 영상 또는 동일 문장을 6~10회 반복한 테스트 음원을 재생한다.
3. 폰/노트북에서 `VIEWER_URL`을 열고 자막이 partial→final로 갱신되는지 본다.

#### 합격 기준과 기록 값

- 서버 `/api/v1/health/ai`가 `configured: true`.
- sidecar 로그에 `audio ws connected`가 보이고 약 50 chunks/sec가 유지된다.
- viewer에서 한국어 자막이 1분 동안 끊기지 않고 흐른다.
- DB utterance `seq`가 발화 수만큼 단조 증가한다(예: 8발화면 seq 1~8).
- 발화 종료 시점 → 첫 viewer 자막 표시 P50 ≤ 2초.
- 기록 예시:

```text
date: 2026-..-..
network: meeting PC wired 10GbE, viewer phone on <AP name>
server_host: <server-host>
session_id: <uuid>
audio_source: 1min English test video / repeated phrase
chunks_per_sec_1s: min/median/max
db_utterance_count: N
viewer_seq_range: 1..N
phrase_end_to_first_subtitle_p50_ms: NNNN
phrase_end_to_first_subtitle_max_ms: NNNN
notes: Wi-Fi AP, browser/device, any drops
```

#### 실패 시 먼저 볼 곳

- viewer 접속 실패: 폰이 서버 URL을 열 수 있는지, 게스트 Wi-Fi/client isolation 여부 확인.
- chunks/sec가 50보다 낮음: 회의실 PC CPU, BlackHole/Voicemeeter 라우팅, Drift Correction 확인.
- 자막만 늦음: 서버→Gemini 외부망 품질, Gemini Live 로그, partial transcript 수신 여부 확인.
- DB `seq`가 덮어써짐: provider seq 재시작 보정(`AISequenceNormalizer`)이 적용된 서버 이미지인지 확인.

## 2. Windows (Voicemeeter Banana — S2 정식 검증 대상)

> macOS의 BlackHole 라우팅과 동일한 효과를 Windows에서는 **Voicemeeter Banana**로 만든다.
> 회의 상대방 음성은 스피커로 그대로 들으면서, 같은 소리가 가상 오디오 라인(B1)을 통해 yeson-meet 사이드카로도 전달된다.
> 마이크는 OS 기본 입력으로 두고 미팅 앱이 직접 잡으므로, sidecar는 "회의 상대방 음성"만 깔끔하게 받아 자막/번역 처리한다.
> 현재 ①~③ 단계는 1회 수동 설정. yeson-meet-desktop S2~S4 슬라이스 완료 시 ①~③은 앱이 자동 적용 예정.

### 2.1 Voicemeeter Banana 설치 (한 번만)

1. https://vb-audio.com/Voicemeeter/banana.htm 접속 → **Voicemeeter Banana** 다운로드 (도네이션웨어, 무료 사용 가능)
2. 다운받은 설치 파일 더블클릭 → **Next** 계속 누르며 설치 완료
3. 설치가 끝나면 **Windows 재부팅** (가상 오디오 드라이버 등록을 위해 필수)

### 2.2 ① Windows 기본 출력 변경

1. 작업표시줄 우하단 스피커 아이콘 우클릭 → **사운드 설정**
2. **재생** 탭에서 **"Voicemeeter Input (VB-Audio Voicemeeter VAIO)"** 선택 → **기본값으로 설정** 클릭

이제 PC의 모든 소리(YouTube, Zoom 상대 음성, 알림)가 Voicemeeter를 통해 흐릅니다.

### 2.3 ② Voicemeeter 라우팅 설정

1. 시작 메뉴 → **Voicemeeter Banana** 실행
2. 화면 우상단 **A1** 클릭 → 실제 스피커 / 헤드셋 선택
3. 화면 가운데 **"Voicemeeter VAIO"** Strip에서 **A1**, **B1** 두 버튼 모두 켜기 (밝게 빛나는 상태)

```
회의 소리 → Voicemeeter Input
  ├─ A1 → 실제 스피커 / 헤드셋 (사람이 들음)
  └─ B1 → Voicemeeter Output (sidecar가 캡처)
```

### 2.4 ③ 미팅 앱 스피커 설정

- Zoom / Google Meet / Teams 등 회의 앱의 **스피커** 설정에서 **"Voicemeeter Input"** 선택
- 시스템 기본값으로 두어도 동작하지만, 직접 지정하는 편이 실수가 적습니다.
- **마이크는 OS 기본 마이크 그대로** 둡니다 (Voicemeeter를 거치지 않음).

### 2.6 설정 점검 — `voicemeeter_dump.exe`

설정이 제대로 들어갔는지 30초 만에 확인할 수 있는 진단 도구가 있습니다.

1. yeson-meet 운영자에게서 **`voicemeeter_dump.exe`** 파일 받기 (350KB)
2. 바탕화면 등 아무 폴더에 놓고 **더블클릭**
3. 검은 콘솔 창이 열리며 옆에 **`vm_dump.json`** 파일이 자동 생성됨
4. 콘솔에 `Edition : Banana`, `Press Enter to close...` 가 보이면 **Enter** 키로 창 닫기
5. 생성된 `vm_dump.json` 파일을 운영자에게 전달

운영자는 그 JSON 한 장으로 라우팅 상태를 즉시 확인합니다. 추후 "소리가 안 잡혀요" 같은 컴플레인이 생기면 동일하게 실행해서 보내주면 원격 진단이 가능합니다.

### 2.7 sidecar 실행 (운영자/스크립트용)

```cmd
SET SERVER_WS_BASE=wss://<server-host>
SET YESON_DEVICE_API_KEY=<plaintext-device-key>
SET YESON_SESSION_ID=<session-uuid>
SET YESON_SIDECAR_MODE=audio
SET YESON_AUDIO_DEVICE_NAME=Voicemeeter Aux Output
uv run python -m apps.client_sidecar.main
```

- `YESON_AUDIO_DEVICE_NAME=Voicemeeter Aux Output` 로 지정하면 B2 가상 라인(상대방 음성 + 내 마이크 풀 믹스)을 캡처합니다.
- admin audio-stats 페이지에서 ≈ 50 chunks/sec 가 유지되면 정상.

### 2.8 자주 발생하는 문제

| 증상 | 확인 사항 |
|---|---|
| 미팅 상대가 내 목소리를 못 들음 | 미팅 앱 마이크가 **"Voicemeeter Output"** 인지 / Strip 1 의 **B1** 이 켜져 있는지 |
| 메아리/하울링이 들림 | Voicemeeter **VAIO** Strip 의 **B1** 을 끄기 (켜져 있으면 발생) |
| 시스템 소리가 안 들림 | VAIO Strip 의 **A1** 이 켜져 있는지 / Windows 기본 재생 장치 = "Voicemeeter Input" 인지 |
| sidecar 가 마이크를 못 잡음 | `voicemeeter_dump.exe` 실행해 결과의 디바이스명과 `YESON_AUDIO_DEVICE_NAME` 일치 여부 확인 |
| Voicemeeter UI 가 안 보임 | 시스템 트레이(우하단) → Voicemeeter 아이콘 우클릭 → **Show** |
