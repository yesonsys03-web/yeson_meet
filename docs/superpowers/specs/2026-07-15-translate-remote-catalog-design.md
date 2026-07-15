# 로컬 번역 모델 원격 카탈로그 — 설계

- 날짜: 2026-07-15
- 상태: 승인됨 (구현 계획 대기)
- 관련: v1.3.8 whisper 전사 모델 원격 카탈로그 (`remote_catalog.py`, `whisper_catalog.remote.json`)

## 배경과 목표

v1.3.8에서 whisper **전사** 모델은 원격 카탈로그(리포 main의 JSON)를 통해 **앱 재배포 없이** 새 모델을 추가할 수 있게 됐다. 로컬 **번역** 모델(Qwen 티어)에는 같은 장치가 없어, 티어 하나를 추가하려면 앱을 다시 배포해야 한다.

목표: 번역 티어도 원격 카탈로그로 추가·오버라이드할 수 있게 하고, 그 과정에서 티어 정의가 흩어져 있는 하드코딩 5곳을 단일 소스로 모은다.

전사 모델과 달리 번역 티어는 **런타임이 둘**(실리콘맥 = MLX, 윈도우·인텔맥 = Ollama)이라 정체성이 이중이다. `repo_id` 하나로 표현되지 않는 것이 이 작업의 핵심 난점이다.

## 현재 구조 (조사 결과)

번역 티어 `qwen` / `qwen_lite` / `qwen_hifi`가 정의된 곳:

| 위치 | 내용 |
|---|---|
| `translate_mlx.py:25` `QWEN_MLX_MODELS` | 티어 → MLX 리포 id |
| `translate_ollama.py:33` `_QWEN_OLLAMA_DEFAULTS` | 티어 → (Ollama 태그, env 오버라이드 키) |
| `translate_cli.py:107` `list_translate_engines` | 번역 드롭다운용 정적 리스트 |
| `translate_models.py:38` `_TIERS` | 다운로드 관리(런타임별 크기) |
| `translate_cli.py:289` `create_translator` | `provider in QWEN_MLX_MODELS` 멤버십 디스패치 |

**별개 소비자**: `serverConfig.ts:88` `MLX_MODELS` + `MlxModelPanel.tsx`. 자막메이커가 아니라 **라이브 자막(회의) 번역**(`apple_mlx_live_translate` provider, 실리콘맥 전용)용이며, 서버 API가 아닌 Tauri `invoke("mlx_model_status")` 기반이다. 우연히 같은 Qwen 모델 3종을 가리킬 뿐이다.

이미 유리한 조건:

- `create_translator`가 이미 **멤버십 디스패치**라, 카탈로그를 그 조회의 소스로 만들면 새 티어의 라우팅이 자동으로 따라온다.
- `video_jobs`의 provider 검증은 v1.3.6 핫픽스에서 `list_translate_engines` 파생으로 바뀌었다 → **새 티어가 자동으로 검증을 통과한다. 이 파일은 손대지 않는다.**
- `server_desktop`은 이미 `videoJobsAdmin.ts`·`reportsAdmin.ts` 등에서 `http://127.0.0.1:<port>` 루프백 REST를 호출하는 관례가 있다 → 라이브 패널의 배관은 새 발명이 아니다.

## 결정 사항

