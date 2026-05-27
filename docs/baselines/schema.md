# Baseline Measurement Schema

> 측정 시작 전 반드시 이 파일을 고정해두고 시작한다. Phase 0(Voicemeeter/BlackHole) 측정과 Phase 1(native ScreenCaptureKit/WASAPI) 재측정이 **같은 스키마**를 따라야 비교가 의미를 가진다.
> Phase 0에서 수집 불가한 필드(예: native helper 측 측정값)는 `null`로 채운다.

---

## 1. 파일 규약

- 위치: `docs/baselines/<YYYY-MM-DD>-<scenario>[-native].json`
- 인코딩: UTF-8, 2-space indent, 마지막 줄 LF
- 한 시나리오 1 실행 = 1 JSON 파일
- raw log 보존: `docs/baselines/raw/<YYYY-MM-DD>-<scenario>[-native].log`
- 같은 날 같은 시나리오 재실행은 suffix `-rerun1`, `-rerun2` … 으로 구분

## 2. JSON 스키마 (v1, frozen 2026-05-27)

```jsonc
{
  // ── identity ──────────────────────────────────────────────────────────
  "schema_version": 1,                  // bump on any breaking change
  "scenario": "zoom-1on1",              // 'zoom-1on1' | 'teams-3plus' | 'youtube-ted' | 'silent'
  "recorded_at": "2026-05-27T14:30:00+09:00",
  "duration_seconds": 300,              // 실측된 측정 길이(approx)
  "source_log": "docs/baselines/raw/2026-05-27-zoom-1on1.log",

  // ── environment ───────────────────────────────────────────────────────
  "env": {
    "provider": "sounddevice",          // 'sounddevice' (Phase 0) | 'native' (Phase 1)
    "os": "macOS",                      // 'macOS' | 'Windows'
    "os_version": "14.5",               // e.g. '14.5', 'Windows 11 23H2 build 22631.3737'
    "cpu_arch": "x86_64",               // 'x86_64' | 'arm64'
    "device_model": "MacBookPro16,1",   // best-effort, optional
    "audio_route": "BlackHole 2ch + Multi-Output", // Phase 0: 'BlackHole 2ch + Multi-Output' / 'Voicemeeter Banana' ; Phase 1: 'ScreenCaptureKit system default' / 'WASAPI default loopback'
    "permission_state": "granted",      // 'granted' | 'denied' | 'not_applicable' (sounddevice) | 'not_determined'
    "server_commit": "883e3bc",         // git rev-parse --short HEAD
    "client_commit": "883e3bc",         // 같은 repo면 동일
    "gemini_model": "gemini-3.1-flash-live-preview",
    "gemini_response_modality": "AUDIO" // 'AUDIO' | 'TEXT'
  },

  // ── capture-stage metrics (sidecar → server WSS) ──────────────────────
  // 모두 측정 전체 구간 통계. drop은 누적.
  "capture": {
    "chunks_per_sec_sustained": 49.8,   // 평균(전체 측정 구간)
    "chunks_per_sec_p05": 47.0,         // 안정성 지표 (낮을수록 jitter↑)
    "audio_queue_drop_count": 0,        // sidecar memory queue lossy drop 누적
    "first_chunk_after_speech_ms": 180  // 발화 onset(스크립트 cue 시각) → 서버 첫 청크 수신
                                         // silent 시나리오는 null
  },

  // ── ai-stage metrics (server → Gemini → server) ───────────────────────
  // 'first_subtitle' 류는 'Gemini Live first subtitle yielded' 로그 기준
  "ai": {
    "gemini_connect_to_first_subtitle_ms_first": 9988,   // segment 1
    "gemini_connect_to_first_subtitle_ms_p50": 8200,     // 전체 segment 분포
    "gemini_connect_to_first_subtitle_ms_p95": 10500,
    "gemini_segment_count": 12,
    "gemini_segments_per_minute": 2.4
  },

  // ── delivery-stage metrics (server → viewer) ──────────────────────────
  // client subtitleTiming.ts 가 export 한 JSON 과 페어링
  // 이건 viewer 도착 시점 — 발화 onset 기준이 아니라 server publish 기준
  "delivery": {
    "server_to_viewer_ms_p50": 5.2,     // server publish → viewer arrival
    "server_to_viewer_ms_p95": 82.4,
    "client_timing_artifact": "docs/baselines/2026-05-27-zoom-1on1-client.json" // 없으면 null
  },

  // ── user-facing metric (the only one that matches PRD 비기능 §8) ─────
  // 발화 onset → viewer 화면 첫 자막. 이게 PRD §8 "자막 지연 P50 ≤ 2.0s" 의 측정값.
  // Phase 0: 스크립트 cue 시각 기준으로 계산(YouTube 시나리오는 영상 timestamp).
  // silent: null.
  "user_perceived": {
    "first_speech_to_first_subtitle_ms_first": null,  // 첫 발화만
    "first_speech_to_final_subtitle_ms_p50": null,
    "first_speech_to_final_subtitle_ms_p95": null,
    "measurement_method": "manual_cue"  // 'manual_cue' | 'youtube_timestamp' | 'vad'
  },

  // ── cost (optional, best-effort) ──────────────────────────────────────
  "cost": {
    "input_tokens_total": null,         // Gemini usage metadata 가 없을 수 있음 → null
    "output_tokens_total": null,
    "usd_estimated": null
  },

  // ── empty scenario flag (silent 등) ──────────────────────────────────
  "empty_scenario": false,              // silent 시나리오는 true. capture / ai / user_perceived는 null 허용

  // ── notes (free-form) ─────────────────────────────────────────────────
  "notes": "first 30s had a Bluetooth headset disconnect"
}
```

