# Whisper 전사 모델 — `large-v3-turbo` 추가 + 원격 카탈로그

- 날짜: 2026-07-14
- 대상: 자막메이커(Video Caption Studio) 서버 사이드카 + 데스크톱 UI
- 상태: 설계 확정 (구현 대기)

## 배경 / 문제

자막메이커의 whisper 전사 모델 목록은 서버 사이드카의
`apps/server/domain/video_captions/whisper_models.py` 안 `CATALOG`(하드코딩 dict)로
정의된다. 항목마다 `ModelInfo(repo_id, approx_bytes, label)`를 갖고, 다운로드는
`huggingface_hub.snapshot_download(repo_id, local_dir)`로 이루어진다.

두 가지 요구:

1. **`large-v3-turbo` 추가** — large급 품질에 약 5~8배 빠른 실물 모델을 지금 카탈로그에 넣는다.
2. **원격 카탈로그** — 앞으로 새 whisper 모델이 나올 때 **앱 재배포 없이** 카탈로그를
   확장할 수 있게, 원격 JSON을 fetch해서 빌트인 목록에 병합한다.

### 현재 코드의 함정 (반드시 해결)

`apps/server/api/v1/video_jobs.py`는 다음처럼 `CATALOG` **딕셔너리 객체를 import 시점에
바인딩**한다:

```python
from apps.server.domain.video_captions.whisper_models import CATALOG, is_downloaded
...
def _require_model(name: str) -> None:
    ...
    if name not in CATALOG:
        raise HTTPException(404, "unknown whisper model")
```

원격 병합 결과로 모듈 전역 `CATALOG`를 **재할당**하면 `video_jobs`가 잡은 참조는 옛
딕셔너리를 계속 가리켜, 원격 추가 모델을 job 제출 시 검증에서 404로 튕긴다. 이는 과거
번역엔진에서 "검증 패턴에 qwen 누락 → 422" 사고와 동일 계열의 버그다. 근본 수정은
**검증·목록·다운로드·삭제가 전부 하나의 동적 소스(`get_catalog()`)에서 도출**되게 하는 것.

## 목표 / 비목표

**목표**
- `large-v3-turbo`를 빌트인 카탈로그에 추가(오프라인에서도 항상 존재).
- 원격 JSON 카탈로그를 fetch·검증·캐시하여 빌트인에 병합.
- 새 모델 추가가 리포 main에 JSON 한 줄 push만으로 반영(앱 재배포 불필요).
- 목록/검증/다운로드/삭제가 단일 동적 소스에서 도출되어 stale-reference 버그 제거.

**비목표**
- HuggingFace 전체 자동 탐색(임의 repo 자동 노출) — 하지 않음(보안·불안정).
- Apple 온디바이스 엔진 로직 변경 — 그대로 둠(카탈로그 밖 센티널).
- GPU 팩(CUDA DLL) 로직 변경 — 무관.

## 아키텍처

### 컴포넌트 개요

| 유닛 | 위치 | 책임 |
|---|---|---|
| `BUILTIN_CATALOG` | `whisper_models.py` | 동결된 baseline 목록(신규 `large-v3-turbo` 포함) |
| `get_catalog()` | `whisper_models.py` | baseline + 원격 병합 결과를 반환하는 **단일 접근자** |
| `remote_catalog.py` | `domain/video_captions/` (신규) | 원격 JSON fetch·검증·디스크 캐시·TTL |
| `list_models` / `download_model` / `delete_model` | `whisper_models.py` | 전부 `get_catalog()` 참조로 전환 |
| `_require_model` | `api/v1/video_jobs.py` | `CATALOG` import 제거 → `get_catalog()` 호출 |
| `GET /video-models[?refresh=1]` | `api/v1/video_models.py` | 병합 목록 반환, `refresh=1`이면 TTL 무시 강제 fetch |
| 전사 모델 탭 | `console/VideoCaptionPanel.tsx` | "카탈로그 새로고침" 버튼 추가 |
| `refreshVideoModels()` | `console/videoApi.ts` | `GET /video-models?refresh=1` 래퍼 |

### 1) 동적 CATALOG (단일 소스)

`whisper_models.py`:

```python
BUILTIN_CATALOG: dict[str, ModelInfo] = {
    "tiny":            ModelInfo("Systran/faster-whisper-tiny",     75_000_000,    "가장 빠름, 초벌용"),
    "base":            ModelInfo("Systran/faster-whisper-base",     145_000_000,   "빠름, 짧은 영상"),
    "small":           ModelInfo("Systran/faster-whisper-small",    486_000_000,   "권장 기본값 (품질/속도 균형)"),
    "medium":          ModelInfo("Systran/faster-whisper-medium",   1_530_000_000, "고품질, 느림"),
    "large-v3":        ModelInfo("Systran/faster-whisper-large-v3", 3_090_000_000, "최고 품질, 가장 느림"),
    "large-v3-turbo":  ModelInfo("mobiuslabsgmbh/faster-whisper-large-v3-turbo",
                                 1_620_000_000, "고품질·고속 (large급 품질, 약 5~8배 빠름)"),
}

def get_catalog() -> dict[str, ModelInfo]:
    """빌트인 baseline에 원격 카탈로그를 오버레이한 유효 목록."""
    merged = dict(BUILTIN_CATALOG)
    for m in remote_catalog.get_remote_models():   # 이미 검증·캐시된 목록
        merged[m.name] = ModelInfo(m.repo_id, m.approx_bytes, m.label)
    return merged
```