| # | 결정 | 근거 |
|---|---|---|
| 1 | **범위 = 자막메이커 + 라이브 자막 양쪽** | 새 모델 하나 추가로 양쪽에 반영, 하드코딩 동기화 부담 제거 |
| 2 | 라이브 패널은 **목록만 HTTP + 빌트인 폴백** | `MlxModelPanel`은 서버 미기동인 초기 설정 단계에서도 동작해야 함. 상태 확인·다운로드는 Tauri 유지 |
| 3 | 런타임 양쪽 **optional** (최소 한쪽 필수) | MLX 전용 양자화·Ollama 전용 태그가 현실적으로 흔함. 양쪽 필수로 하면 원격 추가가 과도하게 좁아짐 |
| 4 | 지원 안 되는 런타임의 티어는 **"보이되 비활성 + 사유 라벨"** | `video_models.py:37-39`에 기록된 결정("플랫폼별로 항목 자체가 사라지던 비대칭을 없앤다")과 `list_translate_engines`의 `available` 관례를 따름. 목록 제외는 같은 화면에 두 규칙을 섞게 됨 |
| 5 | **공통 코어 추출 + 얇은 어댑터 둘** | 중복되는 것이 네트워크·캐시 로직이라 복제 비용이 실질적. whisper 쪽 변경은 순수 리팩터링이라 기존 테스트가 안전망 |
| 6 | 원격 JSON은 **별도 파일** | 단일 파일 2섹션은 배포된 v1.3.8이 읽는 `whisper_catalog.remote.json` 포맷을 깨거나 이중 유지를 강요 |

### 검토했으나 채택하지 않은 것

- **`translate_remote_catalog.py` 신규(whisper 모듈 복제)**: whisper를 안 건드려 가장 안전하지만 fetch/캐시/TTL/https 가드 ~50줄이 두 벌이 된다. 캐시 버그·TTL 정책 변경 시 두 곳을 고쳐야 한다. (`MlxModelPanel`이 스타일 객체를 복제한 전례가 있으나, 그건 스타일이지 네트워크·캐시 로직이 아니다.)
- **단일 원격 JSON에 `whisper`/`translate` 두 섹션**: 네트워크 왕복 절감 대비 구버전 앱 호환 비용이 크다.
- **Rust가 캐시 파일을 직접 파싱**: 서버 없이 원격 모델까지 보이지만 캐시 포맷이 파이썬·Rust 두 곳에 결합된다.

## 아키텍처

### 신규 `catalog_fetch.py` — 제네릭 코어

`remote_catalog.py`에서 fetch·TTL·https 가드·캐시 읽기/쓰기·폴백을 이관한다. 스키마를 모르도록 파서를 주입받는다.

```python
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
CACHE_TTL_SECONDS = 6 * 3600

def get_entries(url_fn, cache_path_fn, parse_fn, force=False) -> list
def cached_entries(cache_path_fn, parse_fn) -> list   # 네트워크 없음
def _http_get(url)   # test seam
def _now()           # test seam
```

`cache_path_fn`을 값이 아니라 **함수**로 받는다. 캐시 경로가 `whisper_models.models_root()`에 의존해 현재 `remote_catalog._cache_path()`가 지역 import로 순환을 피하고 있는데, 호출을 늦추면 그 우회가 불필요해진다.

캐시 페이로드 포맷은 유지한다: `{"fetched_at": float, "models": [...]}`. `parse_fn`은 페이로드 dict 전체를 받아 목록을 반환하며, 원격 응답과 캐시 양쪽에 같은 파서가 쓰인다(현행 whisper 동작 그대로).

### `remote_catalog.py` (whisper) — 공개 API 불변

`RemoteModel`·`_parse`·`cached_models()`·`get_remote_models(force)` 시그니처와 동작 유지, 내부만 코어에 위임. 캐시 파일명 `remote_catalog.cache.json`도 유지 → 기존 사용자 캐시가 무효화되지 않는다.

### 신규 `translate_catalog.py` — 번역 티어 단일 진실

```python
@dataclass(frozen=True)
class TranslateModel:
    name: str
    label: str
    mlx_repo: str | None
    mlx_bytes: int
    ollama_tag: str | None
    ollama_bytes: int

BUILTIN: dict[str, TranslateModel]   # 현행 qwen / qwen_lite / qwen_hifi 3종, 값 그대로

def get_catalog() -> dict[str, TranslateModel]   # BUILTIN + 원격(디스크 캐시) 오버레이
def get_remote_models(force=False)               # 네트워크 — API만 호출
def ollama_env_key(name) -> str                  # YESON_OLLAMA_{NAME.upper()}_MODEL
```

