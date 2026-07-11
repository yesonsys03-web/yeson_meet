# Apple 온디바이스 전사·번역 (`apple_live_translate`) 설계

- 날짜: 2026-07-11
- 상태: 사용자 검토 대기
- 적용 범위: (1) 라이브 미팅 자막 프로바이더, (2) 자막메이커 전사/번역 엔진

## 1. 배경과 목표

실리콘맥에서 Apple 온디바이스 스택(STT + 기계번역)의 성능이 매우 빠름을 확인했다
(특히 번역 배치 속도가 압도적 — 로컬 NMT라 네트워크 왕복이 없음). 이를 기존
프로바이더 체계에 **선택 가능한 옵션으로 추가**한다. 기본값은 현행
`gemini_live_translate` 유지.

사용할 프레임워크 ("Apple Intelligence"라는 이름과 달리 Foundation Models가 아님):

| 역할 | 프레임워크 | 최소 OS |
|---|---|---|
| STT (라이브/파일) | SpeechAnalyzer / SpeechTranscriber | macOS 26 (Tahoe) |
| 기계번역 EN→KO | Translation framework (`TranslationSession`) | macOS 15 |

Foundation Models(온디바이스 LLM)는 스트리밍 지연·컨텍스트 한계로 부적합하여
사용하지 않는다.

전제 조건(가용성 게이팅): Apple Silicon + 해당 macOS 버전 + 언어 에셋 다운로드.
서버 프로세스(server_desktop)가 실리콘맥에서 돌 때만 동작 — "서버가 맥" 시나리오와
"올인원 맥" 시나리오를 동일 코드 경로로 커버한다. 회의실 캡처 PC 측 변경은 없다.

## 2. 전체 구조

Swift 실행 파일 1개(서브커맨드 3개)를 `native_helper_mac` SwiftPM에 새 타깃으로
추가하고, Python이 subprocess로 사용한다.

```
apple-live-translate (Swift, SwiftPM 새 실행 타깃)
├─ live             : stdin 16kHz mono PCM → stdout JSONL partial/final/status
├─ transcribe-file  : --input audio.wav → stdout JSONL 세그먼트(audioTimeRange 포함)
└─ translate-batch  : stdin JSON 배열(EN) → stdout JSON 배열(KO)
```

JSONL 이벤트 (live):

```json
{"type":"status","state":"ready"}
{"type":"status","state":"error","reason":"unsupported_os|missing_stt_asset|missing_mt_asset"}
{"type":"partial","seq":3,"en":"...","ko":"..."}
{"type":"final","seq":3,"en":"...","ko":"...","t0":12.34,"t1":15.10}
```

## 3. 라이브 미팅 프로바이더

### 3.1 Swift `live` 서브커맨드

- SpeechTranscriber volatile(파셜) + finalized 결과 스트리밍.
- 파셜 정책: volatile 결과를 **~500ms 스로틀**로 번역해 `partial` 방출,
  finalized 결과는 즉시 번역해 `final` 방출. Gemini 프로바이더와 동일한
  partial/final 계약.
- `TranslationSession`은 SwiftUI `.translationTask`에 묶여 있어 숨김 오프스크린
  윈도우(NSApplication + hidden NSHostingView)로 우회한다. **구현 리스크 1순위 —
  스파이크로 선검증** (막히면 설계 재고).
- 시작 시 자가 점검(OS 버전/실리콘/STT·번역 에셋) 실패 시 `status:error` 방출 후
  종료.

### 3.2 Python 어댑터 (신규 `apps/server/ai/apple_live_translate.py`)

- `STTProvider` 프로토콜 구현: subprocess 스폰 → stdin PCM 펌핑 → stdout JSONL
  파싱 → `TranslatedUtterance`(seq, provider_segment, is_final) 방출.
- 바이너리 크래시 → 예외 전파 → 기존 `live_session` reconnect 루프가 재시작
  (`provider_segment` 증가로 AISequenceNormalizer가 seq 재정렬).
- `status:error`는 **영구 에러**로 분류해 기존 `_publish_provider_error` 경로로
  운영자에게 표출 (5분 백오프 재시도 낭비 방지).
- `glossary.apply_ko_corrections` 후보정 적용 (Gemini와 동일 접근 — Apple 번역은
  프롬프트 주입 불가이므로 후보정이 유일한 용어 교정 수단).

