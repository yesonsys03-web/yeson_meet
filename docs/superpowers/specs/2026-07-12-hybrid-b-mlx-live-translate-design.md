# 하이브리드 B 설계 — 라이브 자막: 전사 Apple + 번역 MLX 로컬 LLM (2026-07-12)

## 배경과 목표

실회의 3회 평가에서 apple_live_translate(Apple Translation) 품질 열세가 확정됐고, 같은 날
MLX 벤치마크(`2026-07-12-mlx-live-translate-spike.md`)에서 **Qwen3.5-9B-4bit가 블라인드
4파전 압승**(54승/140문장, 치명 결함 1건)을 실증했다. 이 설계는 라이브 자막 파이프라인에서
**전사는 기존 Apple SpeechTranscriber 경로를 유지하고, 파이널 문장의 번역만 MLX 로컬
LLM으로 교체**하는 하이브리드 B를 정의한다.

- **목표**: 완전 오프라인(회의 중 네트워크 0), 정확도 최우선, 자막 중단 없음.
- **비목표**: Gemini Live Translate 대체(온라인 최고 품질 경로는 그대로 유지), 파셜 재번역,
  전사(STT) 개선, 인텔맥 지원.

## 확정된 결정 (사용자)

| 결정 | 선택 |
|---|---|
| 파셜 자막 | Apple MT 유지 (Swift 무변경, 현행 화면 경험 동일) |
| 파이널 확정 방식 | **홀드**: MLX 완료 후 1회만 확정 표시 (확정 자막 불변 — 소급 교체 금지) |
| 환각 가드 불합격 시 | Apple KO 폴백, 재시도 없음 |
| 백로그 정책 | 큐 대기 3문장 초과 시 가장 오래된 문장은 MLX 생략, Apple KO 즉시 확정 |
| 모델 준비 | 서버 콘솔 다운로드 버튼 (회의 전 설치, 진행률 표시) |
| 모델 | 기본 **Qwen3.5-9B-4bit**, 콘솔에서 Qwen3.5-4B-4bit 선택 가능 |
| 아키텍처 | 데코레이터 프로바이더 + MLX 워커 서브프로세스 (A안) |

## 아키텍처

```
[yeson-sidecar] → 오디오 → [yeson-server]
  AudioLiveSession (기존, 무변경)
    └ MlxRefinedAppleProvider (신규 데코레이터, STTProvider 구현)
        ├ AppleLiveTranslateProvider (기존, 무변경)
        │    └ apple-live-translate live  (Swift 바이너리, 무변경)
        └ MLX 워커 프로세스 (신규)
             · 실행: yeson-server --mlx-translate-worker  (패키징 바이너리 자기 재실행)
             · 모델: mlx-lm + YESON_MLX_MODEL (기본 mlx-community/Qwen3.5-9B-4bit)
             · 프로토콜: JSONL stdin/stdout
               요청  {id, en, context: [[en, ko], ...], glossary: {...}}
               응답  {id, ko, gen_ms}
               상태  {type: "status", state: "ready" | "error", reason?}
             · env: HF_HUB_OFFLINE=1 (회의 중 네트워크 원천 차단)
```

- 신규 파일: `apps/server/ai/mlx_live_translate.py`(데코레이터 + 가드 + 워커 클라이언트),
  `apps/server/ai/mlx_worker.py`(워커 루프; mlx-lm은 이 진입점에서만 지연 import).
- 프로바이더 등록: `create_ai_provider()`에 `apple_mlx_live_translate` 분기 추가. 게이팅 =
  기존 apple 게이팅(macOS 26+, Apple Silicon, apple 바이너리 존재) AND 선택 모델 설치됨.
  미충족 시 기동에서 `missing_mlx_model` 등 영구 에러(기존 "provider unavailable" 시그니처
  경로 재사용 → 5분 백오프 + 운영자 알림).
- seq/provider_segment는 데코레이터가 변경하지 않는다 — AISequenceNormalizer·재접속
  로직 현행 유지. 발행 경로의 (session_id, seq) 업서트도 그대로 쓴다.

## 데이터 흐름

- **partial**: 데코레이터 통과 (Apple KO). 홀드 중인 파이널과 무관하게 계속 흐른다
  (seq가 달라 화면 행이 분리됨).
- **final**: 워커 큐에 투입 후 홀드.
  - 문맥: 직전 3개 **확정 발행된** 파이널의 (EN, 확정 KO) 쌍 — 자기일관 유지.
  - MLX 응답 + 가드 통과 → MLX KO로 확정 발행 (이후 불변).
  - 가드 불합격 / 문장 타임아웃 **6초** / 워커 부재 → Apple KO로 확정 발행.
  - 홀드 중(처리 중 포함) 문장 수 > **3** → 가장 오래된 것부터 MLX 생략, Apple KO 즉시 확정.
