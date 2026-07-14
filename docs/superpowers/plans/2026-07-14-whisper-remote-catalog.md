# Whisper large-v3-turbo + 원격 카탈로그 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 자막메이커 whisper 전사 모델 카탈로그를 동적 소스로 바꿔 `large-v3-turbo`를 빌트인에 추가하고, 원격 JSON 카탈로그를 병합해 앱 재배포 없이 새 모델을 추가할 수 있게 한다.

**Architecture:** 서버 사이드카(`apps/server`)에 원격 카탈로그 모듈(fetch·검증·디스크 캐시·TTL)을 신설하고, `whisper_models`의 하드코딩 `CATALOG`를 `BUILTIN_CATALOG` + `get_catalog()`(빌트인 baseline에 원격 오버레이) 구조로 바꾼다. 목록·검증·다운로드·삭제·job 제출 검증이 전부 `get_catalog()` 단일 소스에서 도출되어 stale-reference 버그를 원천 차단한다. 데스크톱 UI에는 "카탈로그 새로고침" 버튼을 더한다.

**Tech Stack:** Python 3, FastAPI, faster-whisper, huggingface_hub, `requests`(기존 의존성), pytest(async client fixture), React + TypeScript(Tauri 데스크톱).

## Global Constraints

- 병합 규칙: 원격은 빌트인에 **추가/오버라이드만** 가능, **삭제 불가**(오프라인에서도 앱 동작).
- `GET /api/v1/video-models`는 원격 실패 시에도 **절대 500을 내지 않는다**(캐시→빌트인 폴백).
- 원격 URL은 `https://`만 허용. 기본값 = `https://raw.githubusercontent.com/yesonsys03-web/yeson_meet/main/apps/server/domain/video_captions/whisper_catalog.remote.json`, env `YESON_WHISPER_CATALOG_URL`로 오버라이드.
- 캐시 TTL 기본 6h(`CACHE_TTL_SECONDS = 6 * 3600`), 캐시 파일 = `{STORAGE_ROOT}/whisper_models/remote_catalog.cache.json`.
- `large-v3-turbo` 빌트인 값: repo_id `mobiuslabsgmbh/faster-whisper-large-v3-turbo`, approx_bytes `1_620_000_000`, label `"고품질·고속 (large급 품질, 약 5~8배 빠름)"`.
- 모듈 전역 `CATALOG` 이름은 **제거** — 모든 참조를 `get_catalog()`(또는 `BUILTIN_CATALOG`)로 교체.
- 개별 malformed 원격 항목은 **스킵**(전체 실패 아님), warning 로그.
- 테스트는 리포 루트에서 `python -m pytest apps/server/tests/... -v`로 실행(pyproject testpaths가 server만 가리키므로 경로 명시).
- 커밋 메시지 말미: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

### Task 1: 원격 카탈로그 모듈 + 시드 JSON

**Files:**
- Create: `apps/server/domain/video_captions/remote_catalog.py`
- Create: `apps/server/domain/video_captions/whisper_catalog.remote.json`
- Test: `apps/server/tests/test_remote_catalog.py`

**Interfaces:**
- Consumes: `whisper_models.models_root()`(캐시 경로용, 함수 내부 지역 import — 순환 방지).
- Produces:
  - `RemoteModel(name: str, repo_id: str, approx_bytes: int, label: str)` (frozen dataclass)
  - `get_remote_models(force: bool = False) -> list[RemoteModel]`
  - 테스트 seam: `_http_get(url: str) -> str`, `_now() -> float`
  - 상수: `DEFAULT_CATALOG_URL`, `CATALOG_URL_ENV`, `CACHE_TTL_SECONDS`

- [ ] **Step 1: 실패 테스트 작성**