**검증 규칙** — whisper와 동일(이름 정규식, `.`/`..` 거부, 양수 정수 크기, 문자열 라벨)하되 한 줄 추가: **`mlx_repo`와 `ollama_tag` 중 최소 하나는 있어야 유효**. 있는 쪽의 `*_bytes`는 양수 정수여야 하고, 없는 쪽의 `*_bytes`는 검증하지 않고 **`0`으로 정규화**한다(진행률 계산이 `approx`가 0이면 `None`을 반환하도록 이미 처리되어 있다).

**원격은 추가·오버라이드만 가능하고 빌트인 삭제는 불가** — 원격 카탈로그가 깨져도 기본 3종은 항상 남는다.

**캐시 경로**: `{STORAGE_ROOT}/translate_catalog.cache.json` — whisper처럼 모델 디렉터리 밑이 아니라 **STORAGE_ROOT 직하**에 둔다. 번역엔 대응하는 단일 모델 디렉터리가 없다(MLX는 `mlx_models/`, Ollama는 Ollama 자체 저장소). `mlx_models/` 밑에 두면 MLX가 존재하지 않는 윈도우 서버에 `mlx_models` 디렉터리가 생긴다(`_write_cache`가 `parents=True`로 mkdir하므로 조용히 성공한다). `STORAGE_ROOT`는 Tauri가 전 플랫폼에 주입한다(`server_process.rs:287`, `<app_data_dir>/storage`).

**`None` 런타임 가드**: `mlx_repo` / `ollama_tag`가 `None`일 수 있으므로 호출 전 반드시 확인한다.

- `qwen_mlx_available(entry.mlx_repo)` — `mlx_repo`가 `None`이면 `mlx_model_installed`가 `None.replace()`로 터진다. **실리콘맥에서만** 터진다(윈도우·인텔맥은 `_is_apple_silicon_mac()`에서 단락되어 도달하지 않음).
- `qwen_ollama_model(name)`은 `ollama_tag`가 없으면 `None`을 반환하고, `qwen_ollama_available(None)`은 `bool(None)`으로 안전하게 `False`다(가드 불필요).

**env 키 규칙**: 기존 세 키(`YESON_OLLAMA_QWEN_MODEL` / `YESON_OLLAMA_QWEN_LITE_MODEL` / `YESON_OLLAMA_QWEN_HIFI_MODEL`)가 `YESON_OLLAMA_{name.upper()}_MODEL` 규칙과 정확히 일치하므로, 규칙 파생으로 하위호환이 그대로 유지된다.

### 신규 `translate_catalog.remote.json`

리포 main에 위치. 초기 내용 `{"version": 1, "models": []}`.

```json
{
  "version": 1,
  "models": [
    {
      "name": "qwen_next",
      "label": "Qwen 12B (로컬)",
      "mlx_repo": "mlx-community/Qwen3.6-12B-4bit",
      "mlx_bytes": 7000000000,
      "ollama_tag": "qwen3.6:12b",
      "ollama_bytes": 9000000000
    }
  ]
}
```

URL 기본값은 whisper와 같은 관례(`raw.githubusercontent.com/.../main/apps/server/domain/video_captions/translate_catalog.remote.json`), env 오버라이드 키는 `YESON_TRANSLATE_CATALOG_URL`.

## 소비자 파생