- 용어집 교정 `apply_ko_corrections`는 기존에 Apple 프로바이더 내부에서 적용되므로,
  데코레이터가 **MLX KO에도 동일하게 적용**해 두 경로의 최종 처리를 일치시킨다.
  이후 발행 처리(마크업 스트립, 업서트, 버스 발행)는 기존 경로 그대로.

## 환각 가드 (전부 정규식/문자열 연산)

하나라도 걸리면 Apple KO 폴백 + `mlx_guard_reject reason=<rule>` 로그(튜닝 데이터).

| 규칙 | 기준 | 겨냥한 실측 실패 |
|---|---|---|
| 외래 문자 | KO에 한자·가나·키릴·태국 문자·U+FFFD 포함 | "코다克斯", "ましょう", "코드КС" |
| 숫자 날조 | KO의 숫자 토큰이 EN 숫자 토큰 집합에 없음 (EN→KO 누락은 허용 — 숫자의 한글 표기 가능) | "53만 달러" 환각 |
| 길이 이상 | KO 비었거나 len(KO)/len(EN) ∉ [0.2, 3.0] | 내용 소실/설명 폭주 |
| 영어 잔존 | KO의 ASCII 알파벳 비율 > 60% | 미번역 통과 (부분 잔존 "landing page"는 허용) |
| 반복 붕괴 | 10자 이상 동일 구절 3회 이상 반복 | "분류하고 분류하여…" |

잔여 리스크: 자연스러운 의미 반전 환각(예: computer→클라우드)은 기계 가드로 못 잡는다.
Qwen3.5-9B 실측 치명 결함 140문장 중 1건이 이 리스크의 현재 수치.

## 에러 처리 & 수명주기

- **기동**: 세션 시작 시 워커 스폰, `status:ready` 대기 (ready 타임아웃 **120s** — 5GB
  페이지-인 감안; apple 바이너리 ready 가드와 동일 패턴). 실패 시 영구 에러 알림 후
  **Apple KO 전용 모드로 세션 계속** (자막 중단 없음).
- **크래시(회의 중)**: 홀드 중 문장 즉시 Apple KO 확정 → 백오프 재스폰 최대 2회 →
  계속 실패 시 잔여 회의 Apple KO 전용. 데코레이터는 예외를 상위로 던지지 않는다 —
  Apple 스트림(전사·파셜)은 워커와 독립적으로 유지.
- **종료**: 세션 stop 시 워커 kill (메모리 즉시 회수).
- **메모리 안전판**: 스폰 직전 가용 RAM < 모델 크기+2GB면 경고 로그 + 운영자 상태 표시
  (기동은 진행).

## 서버 콘솔 (server_desktop)

- 엔진 선택지 추가: "Apple 전사 + 로컬 LLM 번역 (실험적)" → `YESON_AI_PROVIDER=apple_mlx_live_translate`.
- `server_config.rs`에 `yeson_mlx_model` 필드 추가(키체인 저장, spawn 시 env 주입 — 기존
  패턴 그대로). 엔진 변경 후 서버 재시작 필요 표시 유지.
- 모델 관리 섹션(prepare-translation 버튼 패턴): 9B(기본)·4B 각각 다운로드 버튼 + 진행률 +
  설치 배지, 기본 모델 선택. 다운로드는 `yeson-server --mlx-download <model>` 서브커맨드가
  진행률 JSONL 출력 → 콘솔 표시. 저장 위치 `storage/mlx_models/` (앱 소유 경로).

## 패키징

- `mlx`·`mlx-lm`은 arm 빌드에만 포함(PyInstaller 스펙 분기 — apple 리소스 글롭 분리와
  동일 방침, 커밋 510741b 참조). 서버 본체는 mlx 미존재 환경에서도 임포트 가능해야 한다
  (워커/다운로드 진입점에서만 지연 import) — 인텔 빌드 회귀 방지.
- 모델은 번들에 포함하지 않는다(런타임 다운로드). 번들 증가분은 mlx 휠 수준.

## 테스트

- **유닛(모델 불필요)**: 가드 규칙(벤치 실측 실패 문장 픽스처 + 통과 케이스), 백로그 스킵,
  문장 타임아웃, 워커 사망 폴백·스트림 무중단, 홀드 중 파셜 통과 순서.
- **워커 페이크 모드**: `YESON_MLX_FAKE=1` → 모델 로드 없이 에코 응답. 프로토콜(ready,
  요청/응답 매칭, 크래시 시나리오)을 CI에서 검증.
- **실모델 스모크(수동)**: 9B로 10문장 왕복 + ready 시간 실측.
- **콘솔**: 다운로드 진행률 파싱, config round-trip.
- 실회의 품질 판정은 기존 관례대로 사용자 평가.

## 근거 데이터

- 벤치·심사 상세: `docs/superpowers/specs/2026-07-12-mlx-live-translate-spike.md`
- 원자료: `.omc/research/mlx-live-translate-bench-2026-07-12/`
