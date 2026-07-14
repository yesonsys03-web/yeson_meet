# 자막메이커 — 로컬 MLX Qwen 번역 엔진 추가 (설계)

- 날짜: 2026-07-14
- 대상: 자막메이커(Video Caption Studio) 번역 파이프라인
- 관련 자산(재사용): 라이브 자막 하이브리드 B MLX Qwen — `apps/server/ai/mlx_worker.py`, `apps/server/ai/mlx_live_translate.py`, 서버 콘솔 `MlxModelPanel.tsx`
- 선행 결정(메모리): "자막메이커 qwen 번역옵션(나중에)" 후속 작업

## 목표

자막메이커 **번역 엔진 드롭다운**에 로컬 MLX Qwen 2종을 추가한다. 실리콘맥에서
네트워크·과금 없이 온디바이스 LLM으로 EN→KO 자막 번역을 수행한다.

- 전사(모델) 계층은 **변경하지 않는다** — whisper(tiny~large-v3) + Apple 온디바이스 유지.
- "세부모델"은 두 Qwen 변형(9B / 4B)을 **같은 번역 드롭다운의 별도 항목**으로 고르는 것을 뜻한다
  (apple / apple_hifi 2항목 패턴과 동일).
- 실리콘맥 전용. 인텔맥·윈도우·구버전 macOS에서는 목록에 **보이되 비활성**(available=false).

## 비목표 (YAGNI)

- Qwen 기반 전사(STT) 추가 — 아님(사용자 확인 2026-07-14).
- 자막메이커 클라(apps/desktop)에서 MLX 모델을 HTTP로 다운로드하는 기능 — 아님.
  MLX 모델 다운로드/관리는 **기존 서버 콘솔 `MlxModelPanel`을 그대로 사용**한다
  (서버=컨트롤 플레인 관례). 미설치 시 드롭다운에서 비활성 + 안내만.
- 라이브 자막(하이브리드 B) 동작 변경 — 아님. 워커/클라이언트 변경은 전부 **additive·하위호환**.

## 모델 카탈로그 (단일 진실)

`serverConfig.ts`의 `MLX_MODELS`와 동일해야 한다(프론트 다운로드 UI와 백엔드 게이팅이 같은 id를 봐야 함):

| provider 값 | MLX model id | 라벨 | RAM |
|---|---|---|---|
| `qwen` | `mlx-community/Qwen3.5-9B-4bit` | Qwen 9B (MLX 로컬) | ~5GB |
| `qwen_lite` | `mlx-community/Qwen3.5-4B-4bit` | Qwen 4B (MLX 로컬·빠름) | ~2.3GB |

백엔드 매핑은 신규 `translate_mlx.py`의 상수 `QWEN_MLX_MODELS: dict[str, str]`에 둔다.
`MLX_MODELS`(TS)와 값이 어긋나면 안 된다 — 스펙에 동기화 주석을 남긴다.

## 구동 방식 (결정: 서브프로세스 워커 배치)

기존 MLX 워커(`mlx_worker.run_worker`) + `MlxWorkerClient`를 재사용한다. 잡 1회 워커
기동 → 모델 로드 → 청크별 배치 요청 → 잡 종료 시 워커 종료. 라이브 자막과 동일 아키텍처
(MLX 격리: 크래시·메모리 서브프로세스 경계, HF_HUB_OFFLINE).

배치 요청은 라이브의 문장별 `{en, context}` 프로토콜과 다르므로, 워커에 **범용 raw-generate
요청 타입**을 additive로 추가한다. QwenMlxTranslator가 `build_translation_prompt`(글로서리 +
의성어 + 간결 자막 지시)를 만들어 raw 프롬프트로 보내고, 모델의 원문 출력(JSON 배열)을 받아 파싱한다.

## 변경 사항 (계층별)

### 1. `apps/server/ai/mlx_worker.py` — raw-generate 요청 추가 (additive)

- `_make_translate()`를 리팩터링해 **(structured_translate, generate_raw)** 두 클로저를 반환.
  둘은 하나의 `load(model_path)` 결과(model/tokenizer/sampler)를 공유한다. 페이크 모드
  (`YESON_MLX_FAKE=1`)는 두 클로저 모두 에코 반환.
  - `structured_translate(en, context) -> ko`: 기존 로직 그대로(라이브 경로 무변경).
  - `generate_raw(prompt) -> text`: `messages=[{"role":"user","content": prompt}]`로
    chat_template 적용(회의용 system 프롬프트 없음), `generate(max_tokens=4096, temp=0)`,
    `</think>` 제거 후 원문 반환.