| 위치 | 변경 |
|---|---|
| `translate_mlx.QWEN_MLX_MODELS` | 상수 삭제. 호출부가 `get_catalog()[name].mlx_repo` 조회. `qwen_mlx_available(model_id)`는 리포 id를 받으므로 그대로. `serverConfig.ts와 동기화` 주석 삭제 — 더 이상 수동 동기화가 아님 |
| `translate_ollama._QWEN_OLLAMA_DEFAULTS` | 상수 삭제. `qwen_ollama_model(provider)` 시그니처 유지, 내부만 `env 오버라이드 → 카탈로그 태그` 순 조회. `ollama_tag`가 없으면 `None` |
| `translate_cli.list_translate_engines` | qwen 3줄 삭제, 카탈로그 루프로 생성. 정적 엔진(gemini/claude/codex/agy/opencode/apple/apple_hifi)은 그대로 |
| `translate_models._TIERS` / `_TIER_BY_NAME` | 삭제, `get_catalog()` 사용 |
| `translate_cli.create_translator` | `if provider in QWEN_MLX_MODELS:` → `entry = get_catalog().get(provider)` / `if entry is not None:`. 이후 `entry.mlx_repo` / `entry.ollama_tag` 사용 |

`translate_mlx.py`는 `ANCHOR: TRANSLATE_MLX_START` 경계 안에서만 편집한다.

**`available`과 `reason`의 의미를 분리한다** — 둘을 헷갈리면 "설치만 하면 되는 모델"과 "이 기기에선 영원히 못 쓰는 모델"이 같은 회색으로 보인다.

- `available` — **지금 실제로 선택 가능한가**. 현행 `_qwen_available`의 의미를 그대로 유지한다(MLX 설치됨 **또는** Ollama 설치됨). 미설치 티어는 `available: false`이며, 이건 정상 상태다(다운로드하면 켜진다).
- `reason: str | None` — **이 서버에서 지원 런타임 자체가 없을 때만** 채운다. 실리콘인데 `mlx_repo`가 없으면 `"Ollama 전용"`, 실리콘이 아닌데 `ollama_tag`가 없으면 `"실리콘맥 전용"`. 그 외에는 항상 `None`.

즉 **`reason`이 있는 항목만 "다운로드해도 소용없는 항목"**이고, 클라는 이때만 다운로드 버튼을 disable한다. `reason: None` + `available: false`는 그냥 미설치이므로 다운로드 버튼이 살아 있어야 한다. `list_translate_engines`(드롭다운)와 `list_models`(모델 관리 탭)가 같은 값을 쓴다.

`list_models`의 `downloadable`은 현행 `rt == "mlx" or ollama_run`에 "이 티어가 현재 런타임을 지원하는가"가 추가된다. 응답 항목에 `mlx_repo`·`ollama_tag`도 추가로 싣는다(라이브 패널이 리포 id를 알아야 함).

## API

`video_models.py`의 검증된 패턴을 미러한다.

- `GET /translate-models?refresh=false` → `await run_in_threadpool(translate_catalog.get_remote_models, refresh)` 후 목록 반환. 블로킹 `requests.get`의 스레드풀 오프로드까지 동일(v1.3.8에서 한 번 밟은 함정).
- `POST /{name}/download`, `DELETE /{name}` → `tmods._TIER_BY_NAME` 대신 `get_catalog()`. `/ollama/install`이 `/{name}` 앞에 선언된 순서 규약 유지.
- **`POST /{name}/download`에 "현재 런타임 미지원" 409를 추가한다** — `reason`이 있는 티어(= 이 서버의 런타임을 지원하지 않는 티어)는 거부한다. UI 비활성에만 의존하면 안 된다: MLX 전용 티어(`ollama_tag` 없음)를 윈도우 서버에 POST하면 `qwen_ollama_model()`이 `None`을 반환하고 그대로 `pull_model(None)`까지 흘러가 `{"model": null}`로 Ollama에 요청이 나간다. 이 API는 무인증(LAN 신뢰경계)이라 UI를 거치지 않는 호출이 정상 경로다.
- 방어 심층화로 `translate_models.download_model(name)`도 미지원 런타임이면 `RuntimeError`를 던진다(API가 409로 변환). `delete_model`도 동일.
- `video_jobs.py` — **변경 없음**(list_translate_engines 파생이라 자동).

## 클라이언트