- 하위호환: 기존에 `CATALOG`를 참조하던 코드가 있으면 전부 `get_catalog()` 호출로 교체한다.
  (모듈 전역 `CATALOG` 이름은 제거하여 stale 바인딩 재발을 원천 차단.)
- `remote_catalog` import는 순환 참조가 없도록 함수 안 지역 import 또는 모듈 상단 단방향
  import(remote_catalog는 whisper_models를 import하지 않음)로 둔다.

### 2) `large-v3-turbo` 실측 근거

HuggingFace API 확인(2026-07-14):
- `mobiuslabsgmbh/faster-whisper-large-v3-turbo` → `model.bin` 1.62GB, 7 files (faster-whisper 라이브러리 공식 매핑 대상).
- `deepdml/faster-whisper-large-v3-turbo-ct2` → 동일 1.62GB (대체 후보).
- `Systran/faster-whisper-large-v3-turbo` → 미존재(401).

→ `mobiuslabsgmbh/...` 채택. `approx_bytes = 1_620_000_000`.

### 3) 원격 카탈로그 모듈 (`remote_catalog.py`, 신규)

```python
DEFAULT_CATALOG_URL = (
    "https://raw.githubusercontent.com/yesonsys03-web/yeson_meet/main/"
    "apps/server/domain/video_captions/whisper_catalog.remote.json"
)
CATALOG_URL_ENV = "YESON_WHISPER_CATALOG_URL"
CACHE_TTL_SECONDS = 6 * 3600

@dataclass(frozen=True)
class RemoteModel:
    name: str
    repo_id: str
    approx_bytes: int
    label: str
```

**원격 JSON 스키마** (`whisper_catalog.remote.json`):

```json
{
  "version": 1,
  "models": [
    { "name": "large-v3-turbo", "repo_id": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
      "approx_bytes": 1620000000, "label": "고품질·고속 (large급 품질, 약 5~8배 빠름)" }
  ]
}
```

- 시드 파일을 리포에 커밋(초기엔 빌트인과 동일한 turbo 항목 하나). 이후 새 모델은 이 파일
  수정 → main push로 배포. (병합 규칙상 빌트인과 중복돼도 무해 — 오버라이드가 동일값.)

**동작**
- URL: `os.environ.get(CATALOG_URL_ENV, DEFAULT_CATALOG_URL)`, `https://`만 허용(그 외는 무시하고 빈 목록).
- fetch: `_http_get(url)`(테스트 seam) → `requests.get(timeout=...)`. `requests`는 이미 의존성(gpu_pack).
- 검증 `_parse(payload) -> list[RemoteModel]`:
  - 최상위 `models`가 list가 아니면 전체 거부(빈 목록).
  - 각 항목: `name`(비어있지 않은 str, `^[A-Za-z0-9._-]+$`), `repo_id`(비어있지 않은 str),
    `approx_bytes`(양의 int), `label`(str). 하나라도 어긋나면 **그 항목만 스킵**하고 warning 로그.
- 디스크 캐시: `{STORAGE_ROOT}/whisper_models/remote_catalog.cache.json`,
  형식 `{ "fetched_at": <epoch>, "models": [...] }`.
- `get_remote_models(force=False) -> list[RemoteModel]`:
  1. `force`가 아니고 캐시가 신선(`now - fetched_at < TTL`)하면 캐시 파싱 결과 반환.
  2. 아니면 네트워크 fetch 시도 → 성공 시 검증→캐시 기록→반환.
  3. fetch 실패(네트워크/타임아웃/HTTP/JSON 오류) → **이전 캐시가 있으면 그걸 반환**,
     없으면 **빈 목록**. 예외를 상위로 던지지 않는다.
- 시간: `time.time()` 사용(캐시 TTL 판정). 테스트에서는 `_now()` seam으로 주입.

### 4) 병합 규칙

`get_catalog()` = 빌트인 baseline에 원격 오버레이:
- 원격은 **새 이름 추가** 가능.
- 원격은 **기존 이름의 label/approx_bytes/repo_id 덮어쓰기** 가능(깨진 repo 리포인트 대응).
- 원격은 **빌트인 항목 삭제 불가** → 오프라인/차단 환경에서도 앱 항상 동작.

### 5) 데이터 흐름 (탭 열기)

