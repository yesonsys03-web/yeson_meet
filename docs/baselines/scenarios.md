# Baseline Measurement Scenarios

> 측정 환경: 현재 BlackHole(macOS) 또는 Voicemeeter(Windows) 기반. Phase 1 완료 후 동일 시나리오를 native 캡처로 재측정해 비교한다.

## 공통 사전 조건
- 서버는 `docker compose ... up -d server` 로 정상 가동
- `.env` 확인: `GEMINI_RESPONSE_MODALITY=AUDIO`, `GEMINI_LIVE_MODEL=gemini-3.1-flash-live-preview`
- 클라이언트(dashboard)는 dev 모드(`pnpm --filter @yeson-meet/desktop tauri:dev`)
- 측정 시작 전 `/tmp/yeson-server-<scenario>.log` 로그 캡처 시작:
  `docker compose ... logs -f --since=0s server > /tmp/yeson-server-<scenario>.log 2>&1 &`
- 측정 종료 후 log capture kill, `scripts/baseline_collect.py --log <path> --scenario <name> --out docs/baselines/<date>-<scenario>.json`

## 시나리오 1: Zoom 1:1 EN→KO (5분)
- 화자 1인 (영어 native, 정상 음량)
- 정해진 영어 대본 약 60문장 낭독 (대본은 `docs/baselines/script_en.md` — 추후 작성)
- BlackHole / Multi-Output Device 셋업 확인
- 측정 종료 후 docker logs를 `2026-MM-DD-zoom-1on1.log`로 저장

## 시나리오 2: Teams 3+ mixed (10분)
- 3인 이상 회의, 한국어와 영어 혼재
- 자연 대화 흐름 — 스크립트 없음
- 화자 전환·짧은 silence 자연스럽게 발생해야 함
- 출력 로그: `2026-MM-DD-teams-3plus.log`

## 시나리오 3: YouTube TED EN (10분)
- 정해진 TED talk URL 1개 (예: "How great leaders inspire action" 첫 10분)
- 모노 출력, 음량 60% 고정
- 출력 로그: `2026-MM-DD-youtube-ted.log`

## 시나리오 4: Silent room (5분)
- 발화 없음, 캡처는 활성
- 자막이 false positive로 안 뜨는지 확인
- 출력 로그: `2026-MM-DD-silent.log`

## 측정 후 수집할 지표 (`baseline_collect.py`가 자동 추출)
- `subtitle_first_token_ms` — 첫 발화 → 첫 자막 토큰
- `subtitle_full_p50_ms`, `subtitle_full_p95_ms` — 발화 종료 → final 자막
- `chunks_per_sec_sustained` — 평균
- `audio_queue_drop_count` — 누적
- `gemini_segments_per_minute` — TPM 추정용

## 출력 파일 명명 규칙
- 로그: `/tmp/yeson-server-<scenario>.log` (수집 중) → `docs/baselines/raw/<date>-<scenario>.log` (보존)
- 지표 JSON: `docs/baselines/<date>-<scenario>.json`