### 자막메이커 탭 (`apps/desktop/src/console/VideoCaptionPanel.tsx`)

- `refreshTranslateModels()`(`?refresh=true`) 추가.
- 번역 모델 탭에 전사 탭과 동일한 "카탈로그 새로고침" 버튼(동일 스타일·`refreshingCatalog` 상태 관례).
- `reason`이 있는 항목만 사유 라벨 표시 + 다운로드 버튼 disable(미설치 티어는 정상적으로 다운로드 가능해야 함).

### 라이브 자막 패널 (`apps/server_desktop`)

신규 `translateCatalogAdmin.ts` — `videoJobsAdmin.ts` 관례(`base(port)` + `fetch`, 무인증 루프백) 미러:

```ts
export type LiveMlxModel = { id: string; label: string; bytes: number };
export const BUILTIN_MLX_MODELS: LiveMlxModel[] = [ /* 현행 3종 이관 */ ];
export async function listLiveMlxModels(port: number): Promise<LiveMlxModel[]>
```

- `GET /translate-models` 응답에서 `mlx_repo`가 있는 항목만 `{id: mlx_repo, label, bytes: mlx_bytes}`로 변환.
- **fetch 실패·서버 미기동 → `BUILTIN_MLX_MODELS` 폴백**(결정 2).
- `serverConfig.ts`의 `MLX_MODELS` 상수는 이 파일의 `BUILTIN_MLX_MODELS`로 이관(폴백 겸용).
- 상태 확인·다운로드는 기존 Tauri 커맨드(`mlx_model_status`/`mlx_download_model`) 유지 → 서버 없이 동작.
- **포트 배선**: `ServerConfigPanel`이 현재 포트를 모른다(`<ServerConfigPanel />`로 렌더). `ServerConsole`의 `port` 상태를 `ServerConfigPanel` → `MlxModelPanel`로 prop 한 단계 전달.
- **라벨**: 기존 라이브 라벨(`Qwen3.5 9B (기본 — 품질 우선, RAM ~5GB)`)이 카탈로그 라벨(`Qwen 9B (로컬)`)로 대체되며 RAM 힌트가 사라진다. 패널이 `label · ~5.0GB` 형태로 `mlx_bytes`를 포맷해 붙여 정보값을 유지한다.

## 에러 처리

whisper와 동일한 계약:

- 원격 실패 → 캐시 → 빌트인. **예외는 절대 전파하지 않는다.**
- 잘못된 항목은 경고 로그 후 스킵(다른 항목은 살아남는다).
- non-https URL은 무시하고 캐시/빌트인으로 폴백.
- 라이브 패널 fetch 실패 → 빌트인 폴백(조용히).
- 다운로드 중 삭제 → 409(현행 유지).

## 테스트

- `test_remote_catalog.py` — 패치 대상을 `rc._http_get`/`rc._now` → `catalog_fetch._http_get`/`_now`로 이관(약 10곳, 기계적). **whisper 동작은 그대로 통과해야 한다 — 리팩터링 안전망.**
- 신규 `test_translate_catalog.py` — 병합·오버라이드, 빌트인 삭제 불가, 한쪽 런타임만 있는 항목, 잘못된 항목 스킵, **env 키 규칙이 기존 3키를 그대로 재현하는지**(하위호환 회귀 방지).
- 신규 `test_api_translate_models.py` — `?refresh` 동작, 404(미지 모델), 409(Ollama 미기동).
- **`available` / `reason` 분리 회귀** — 미설치 티어는 `available: false` + `reason: None`(다운로드 가능), 지원 런타임이 없는 티어만 `reason` 채워짐. 이 둘이 섞이면 정상 티어의 다운로드 버튼이 죽는다.
- **미지원 런타임 다운로드 거부** — MLX 전용 티어를 `runtime()=="ollama"`로 모킹하고 `POST /{name}/download` → 409, `pull_model`이 **호출되지 않음**을 단언(`{"model": null}` 요청 방지).
- **`mlx_repo=None` 가드** — `_is_apple_silicon_mac()`을 True로 모킹하고 Ollama 전용 티어에 대해 `list_models`(`is_installed`를 직접 호출하는 경로)가 터지지 않는지 확인한다(실리콘맥 크래시 회귀 — 여기선 실제로 살아있는 방어선). `list_translate_engines`에도 같은 모양의 가드가 `_qwen_available` 내부에 있지만, `reason`이 이미 있으면 `_qwen_available` 자체를 호출하지 않으므로 그 가드는 도달 불가능한 죽은 방어다 — 진짜 방어선은 `create_translator`에 있고, 그건 `test_create_translator_no_crash_for_ollama_only_tier_on_silicon`이 별도로 커버한다.
- 기존 `test_video_translate_*.py` / `test_api_video_jobs.py` — 상수 몽키패치가 있으면 카탈로그 패치로 전환.
- 신규 `translateCatalogAdmin.test.ts` — 서버 다운 시 빌트인 폴백.