Create `apps/server/tests/test_remote_catalog.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.server.domain.video_captions import remote_catalog as rc


@pytest.fixture(autouse=True)
def _storage(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    yield


def _payload(*models: dict) -> str:
    return json.dumps({"version": 1, "models": list(models)})


VALID = {"name": "large-v3-turbo", "repo_id": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
         "approx_bytes": 1_620_000_000, "label": "고품질·고속"}


def test_fetch_parses_valid_models(monkeypatch):
    monkeypatch.setattr(rc, "_http_get", lambda url: _payload(VALID))
    monkeypatch.setattr(rc, "_now", lambda: 1000.0)
    out = rc.get_remote_models(force=True)
    assert [m.name for m in out] == ["large-v3-turbo"]
    assert out[0].repo_id == "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
    assert out[0].approx_bytes == 1_620_000_000


def test_skips_malformed_entries_keeps_valid(monkeypatch):
    bad = [
        {"name": "no-repo", "approx_bytes": 10, "label": "x"},          # repo_id 없음
        {"name": "bad name!", "repo_id": "a/b", "approx_bytes": 10, "label": "x"},  # name 정규식
        {"name": "neg", "repo_id": "a/b", "approx_bytes": -1, "label": "x"},        # 음수
        {"name": "boolbytes", "repo_id": "a/b", "approx_bytes": True, "label": "x"},  # bool
        "not-a-dict",
    ]
    monkeypatch.setattr(rc, "_http_get", lambda url: _payload(VALID, *bad))
    monkeypatch.setattr(rc, "_now", lambda: 1000.0)
    out = rc.get_remote_models(force=True)
    assert [m.name for m in out] == ["large-v3-turbo"]


def test_cache_hit_skips_network(monkeypatch):
    calls = {"n": 0}
    def fake_get(url):
        calls["n"] += 1
        return _payload(VALID)
    monkeypatch.setattr(rc, "_http_get", fake_get)
    monkeypatch.setattr(rc, "_now", lambda: 1000.0)
    rc.get_remote_models(force=True)        # writes cache at t=1000
    assert calls["n"] == 1
    monkeypatch.setattr(rc, "_now", lambda: 1000.0 + 3600)  # < TTL(6h)
    rc.get_remote_models(force=False)       # cache fresh -> no network
    assert calls["n"] == 1


def test_ttl_expiry_refetches(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(rc, "_http_get", lambda url: (calls.__setitem__("n", calls["n"] + 1), _payload(VALID))[1])
    monkeypatch.setattr(rc, "_now", lambda: 1000.0)
    rc.get_remote_models(force=True)
    monkeypatch.setattr(rc, "_now", lambda: 1000.0 + rc.CACHE_TTL_SECONDS + 1)
    rc.get_remote_models(force=False)
    assert calls["n"] == 2


def test_fetch_failure_falls_back_to_cache(monkeypatch):
    monkeypatch.setattr(rc, "_http_get", lambda url: _payload(VALID))
    monkeypatch.setattr(rc, "_now", lambda: 1000.0)
    rc.get_remote_models(force=True)  # populate cache
    def boom(url):
        raise RuntimeError("network down")
    monkeypatch.setattr(rc, "_http_get", boom)
    monkeypatch.setattr(rc, "_now", lambda: 1000.0 + rc.CACHE_TTL_SECONDS + 1)  # stale -> tries network
    out = rc.get_remote_models(force=True)
    assert [m.name for m in out] == ["large-v3-turbo"]  # served from cache


def test_no_cache_no_network_returns_empty(monkeypatch):
    def boom(url):
        raise RuntimeError("network down")
    monkeypatch.setattr(rc, "_http_get", boom)
    monkeypatch.setattr(rc, "_now", lambda: 1000.0)
    assert rc.get_remote_models(force=True) == []


def test_non_https_url_ignored(monkeypatch):
    monkeypatch.setenv(rc.CATALOG_URL_ENV, "http://evil.example/catalog.json")
    called = {"n": 0}
    monkeypatch.setattr(rc, "_http_get", lambda url: called.__setitem__("n", 1) or _payload(VALID))
    monkeypatch.setattr(rc, "_now", lambda: 1000.0)
    assert rc.get_remote_models(force=True) == []
    assert called["n"] == 0
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest apps/server/tests/test_remote_catalog.py -v`
Expected: FAIL — `ModuleNotFoundError: ... remote_catalog` (모듈 없음)

- [ ] **Step 3: 모듈 구현**

Create `apps/server/domain/video_captions/remote_catalog.py`:

```python
"""Remote whisper catalog overlay — fetch/validate/cache an optional model list.

새 whisper 모델을 앱 재배포 없이 추가하기 위한 오버레이. 리포 main의
``whisper_catalog.remote.json``을 TTL 캐시로 fetch해 빌트인에 병합한다
(병합은 whisper_models.get_catalog()). 빌트인은 항상 유지되는 baseline이며,
원격은 추가/오버라이드만 한다.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger("yeson.video.remote_catalog")

DEFAULT_CATALOG_URL = (
    "https://raw.githubusercontent.com/yesonsys03-web/yeson_meet/main/"
    "apps/server/domain/video_captions/whisper_catalog.remote.json"
)
CATALOG_URL_ENV = "YESON_WHISPER_CATALOG_URL"
CACHE_TTL_SECONDS = 6 * 3600
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class RemoteModel:
    name: str
    repo_id: str
    approx_bytes: int
    label: str


def _catalog_url() -> str:
    return os.environ.get(CATALOG_URL_ENV, DEFAULT_CATALOG_URL)


def _cache_path() -> Path:
    # 지역 import — whisper_models가 이 모듈을 import하므로 순환 방지.
    from apps.server.domain.video_captions.whisper_models import models_root
    return models_root() / "remote_catalog.cache.json"


def _now() -> float:  # test seam
    return time.time()


def _http_get(url: str) -> str:  # test seam
    import requests
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return resp.text


def _parse(payload: object) -> list[RemoteModel]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("models")
    if not isinstance(raw, list):
        return []
    out: list[RemoteModel] = []
    for item in raw:
        if not isinstance(item, dict):
            logger.warning("remote_catalog: skip non-dict entry: %r", item)
            continue
        name = item.get("name")
        repo_id = item.get("repo_id")
        approx = item.get("approx_bytes")
        label = item.get("label")
        if (not isinstance(name, str) or not _NAME_RE.match(name)
                or not isinstance(repo_id, str) or not repo_id
                or not isinstance(approx, int) or isinstance(approx, bool) or approx <= 0
                or not isinstance(label, str)):
            logger.warning("remote_catalog: skip invalid entry: %r", item)
            continue
        out.append(RemoteModel(name=name, repo_id=repo_id, approx_bytes=approx, label=label))
    return out


def _read_cache() -> tuple[float, list[RemoteModel]] | None:
    path = _cache_path()
    if not path.is_file():
        return None
    try:
        blob = json.loads(path.read_text("utf-8"))
        fetched_at = float(blob.get("fetched_at", 0))
        return fetched_at, _parse(blob)
    except Exception as exc:  # 손상된 캐시는 없는 것으로 취급
        logger.warning("remote_catalog: bad cache file: %s", exc)
        return None


def _write_cache(models: list[RemoteModel]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": _now(), "models": [asdict(m) for m in models]}
    path.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")


def get_remote_models(force: bool = False) -> list[RemoteModel]:
    """원격 카탈로그의 검증된 모델 목록. 네트워크/파싱 실패 시 캐시→빈 목록으로 폴백(예외 없음)."""
    cached = _read_cache()
    if not force and cached is not None:
        fetched_at, models = cached
        if _now() - fetched_at < CACHE_TTL_SECONDS:
            return models
    url = _catalog_url()
    if not url.startswith("https://"):
        logger.warning("remote_catalog: non-https url ignored: %s", url)
        return cached[1] if cached else []
    try:
        text = _http_get(url)
        models = _parse(json.loads(text))
        _write_cache(models)
        return models
    except Exception as exc:
        logger.warning("remote_catalog: fetch failed (%s); using cache/builtin", exc)
        return cached[1] if cached else []
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest apps/server/tests/test_remote_catalog.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: 시드 JSON 생성**

Create `apps/server/domain/video_captions/whisper_catalog.remote.json`:

```json
{
  "version": 1,
  "models": [
    {
      "name": "large-v3-turbo",
      "repo_id": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
      "approx_bytes": 1620000000,
      "label": "고품질·고속 (large급 품질, 약 5~8배 빠름)"
    }
  ]
}
```

- [ ] **Step 6: 커밋**

```bash
git add apps/server/domain/video_captions/remote_catalog.py \
        apps/server/domain/video_captions/whisper_catalog.remote.json \
        apps/server/tests/test_remote_catalog.py
git commit -m "feat(video): 원격 whisper 카탈로그 모듈 + 시드 JSON

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: 동적 카탈로그 + large-v3-turbo 빌트인

**Files:**
- Modify: `apps/server/domain/video_captions/whisper_models.py`
- Test: `apps/server/tests/test_video_whisper_models.py`