- `run_worker()` 디스패치: 요청 줄에 `"prompt"` 키가 있으면 `generate_raw` →
  `{"id", "text", "gen_ms"}` emit; 아니면 기존 `"en"` 경로 → `{"id", "ko", "gen_ms"}` emit.
  (요청 하나의 예외가 워커를 죽이지 않는 기존 방어 유지.)

### 2. `apps/server/ai/mlx_live_translate.py` — `MlxWorkerClient` 확장 (additive)

- `__init__(model_id: str | None = None, ...)`: 지정 시 `start()`가
  `YESON_MLX_MODEL_PATH = mlx_model_dir(self._model_id or mlx_model_id())`를 쓴다.
  기존 호출부(라이브)는 인자 미전달 → env 기본값 유지(하위호환).
- `async def generate(self, prompt: str, timeout: float) -> str`: `{"id", "prompt"}` 전송,
  같은 id의 `{"text": ...}` 응답 대기. 기존 `translate()`를 미러(락·EOF·타임아웃 처리 동일).
  기존 `translate()`는 손대지 않는다.

### 3. `apps/server/domain/video_captions/translate_mlx.py` — 신규 (translate_apple.py 미러)

- `QWEN_MLX_MODELS: dict[str, str]` (provider→model id 매핑, 위 표).
- `qwen_mlx_available(model_id) -> bool` = `_is_apple_silicon_mac()`(apple_native)
  `and mlx_model_installed(model_id)`(mlx_live_translate). MLX 번역은 Apple STT 바이너리·
  macOS 26과 무관하므로 `apple_stt_available()`를 쓰지 않는다.
- `class QwenMlxTranslator`(TranslationProvider):
  - `__init__(model_id, *, client_factory=None, timeout=DEFAULT)`; 워커는 지연 기동·유지.
  - `translate_batch(texts)`:
    1. 빈 입력 → `[]`.
    2. 워커 미기동/사망이면 `MlxWorkerClient(model_id=...)` 기동(`start()`).
       기동 실패 → `TranslationError`(→ 상위 `_translate_resilient`가 청크 재분할, 최종
       실패 줄은 원문 유지).
    3. `prompt = build_translation_prompt(texts)`; `raw = await client.generate(prompt, timeout)`.
    4. `out = _extract_json_array(raw, len(texts))`(translate_cli.py 재사용); None이면
       `TranslationError`.
    5. **환각 가드**: 각 `(src, ko)`에 `guard_mlx_ko(src, ko)` 적용, 불합격 줄은 원문(EN)
       유지 + `logger.info("mlx_video_guard_reject ...")`. (검수 단계에서 눈에 띄어 수정 유도.)
    6. 반환.
  - `async def aclose()`: 워커 종료(멱등).
- `_extract_json_array`, `TranslationError`, `build_translation_prompt`는 기존 모듈에서 import
  (중복 구현 금지). `guard_mlx_ko`는 `mlx_live_translate`에서 import.

### 4. `apps/server/domain/video_captions/translate_cli.py` — 목록 + 라우팅

- `list_translate_engines()`: apple_hifi 뒤에 2항목 추가
  (`from .translate_mlx import QWEN_MLX_MODELS, qwen_mlx_available` 지연 import로 인텔/윈도우
  임포트 오염 방지):
  ```
  {"value": "qwen", "label": "Qwen 9B (MLX 로컬)",
   "available": qwen_mlx_available(QWEN_MLX_MODELS["qwen"])},
  {"value": "qwen_lite", "label": "Qwen 4B (MLX 로컬·빠름)",
   "available": qwen_mlx_available(QWEN_MLX_MODELS["qwen_lite"])},
  ```
- `create_translator()`: `if provider in QWEN_MLX_MODELS:` → `QwenMlxTranslator(QWEN_MLX_MODELS[provider])`.

### 5. `apps/server/domain/video_captions/pipeline.py` — 워커 생명주기 정리

- 280–291 블록(앵커 없음)에서 `translate_segments`를 `try/finally`로 감싸,
  `finally`에서 `aclose = getattr(translator, "aclose", None)`가 있으면 `await aclose()`.
  다른 번역기(gemini/CLI/apple)는 `aclose`가 없어 무영향.

### 6. 프론트 (apps/desktop `VideoCaptionPanel.tsx`)

- 코드 변경 없음. 엔진 목록은 서버 `/video-jobs/translate-engines`에서 받아
  `available=false`면 이미 비활성 렌더(기존 apple/apple_hifi와 동일). 라벨로 실리콘맥 전용/
  미설치 여부가 드러난다.