**회귀 기준선**: 빌트인 3종의 값이 그대로 유지되므로, 원격 JSON이 비어 있는 초기 상태에서 사용자 눈에 보이는 변화가 없어야 한다.

## 플랫폼 점검 (윈도우·인텔맥)

설계 확정 후 윈도우 경로를 코드로 확인한 결과 — 차단 이슈 없음.

| 항목 | 상태 |
|---|---|
| **cp949 subprocess 함정** | 무관. `translate_ollama.py`는 전부 httpx HTTP(`/api/tags`·`/api/pull`·`/api/delete`·`/api/generate`)이고, `ollama_install.py`의 윈도 분기는 `os.startfile`이라 stdout을 디코딩하는 경로가 없다 |
| **`STORAGE_ROOT`** | Tauri가 전 플랫폼 주입(`server_process.rs:287`) → 캐시 경로 유효 |
| **의존성** | `catalog_fetch`의 `requests`는 v1.3.8이 이미 윈도우 3플랫폼 CI로 검증 — 신규 의존성 없음 |
| **런타임 구조** | `runtime()` → ollama 유지. GPU 선택(CUDA/Metal/CPU)은 Ollama가 자동 처리 |
| **`os.startfile` 반자동 설치** | 변경 없음 |

이 점검에서 위의 3개 갭(캐시 경로 / 미지원 런타임 409 / `mlx_repo=None` 가드)이 드러나 스펙에 반영했다.

**서버 콘솔의 `MlxModelPanel`이 윈도우에서도 MLX 모델을 나열하는 것은 기존 동작이다** — 현재도 하드코딩 3종을 플랫폼 무관하게 렌더한다. 카탈로그로 바뀌어도 동일하며, 이 설계가 만드는 회귀가 아니다.

## 범위 밖 / 후속

- **윈도우·인텔맥의 라이브 자막 번역**: `MlxModelPanel`은 `apple_mlx_live_translate`(실리콘맥 전용) 전용이고, 그 외 플랫폼의 라이브 자막은 Gemini를 쓴다. 이 설계가 바꾸지 않는다. 즉 라이브 쪽 효과는 실리콘맥 한정이며, 윈도우·인텔맥에 새 모델이 열리는 건 **자막메이커 쪽**이다(Ollama 런타임).
- **원격 카탈로그 운영 정책**: 원격 JSON은 리포 main이라 우리만 편집한다. 티어를 올리기 전 **두 런타임 각각**(실리콘맥 MLX / 윈도우·인텔맥 Ollama)에서 `build_translation_prompt` → JSON 배열 KO 계약을 지키는지 실기 검증한다. whisper와 달리 번역은 프롬프트 계약에 묶여 있어(`_extract_json_array` 실패 시 `TranslationError`) 검증 비용이 티어당 2회다. 설계상의 제약이 아니라 릴리스 절차의 항목.