**Interfaces:**
- Consumes: `remote_catalog.get_remote_models()` (Task 1).
- Produces:
  - `BUILTIN_CATALOG: dict[str, ModelInfo]` (기존 5개 + `large-v3-turbo`)
  - `get_catalog() -> dict[str, ModelInfo]` (빌트인 + 원격 병합)
  - 기존 `list_models`/`download_model`/`delete_model`/`is_downloaded`/`model_dir`/`models_root` 시그니처 불변
  - `CATALOG` 전역은 **삭제됨** (참조 금지)

- [ ] **Step 1: 실패 테스트로 갱신**

Modify `apps/server/tests/test_video_whisper_models.py` — `test_catalog_has_expected_models`(현재 L16-18)를 교체하고 병합 테스트 추가:

```python
def test_builtin_catalog_has_expected_models():
    assert set(wm.BUILTIN_CATALOG) == {
        "tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"}
    assert wm.BUILTIN_CATALOG["small"].repo_id == "Systran/faster-whisper-small"
    turbo = wm.BUILTIN_CATALOG["large-v3-turbo"]
    assert turbo.repo_id == "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
    assert turbo.approx_bytes == 1_620_000_000


def test_get_catalog_merges_remote(monkeypatch):
    from apps.server.domain.video_captions import remote_catalog as rc
    monkeypatch.setattr(rc, "get_remote_models", lambda force=False: [
        rc.RemoteModel("future-xl", "acme/future-xl", 9_000_000_000, "실험 모델"),
        rc.RemoteModel("small", "acme/override-small", 111, "덮어쓴 라벨"),  # override
    ])
    cat = wm.get_catalog()
    assert "future-xl" in cat and cat["future-xl"].repo_id == "acme/future-xl"
    assert cat["small"].repo_id == "acme/override-small"      # 오버라이드
    assert cat["small"].label == "덮어쓴 라벨"
    # 빌트인 삭제 불가: 원격에 없어도 유지
    assert "large-v3" in cat


def test_get_catalog_offline_is_builtin(monkeypatch):
    from apps.server.domain.video_captions import remote_catalog as rc
    monkeypatch.setattr(rc, "get_remote_models", lambda force=False: [])
    assert set(wm.get_catalog()) == set(wm.BUILTIN_CATALOG)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest apps/server/tests/test_video_whisper_models.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'BUILTIN_CATALOG'` / `get_catalog`

- [ ] **Step 3: whisper_models.py 수정**

`whisper_models.py`에서 상단 import에 remote_catalog 추가(기존 import 블록 아래):

```python
from apps.server.domain.video_captions import remote_catalog
```

`CATALOG` 정의(현재 L28-34)를 `BUILTIN_CATALOG`로 바꾸고 turbo 추가:

```python
BUILTIN_CATALOG: dict[str, ModelInfo] = {
    "tiny": ModelInfo("Systran/faster-whisper-tiny", 75_000_000, "가장 빠름, 초벌용"),
    "base": ModelInfo("Systran/faster-whisper-base", 145_000_000, "빠름, 짧은 영상"),
    "small": ModelInfo("Systran/faster-whisper-small", 486_000_000, "권장 기본값 (품질/속도 균형)"),
    "medium": ModelInfo("Systran/faster-whisper-medium", 1_530_000_000, "고품질, 느림"),
    "large-v3": ModelInfo("Systran/faster-whisper-large-v3", 3_090_000_000, "최고 품질, 가장 느림"),
    "large-v3-turbo": ModelInfo(
        "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        1_620_000_000, "고품질·고속 (large급 품질, 약 5~8배 빠름)"),
}


def get_catalog() -> dict[str, ModelInfo]:
    """빌트인 baseline에 원격 카탈로그를 오버레이한 유효 목록.

    원격은 새 이름 추가·기존 이름 오버라이드만 가능하고 빌트인 삭제는 불가하다.
    """
    merged = dict(BUILTIN_CATALOG)
    for m in remote_catalog.get_remote_models():
        merged[m.name] = ModelInfo(m.repo_id, m.approx_bytes, m.label)
    return merged
```

`download_model`(현재 L67) 내부 `info = CATALOG[name]` → `info = get_catalog()[name]`.

`delete_model`(현재 L84) 내부 `CATALOG[name]` → `get_catalog()[name]`.

`list_models`(현재 L93) 내부 `for name, info in CATALOG.items():` → `for name, info in get_catalog().items():`.

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest apps/server/tests/test_video_whisper_models.py -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add apps/server/domain/video_captions/whisper_models.py \
        apps/server/tests/test_video_whisper_models.py