### 3.3 배선

- `create_ai_provider()`(`apps/server/ws/sidecar.py`)에 `"apple_live_translate"`
  매핑 추가. 바이너리 경로는 env `YESON_APPLE_TRANSLATE_BIN`.
- server_desktop: 프로바이더 셀렉터에 옵션 추가(macOS arm64에서만 표시), Swift
  바이너리를 Tauri 리소스로 번들하고 경로 env 주입.
- 미지원 환경(리눅스/인텔맥/Windows)에서 env로 강제 지정 시 명확한
  `status:error`로 실패.

## 4. 자막메이커 적용

### 4.1 전사 엔진 옵션 (`transcribe-file`)

- `transcribe.py`에 엔진 분기: 기존 faster-whisper 유지 + `apple` 선택 시
  서브커맨드 호출. SpeechTranscriber finalized 결과의 `audioTimeRange`를 기존
  `words_to_cues`(6초/90자 큐 분할)에 그대로 물린다.
- 진행률 콜백·`StaleRunCancelled` 취소 계약 유지.
- 이점: whisper 모델 다운로드(수 GB)·GPU 팩 불필요, 전사 속도 대폭 향상.

### 4.2 번역 엔진 옵션 (`translate-batch`)

- `translate.py`의 `TranslationProvider` plug point(도크스트링에 명시된 자리)에
  `AppleTranslator.translate_batch` 구현 추가 — Translation framework의 배치
  API(`translate(batch:)`) 사용.
- `list_translate_engines()`에 등록 → 기존 엔진 픽커(gemini/claude/codex/…)에
  "Apple 온디바이스"로 노출, macOS+실리콘에서만.
- 게이팅 주의: 번역 엔진은 STT가 불필요하므로 **macOS 15+** 면 동작. 전사
  엔진(4.1)과 라이브 프로바이더(§3)는 SpeechTranscriber 요구로 **macOS 26+**.
  가용성 체크는 기능별로 분리한다.
- 글로서리는 후보정(`apply_ko_corrections`)만 적용. 전문용어 정확도가 중요하면
  운영자가 픽커에서 Gemini 선택.

### 4.3 조합 자유

전사 엔진과 번역 엔진은 독립 선택 — 예: "전사 Apple(빠름) + 번역 Gemini(용어
정확)" 조합 가능.

## 5. 에러·엣지 케이스

- 언어 에셋 미다운로드: 운영자에게 "시스템 설정에서 언어 다운로드" 안내 status.
  프로그램적 다운로드 유도는 v2로 미룸 (YAGNI).
- 번역 품질이 Gemini 대비 떨어지는 도메인: 글로서리 후보정 + 엔진 전환으로 흡수.
- 자막메이커 배치 중 바이너리 크래시: 해당 배치 재시도 후 실패 시 기존
  `TranslationError` 경로.

## 6. 테스트

- **Swift**: 트랜스크립트 어셈블/스로틀 로직 단위 테스트 (기존 `Tests/` 체계).
- **Python 라이브**: 가짜 subprocess(JSONL 방출 스크립트)로 partial/final/크래시/
  영구에러 시나리오 — `test_gemini_live_translate.py` 패턴 재사용.
- **Python 자막메이커**: `AppleTranslator`·apple 전사 분기를 가짜 subprocess로
  테스트 — 기존 translate/transcribe 테스트 패턴 재사용.
- **실기 검증**: 실리콘맥에서 synthetic 오디오 레이턴시 실측(기존 baseline
  스크립트, Gemini 실측 P50 1.4s와 비교) + 자막메이커 전사/번역 소요시간 비교.

## 7. 구현 순서 (리스크 우선)

1. **스파이크**: 최소 Swift CLI로 SpeechTranscriber + 헤드리스 TranslationSession
   검증 (`live`·`transcribe-file`·`translate-batch` 셋 다 같은 프레임워크라 함께
   검증) ← 막히면 설계 재고
2. Swift 바이너리 본 구현 + 테스트
3. Python 라이브 어댑터 + 배선 + 테스트
4. Python 자막메이커 엔진(전사/번역) + 테스트
5. server_desktop UI/번들링
6. 실기 성능 실측