- (선택) 미설치 qwen 항목 hover/subtext에 "서버 콘솔에서 MLX 모델을 먼저 다운로드하세요" 안내.
  기존 EngineOption 렌더가 사유 텍스트를 지원하면 추가, 아니면 라벨에 포함. 범위 최소화를 위해
  MVP에서는 라벨 정도로만.

## 데이터 흐름

```
job(translate_provider="qwen") → pipeline.create_translator()
  → QwenMlxTranslator("mlx-community/Qwen3.5-9B-4bit")
  → translate_segments(chunk=50) 반복 호출 translate_batch(chunk)
      → (최초 1회) MlxWorkerClient(model_id).start() [서버 바이너리 self-reexec, 모델 로드]
      → build_translation_prompt(글로서리+의성어) → client.generate(prompt)
      → _extract_json_array → guard_mlx_ko 필터 → apply_ko_corrections(translate_segments)
  → (잡 종료) finally: translator.aclose() [워커 종료]
```

## 에러 처리 / 폴백

- 모델 미설치·비실리콘: 드롭다운 `available=false`로 애초에 선택 불가. 서버가 그래도 받으면
  `QwenMlxTranslator` 기동 실패 → `TranslationError` → `_translate_resilient`가 원문 유지로 폴백
  (잡 전체는 죽지 않음). 상위 pipeline은 기존 실패 처리 경로.
- 워커 배치 출력 파싱 실패/개수 불일치: `_extract_json_array` None → `TranslationError` →
  `_translate_resilient`가 청크 반으로 재분할 재시도 → 1줄까지 실패하면 그 줄 원문 유지.
- 워커 배치 타임아웃/사망: `MlxWorkerUnavailable`/`asyncio.TimeoutError` → `TranslationError`로
  변환해 동일 재분할·원문유지 경로. 워커는 aclose로 정리.
- 환각(외국문자/반복/숫자날조/길이비/영어누출): `guard_mlx_ko` 불합격 줄만 원문(EN) 유지.

## 테스트 (기존 서버 테스트 관례; mlx 미설치 CI/인텔맥에서도 통과해야 함)

- `mlx_worker` raw-generate: `YESON_MLX_FAKE=1`로 `{"prompt": ...}` → `{"text": ...}` 에코 확인,
  기존 `{"en": ...}` 경로 회귀 없음.
- `MlxWorkerClient.generate`: 페이크 워커(argv 주입)로 왕복·타임아웃·EOF 처리. `model_id` 지정 시
  `YESON_MLX_MODEL_PATH` env 반영 확인.
- `translate_mlx.QwenMlxTranslator.translate_batch`: `client_factory` 주입(가짜 클라이언트)으로
  정상 배열/파싱실패→TranslationError/가드 리젝트→원문유지/빈입력. mlx 실모델 로드 없음.
- `qwen_mlx_available`: `_is_apple_silicon_mac`·`mlx_model_installed` monkeypatch 조합 진리표.
- `list_translate_engines`: qwen 2항목 존재 + available 게이팅(설치/플랫폼 monkeypatch).
- `create_translator("qwen")` → `QwenMlxTranslator` 인스턴스 + model_id 매핑.
- `pipeline` translate finally: `aclose` 있는 translator는 호출, 없는 것은 무영향(기존 테스트 회귀).

## 수동 검증 (실리콘맥 실기기)

1. 서버 콘솔 `MlxModelPanel`에서 Qwen 9B 다운로드.
2. 자막메이커 클라에서 번역 엔진 "Qwen 9B (MLX 로컬)" 선택 가능(활성) 확인, 4B는 미설치 시 비활성.
3. 짧은 영상 전사(base) + Qwen 9B 번역 완주 → 자막 품질/의성어/용어 확인.
4. 인텔맥/윈도우 빌드에서 두 항목 **비활성**으로만 보이고 서버 임포트 오염 없음 확인.

## 리스크 / 메모

- 배치 50줄 단일 generate가 JSON을 흐트러뜨리면 재분할 비용 발생 — 필요 시 MLX 전용 chunk_size
  축소(예: 20)를 후속 튜닝으로. MVP는 50 유지.
- `QWEN_MLX_MODELS`(py) ↔ `MLX_MODELS`(ts) 동기화 필요(두 곳). 주석으로 상호 참조.
- 첫 배치 콜드 로드(최대 ~120s, 5GB) 동안 전사→번역 전환 진행률이 멈춰 보일 수 있음 — 로그로만
  관측, UX는 기존 진행바 유지(허용).
```