git commit -m "feat(video): 동적 whisper 카탈로그(get_catalog) + large-v3-turbo 빌트인

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: API 배선 (검증 단일화 + refresh)

**Files:**
- Modify: `apps/server/api/v1/video_jobs.py:39,115`
- Modify: `apps/server/api/v1/video_models.py:29-44,85,97`
- Test: `apps/server/tests/test_api_video_models.py`

**Interfaces:**
- Consumes: `whisper_models.get_catalog()` (Task 2), `remote_catalog.get_remote_models(force=True)` (Task 1).
- Produces: `GET /api/v1/video-models?refresh=<bool>` (기본 false).

- [ ] **Step 1: 실패 테스트로 갱신**

Modify `apps/server/tests/test_api_video_models.py` — 목록 기대값에 turbo 추가하고 refresh 테스트 추가.

`test_list_models`의 마지막 assert(현재 L24) 교체:

```python
    assert names == ["apple", "tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]
```

파일 하단에 추가:

```python
async def test_refresh_forces_remote_fetch(client, monkeypatch):
    from apps.server.api.v1 import video_models as api_vm
    from apps.server.domain.video_captions import remote_catalog as rc
    monkeypatch.setattr(api_vm, "apple_stt_available", lambda: False)
    seen = {"force": None}
    monkeypatch.setattr(rc, "get_remote_models",
                        lambda force=False: seen.__setitem__("force", force) or [])
    resp = await client.get("/api/v1/video-models?refresh=1")
    assert resp.status_code == 200
    assert seen["force"] is True


async def test_list_without_refresh_does_not_force(client, monkeypatch):
    from apps.server.api.v1 import video_models as api_vm
    from apps.server.domain.video_captions import remote_catalog as rc
    monkeypatch.setattr(api_vm, "apple_stt_available", lambda: False)
    seen = {"force": "unset"}
    monkeypatch.setattr(rc, "get_remote_models",
                        lambda force=False: seen.__setitem__("force", force) or [])
    resp = await client.get("/api/v1/video-models")
    assert resp.status_code == 200
    # list_models 내부(get_catalog)에서만 호출되므로 force=True는 오지 않는다
    assert seen["force"] in (False, "unset")
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest apps/server/tests/test_api_video_models.py -v`
Expected: FAIL — 목록에 large-v3-turbo 없음 / refresh 파라미터 미반영

- [ ] **Step 3: video_jobs.py 수정**

L39 import 교체:

```python
from apps.server.domain.video_captions.whisper_models import get_catalog, is_downloaded
```

`_require_model` 내부 L115 교체:

```python
    if name not in get_catalog():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown whisper model")
```

- [ ] **Step 4: video_models.py 수정**

상단 import에 remote_catalog 추가(기존 `from ... import whisper_models as wm` 아래):

```python
from apps.server.domain.video_captions import remote_catalog
```

`list_video_models`(현재 L28-44) 시그니처와 앞부분 교체:

```python
@router.get("")
async def list_video_models(refresh: bool = False) -> dict:
    if refresh:
        # TTL 무시하고 원격 재조회(수동 새로고침). 실패해도 예외 없음.
        remote_catalog.get_remote_models(force=True)
    models = wm.list_models()
```

(이하 Apple 삽입 블록은 그대로 유지.)

download/delete 핸들러의 `if name not in wm.CATALOG:`(L85, L97) 2곳을 교체:

```python
    if name not in wm.get_catalog():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown model")
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest apps/server/tests/test_api_video_models.py apps/server/tests/test_api_video_jobs.py -v`
Expected: PASS

- [ ] **Step 6: 서버 전체 회귀 확인**

Run: `python -m pytest apps/server/tests/test_video_whisper_models.py apps/server/tests/test_remote_catalog.py apps/server/tests/test_api_video_models.py apps/server/tests/test_api_video_jobs.py apps/server/tests/test_video_models.py -v`
Expected: PASS (전 항목)

- [ ] **Step 7: 커밋**