## 3. 필수 / 선택 필드

| 필드 | Phase 0 필수 | Phase 1 필수 | silent 시나리오 |
|---|---|---|---|
| `schema_version`, `scenario`, `recorded_at`, `env.*` | ✓ | ✓ | ✓ |
| `capture.chunks_per_sec_sustained`, `capture.audio_queue_drop_count` | ✓ | ✓ | ✓ (0이거나 매우 낮을 것) |
| `capture.first_chunk_after_speech_ms` | ✓ | ✓ | `null` |
| `ai.gemini_connect_to_first_subtitle_ms_*`, `ai.gemini_segment_count` | ✓ | ✓ | `null` (subtitle 없음) |
| `delivery.server_to_viewer_ms_*` | ○ | ✓ | `null` |
| `delivery.client_timing_artifact` | ○ | ○ | — |
| `user_perceived.first_speech_to_first_subtitle_ms_first` | ✓ | ✓ | `null` |
| `cost.*` | ○ | ○ | — |
| `empty_scenario` | ✓ (silent만 true) | ✓ | ✓ |

✓ 필수 / ○ best-effort

## 4. 시나리오별 onset 측정 방법

| 시나리오 | onset 기준 | 비고 |
|---|---|---|
| `zoom-1on1` | 대본 첫 단어 발화 시각 (운영자가 스톱워치로 cue) | `measurement_method: "manual_cue"`. cue 시각은 raw log에 `# CUE: speech_onset T=<unix_ms>` 라인으로 수동 삽입 |
| `teams-3plus` | 자연 회의 첫 발화 시각 (스톱워치) | `measurement_method: "manual_cue"`. 정확도 ±500ms 수용 |
| `youtube-ted` | YouTube timestamp 0:00 = 영상 첫 발화 | `measurement_method: "youtube_timestamp"`. 측정 시작과 영상 재생 시작 sync 필수 |
| `silent` | 해당 없음 | `user_perceived.*` = `null` |

## 5. 비교 리포트와의 관계

- `scripts/baseline_compare.py` 는 Phase 0 JSON 과 Phase 1 native JSON 을 입력 받아 **같은 키만** 비교한다. 키 mismatch 시 `—` 로 표기.
- `schema_version` 가 다르면 비교 거부 + 마이그레이션 강요(향후 변경 시).
- 비교 핵심 지표 (delta 표에 반드시 포함):
  1. `user_perceived.first_speech_to_first_subtitle_ms_first` — **the** 사용자 체감 지표
  2. `ai.gemini_connect_to_first_subtitle_ms_p50` — Gemini-쪽 지연 변화 (캡처 경로 교체로 인한 영향만 분리)
  3. `capture.chunks_per_sec_sustained` — 캡처 안정성
  4. `capture.audio_queue_drop_count` — drop 변화
  5. `delivery.server_to_viewer_ms_p50` — fan-out latency 변화 (이건 거의 일정해야 함)

## 6. 변경 이력

| version | 날짜 | 변경 |
|---|---|---|
| 1 | 2026-05-27 | 초기 frozen schema. Phase 0 측정 전 alignment. |
