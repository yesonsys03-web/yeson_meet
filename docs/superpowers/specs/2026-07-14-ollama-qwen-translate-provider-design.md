# Ollama Qwen 번역 provider (자막메이커) — 설계

- 날짜: 2026-07-14
- 대상: `apps/server/domain/video_captions` 번역 스택
- 목표: 자막메이커의 로컬 Qwen 번역을 **실리콘맥 전용(MLX)에서 전 플랫폼(윈도·인텔맥 포함)으로 확장**한다.

## 배경 / 문제

현재 `qwen`/`qwen_lite`/`qwen_hifi` 번역 옵션은 MLX 워커로만 구동된다
(`translate_mlx.py`). MLX는 Apple Silicon GPU 전용 프레임워크라 인텔맥·윈도에서는
설치조차 불가능하고, 클라 드롭다운에서 "보이되 비활성"으로 뜬다
(`qwen_mlx_available = _is_apple_silicon_mac() and mlx_model_installed()`).

윈도(예: RTX 2080)·인텔맥에서도 로컬 Qwen으로 번역하고 싶다.

## 런타임 선택: Ollama

인텔맥/윈도용 로컬 Qwen 런타임 후보(Ollama / llama.cpp 직접 / 사이드카 번들)
중 **Ollama**를 채택한다. 근거:

- 크로스플랫폼 + GPU 자동(Win=CUDA, Mac=Metal, CPU 폴백).
- 앱 번들 무게 0 — 운영자가 별도 설치(claude/codex/opencode provider와 동일 패턴).
- 모델 pull·양자화 태그 관리 내장, HTTP API(`:11434`)로 견고한 JSON 계약.
- torch/llama-cpp-python 번들(무거운 빌드·플랫폼별 휠)을 피한다
  (참고: 로컬 MT 번들은 과거에 무게/품질 이유로 기각된 트랙).

## 모델: Qwen3.5 (Qwen3.6 아님)

Qwen3.6은 최소 크기가 27B(Q4 ~16.8GB)라 RTX 2080(8GB)에 안 올라간다.
Qwen3.5는 4B/9B 소형 변종이 있어 2080에 적합하며, 자막 EN→KO 번역엔
9B로 충분하다. GPU 업그레이드(≥24GB) 후 3.6 전환은 **env 오버라이드**로 대응.

| 버튼 값 | 실리콘(MLX) | 그 외(Ollama) 기본 태그 | env 오버라이드 |
|---|---|---|---|
| `qwen`      | `mlx-community/Qwen3.5-9B-4bit` | `qwen3.5:9b`        | `YESON_OLLAMA_QWEN_MODEL` |
| `qwen_lite` | `mlx-community/Qwen3.5-4B-4bit` | `qwen3.5:4b`        | `YESON_OLLAMA_QWEN_LITE_MODEL` |
| `qwen_hifi` | `mlx-community/Qwen3.5-9B-8bit` | `qwen3.5:9b-q8_0`   | `YESON_OLLAMA_QWEN_HIFI_MODEL` |

## 설계 — 버튼은 3개 그대로, 런타임 자동 선택

버튼(provider 값)을 6개로 늘리지 않는다. 기존 3개를 두고 백엔드가 런타임을 고른다:

- **`available`** = `qwen_mlx_available(mlx_id)` **OR** `qwen_ollama_available(ollama_tag)`.
- **라벨**: "Qwen 9B (MLX 로컬)" → "Qwen 9B (로컬)" — MLX 노출 제거.
- **`create_translator(provider)`** 분기:
  - 실리콘 + MLX 모델 설치됨 → `QwenMlxTranslator` (더 빠름, 우선).
  - 아니면 Ollama 사용가능 → `OllamaTranslator(tag)`.
  - 둘 다 없음 → 설치 안내가 담긴 `TranslationError`.
- **노출 범위**: 전 플랫폼. 실리콘 사용자는 MLX/Ollama 중 설치된 것으로 자동.

### 신규: `translate_ollama.py`

- `QWEN_OLLAMA_MODELS: dict[str,str]` — provider 값 → 기본 태그(위 표), env 오버라이드 적용.
- `ollama_base_url()` — `YESON_OLLAMA_URL` 기본 `http://127.0.0.1:11434`.
- `qwen_ollama_available(tag) -> bool` — `GET /api/tags`(timeout 0.5s)로 서버 생존 +
  해당 모델 pull 여부. 짧은 TTL 캐시로 검증 폭주 방지. httpx는 **함수 내 지연 import**
  (미설치 환경에서 모듈 import 실패 방지; gpu_pack의 requests 지연 import 관례와 동일).
- `OllamaTranslator(model_id)` — `TranslationProvider`.
  - `translate_batch`: `build_translation_prompt`(공유) → `POST /api/generate`
    `{model, prompt, stream:false, format:"json", options:{temperature:0}}` →
    `_extract_json_array`(translate_cli 공유) → `guard_mlx_ko` 환각 가드
    (MLX와 동일 — 불합격 줄은 원문 EN 유지). 블로킹 httpx는 `asyncio.to_thread`로.
  - 개수 불일치/파싱 실패는 `TranslationError` → `_translate_resilient`가 청크 분할 재시도.

### 편집: `translate_cli.py`

- `list_translate_engines()`: qwen 3항목의 `available`를 MLX-OR-Ollama로 합성, 라벨 수정.
  검증 패턴(`video_jobs.py`)은 이 함수에서 자동 도출되므로 추가 작업 없음.
- `create_translator()`: `provider in QWEN_MLX_MODELS` 분기를 런타임 자동선택으로 교체.

### 편집: `translate_mlx.py`

- 모듈 docstring의 "실리콘맥 전용" 문구를 "실리콘=MLX, 그 외=Ollama 폴백"으로 정정.

## 오류 처리

- Ollama 서버 down / 모델 미pull → `available:false`(버튼 비활성). `create_translator`가
  강제 호출되면 "Ollama 서버(:11434)·모델(pull) 확인" 안내 `TranslationError`.
- 번역 중 HTTP 오류/타임아웃 → `TranslationError` → 상위 resilient 폴백(청크 분할 → 원문 유지).
- 환각(외국어/반복/숫자 조작 등) → `guard_mlx_ko` 재사용으로 해당 줄 원문 유지.

## 테스트 (`test_video_translate_ollama.py`, 순수 유닛)

- `qwen_ollama_available`: `/api/tags`에 모델 있음/없음/서버 down(연결오류) → True/False. httpx monkeypatch.
- `OllamaTranslator.translate_batch`: 정상 JSON 배열 반환 / 펜스 감싼 출력 / 가드 불합격 줄 원문 유지 / 개수 불일치 시 TranslationError.
- `create_translator`: 비실리콘 + Ollama 가용 → `OllamaTranslator`; 둘 다 불가 → TranslationError.
- `list_translate_engines`: Ollama만 가용일 때 qwen `available:true`, 라벨에 "MLX" 없음.
- env 오버라이드: `YESON_OLLAMA_QWEN_MODEL`가 태그 치환.

로컬 검증은 conftest(DB) 우회한 standalone 러너 + 최소 venv로, CI는 정식 test 파일로.

## 비목표 (YAGNI)

- 전용 "Qwen3.6" 버튼(하드웨어 부재 — GPU 업그레이드 시 env 또는 2분 후속).
- 앱 내 Ollama 자동 설치/모델 pull UI(운영자 수동, 기존 CLI provider와 동일).
- 라이브(회의) 자막의 Ollama화(이 건은 자막메이커 배치 전용).