```bash
git add apps/server/api/v1/video_jobs.py apps/server/api/v1/video_models.py \
        apps/server/tests/test_api_video_models.py
git commit -m "feat(video): 모델 검증을 get_catalog로 단일화 + /video-models?refresh

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: 데스크톱 UI — 카탈로그 새로고침 버튼

**Files:**
- Modify: `apps/desktop/src/console/videoApi.ts:63-67`
- Modify: `apps/desktop/src/console/VideoCaptionPanel.tsx` (import L7, state ~L96, handler ~L146, header UI ~L804)

**Interfaces:**
- Consumes: `GET /api/v1/video-models?refresh=1` (Task 3).
- Produces: `refreshVideoModels(): Promise<VideoModelInfo[]>`, UI 버튼.

- [ ] **Step 1: videoApi.ts에 래퍼 추가**

`listVideoModels`(L63-67) 바로 아래에 삽입:

```ts
export async function refreshVideoModels(): Promise<VideoModelInfo[]> {
  const out = await request<{ models: VideoModelInfo[] }>(
    `${apiBase()}/api/v1/video-models?refresh=1`, {});
  return out.models;
}
```

- [ ] **Step 2: VideoCaptionPanel.tsx — import 추가**

L7 근처 import 목록의 `listVideoModels`를 `listVideoModels, refreshVideoModels`로 확장(같은 `from "./videoApi"` 블록 내).

- [ ] **Step 3: state + 핸들러 추가**

`const [models, setModels] = useState<VideoModelInfo[]>([]);`(L96) 아래에 추가:

```tsx
  const [refreshingCatalog, setRefreshingCatalog] = useState(false);
```

`refresh` useCallback(L146) 정의 뒤에 추가:

```tsx
  const refreshCatalog = useCallback(async () => {
    setRefreshingCatalog(true);
    try {
      setModels(await refreshVideoModels());
    } catch {
      // 실패해도 기존 목록 유지(원격은 부가 정보)
    } finally {
      setRefreshingCatalog(false);
    }
  }, []);
```

- [ ] **Step 4: 버튼 렌더**

전사 모델 설명 `<p>`(현재 L804-806, "모델은 서버에 저장됩니다…") 바로 아래에 삽입:

```tsx
        <button type="button" style={{ ...consoleStyles.mutedAction, alignSelf: "flex-start" }}
          disabled={refreshingCatalog}
          onClick={() => void refreshCatalog()}>
          {refreshingCatalog ? "새로고침 중…" : "카탈로그 새로고침"}
        </button>
```

- [ ] **Step 5: 타입체크/빌드 확인**

Run: `cd apps/desktop && pnpm tsc --noEmit`
Expected: 에러 없음 (신규 심볼 `refreshVideoModels`, `refreshingCatalog`, `refreshCatalog` 모두 해소)

- [ ] **Step 6: 커밋**

```bash
git add apps/desktop/src/console/videoApi.ts apps/desktop/src/console/VideoCaptionPanel.tsx
git commit -m "feat(video): 전사 모델 탭에 '카탈로그 새로고침' 버튼

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## 최종 검증 (전체)

- [ ] 서버 테스트: `python -m pytest apps/server/tests/ -q` → 전부 PASS
- [ ] 프런트 타입: `cd apps/desktop && pnpm tsc --noEmit` → 에러 없음
- [ ] 수동 스모크(선택, 서버앱 재동결 후): 모델 관리 → 전사 모델 탭에 `large-v3-turbo`가 목록에 보이고 다운로드 가능, "카탈로그 새로고침" 동작.

## 배포 메모

- frozen-bundle 앱은 소스 변경 후 `build-server.sh` 재동결 + 서버앱 재시작해야 반영(자막메이커 릴리스 시). tauri:dev 중에는 재동결 금지.
- 이번 변경은 다음 자막메이커 릴리스에 포함. 원격 카탈로그 시드 파일이 main에 올라가야 기본 URL이 해석됨.

## Self-Review 결과

- **Spec coverage:** 동적 CATALOG(Task 2), turbo 빌트인(Task 2), remote_catalog 모듈/스키마/검증/캐시/TTL/폴백(Task 1), 병합 규칙(Task 2 테스트), API refresh(Task 3), 검증 단일화(Task 3), UI 버튼(Task 4), 시드 JSON(Task 1), 테스트 전 항목(각 Task) — 스펙 요구 전부 매핑됨.
- **Placeholder scan:** 모든 코드 스텝에 실제 코드 포함, "handle errors" 류 없음.
- **Type consistency:** `get_catalog()`/`BUILTIN_CATALOG`/`RemoteModel(name,repo_id,approx_bytes,label)`/`get_remote_models(force=)`/`refreshVideoModels()` 이름·시그니처가 전 Task에서 일관.