```
UI 모델 탭 open → GET /video-models
  → list_models()
      → get_catalog()
          → remote_catalog.get_remote_models()   # TTL 캐시 히트면 네트워크 안 탐
          → merge(builtin, remote)
      → 각 모델의 downloaded/disk_bytes/downloading/progress 계산
  → 병합 목록 반환

"카탈로그 새로고침" 클릭 → GET /video-models?refresh=1
  → get_remote_models(force=True)  # TTL 무시, 네트워크 재조회
```

### 6) API 변경

`api/v1/video_models.py`:
- `GET /video-models`에 옵션 쿼리 `refresh: bool = False`. `True`면 `list_models` 이전에
  `remote_catalog.get_remote_models(force=True)`를 한 번 호출(캐시 갱신)하고 목록 생성.
- `POST /video-models/{name}/download`, `DELETE`는 로직 그대로(내부에서 `get_catalog()`
  참조로 바뀌므로 원격 모델도 자동 지원). 404 판정도 `get_catalog()` 기준.

### 7) UI 변경

`console/VideoCaptionPanel.tsx` — "전사 모델" 탭 헤더 영역:
- 작은 **"카탈로그 새로고침"** 버튼 추가 → `refreshVideoModels()` → 완료 후 기존 `refresh()`
  (목록 재조회). 로딩 중 비활성/스피너.
- 원격에서 온 모델도 빌트인과 **동일한 렌더**(다운로드/삭제/진행률). 별도 뱃지 없음(YAGNI).

`console/videoApi.ts`:
- `refreshVideoModels(): Promise<VideoModelInfo[]>` = `GET /video-models?refresh=1`.
- 기존 `listVideoModels()` 유지.

## 에러 처리

- 원격 fetch/파싱 실패 → warning 로그, 캐시→빌트인 순으로 폴백. **`GET /video-models`는
  절대 500을 내지 않는다**(원격은 부가 정보).
- 개별 malformed 항목 → 스킵(전체 실패 아님).
- `refresh=1`도 실패 시 조용히 기존 목록(빌트인+마지막 캐시)으로 응답. UI는 토스트로 "원격
  카탈로그를 불러오지 못했습니다(캐시 사용)" 정도만 알림(선택).

## 보안 / 신뢰 모델

- URL은 `https://`만. 기본값은 우리 소유 **공개 리포 main**의 raw 파일 → 앱 바이너리/자동
  업데이터와 동일한 신뢰 경계.
- 원격 항목은 코드 실행이 아니라 HF `repo_id` 문자열일 뿐이며, 다운로드는 기존
  `snapshot_download` 경로를 그대로 탄다. (카탈로그가 탈취되면 악성 HF repo를 가리킬 수
  있으나, 이는 앱 자체가 탈취된 것과 동급 위험으로 수용.)
- `YESON_WHISPER_CATALOG_URL` 오버라이드는 자가호스팅 운영자용(문서화).

## 테스트

`apps/server/tests/test_video_whisper_models.py` / `test_api_video_models.py` 확장,
필요 시 신규 `test_remote_catalog.py`:

- `large-v3-turbo`가 빌트인 목록에 존재하고 `_require_model`(미다운로드 시 409, 미존재 아님)에서 인식.
- 병합: 원격이 새 이름 추가 → `get_catalog()`·`list_models()`·`GET /video-models`에 노출.
- 오버라이드: 원격이 기존 이름의 label/bytes/repo_id 교체.
- 삭제 불가: 원격에 없는 빌트인 항목은 병합 후에도 유지.
- 오프라인 폴백: fetch 예외 → 이전 캐시 반환, 캐시 없으면 빌트인만.
- malformed: 잘못된 항목 스킵, 정상 항목 유지.
- TTL: 신선 캐시면 `_http_get` 미호출; `force=True`면 호출.
- `_require_model`이 원격 추가 모델을 (다운로드됐다는 전제 stub 하에) 통과.

## 구현 순서(요약)

1. `whisper_models.py`: `BUILTIN_CATALOG` 리네임 + `large-v3-turbo` 추가 + `get_catalog()` 도입,
   내부 함수들(`list_models`/`download_model`/`delete_model`) `get_catalog()`로 전환.
2. `remote_catalog.py` 신규(fetch/검증/캐시/TTL, `_http_get`·`_now` seam).
3. `whisper_catalog.remote.json` 시드 커밋.
4. `video_jobs.py`: `CATALOG` import 제거 → `get_catalog()` 사용.
5. `video_models.py`: `?refresh=1` 지원.
6. UI: `videoApi.ts` `refreshVideoModels()` + `VideoCaptionPanel.tsx` 새로고침 버튼.
7. 테스트 확장 + `pytest` 통과.
8. frozen-bundle 재동결 후 서버앱 재시작(반영 확인) — 릴리스 시.

## 미해결/후속

- 이번 변경은 version bump 없이도 동작하나, 배포는 다음 릴리스에 포함(자막메이커 자산).
- turbo 다운로드 후 실제 전사 품질/속도는 실리콘/윈도 실기기에서 user-verify 후속.
