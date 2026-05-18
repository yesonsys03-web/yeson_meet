# SETUP_MEETING_PC — 회의실 PC 셋업

> MVP-α S2 PoC: Intel Mac + BlackHole 우선. Windows + Voicemeeter는 시스템 부원 협조 시점 별도 검증 (별도 섹션 placeholder).

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

## 2. Windows (1순위 검증 — 시스템 부원 협조 단계, placeholder)

> ROADMAP §S2 정식 완료 기준은 Windows 회의실 PC + Voicemeeter Banana. 본 섹션은 시스템 부원이 진행 시 채워짐.

### 2.1 Voicemeeter Banana 설치
- VB-Audio Voicemeeter Banana: https://vb-audio.com/Voicemeeter/banana.htm
- 설치 후 재부팅 필요

### 2.2 캡처 라우팅
- 시스템 사운드 출력 → Voicemeeter Input (VAIO)
- A1 출력은 실제 스피커로 → 회의 소리 들림 동시에 VAIO를 통해 캡처 가능
- sidecar는 `YESON_AUDIO_DEVICE_NAME=Voicemeeter` regex로 자동 인식

### 2.3 Windows-Specific 검증
- (시스템 부원 협조 시 채움)
