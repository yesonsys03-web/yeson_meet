# 로컬 번역 모델 원격 카탈로그 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 자막메이커 번역 티어(qwen/qwen_lite/qwen_hifi)를 whisper 전사 모델처럼 원격 카탈로그로 추가·오버라이드할 수 있게 하고, 흩어진 하드코딩을 `translate_catalog.get_catalog()` 단일 소스로 통합한다.

**Architecture:** `remote_catalog.py`의 fetch·TTL 캐시·https 가드를 스키마 비의존 코어(`catalog_fetch.py`)로 추출해 whisper와 번역이 공유한다. 번역 티어는 런타임이 둘(실리콘=MLX, 그 외=Ollama)이라 `mlx_repo`+`ollama_tag` 이중 정체성을 갖고, 각 런타임은 optional(최소 한쪽 필수)이다. 소비자(translate_mlx/translate_ollama/translate_cli/translate_models/API/클라 2종)는 상수 대신 카탈로그를 조회한다.

**Tech Stack:** Python 3 / FastAPI / pytest, React + TypeScript / vitest, Tauri

**Spec:** `docs/superpowers/specs/2026-07-15-translate-remote-catalog-design.md`

## Global Constraints

- **최소 패치 원칙** — 요청한 파일만, 임포트 구조 임의 변경 금지 (`CLAUDE.md`).
- **파일 전체 재작성은 금지하되, 다음 두 파일은 명시적 예외다** — `remote_catalog.py`(Task 1)와 `translate_models.py`(Task 5). 둘 다 로직의 대부분이 이동/치환되어 부분 편집이 오히려 더 큰 diff와 어수선한 중간 상태를 낳는다. 이 경우엔 재작성이 곧 최소 패치다. **그 외 모든 파일은 부분 편집만** 한다.
- **앵커 경계 준수** — `translate_mlx.py`는 `ANCHOR: TRANSLATE_MLX_START`~`_END`, `translate_ollama.py`는 `ANCHOR: TRANSLATE_OLLAMA_START`~`_END`, `MlxModelPanel.tsx`는 `ANCHOR: MLX_MODEL_PANEL_START`~`_END` 안에서만 편집.
- **회귀 기준선** — 빌트인 3종의 값이 그대로이므로, 원격 JSON이 빈 초기 상태에서 사용자 눈에 보이는 변화가 없어야 한다.
- **원격 카탈로그는 예외를 절대 전파하지 않는다** — 원격 실패 → 캐시 → 빌트인. 잘못된 항목은 경고 로그 후 스킵(다른 항목은 생존).
- **원격은 추가·오버라이드만 가능, 빌트인 삭제 불가.**
- **번들 SQLite/스키마 무관** — 이 작업은 DB를 건드리지 않는다.
- **테스트 실행**: 서버 `uv run pytest apps/server/tests/...` (root `pyproject.toml` testpaths가 `apps/server/tests`만 가리킨다), 클라 `pnpm --dir apps/desktop test`, 서버 콘솔 `pnpm --dir apps/server_desktop test`.
- **커밋 메시지 말미**: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

## File Structure

**신규**
- `apps/server/domain/video_captions/catalog_fetch.py` — 스키마 비의존 원격 카탈로그 코어(네트워크·TTL 캐시·https 가드·폴백)
- `apps/server/domain/video_captions/translate_catalog.py` — 번역 티어 단일 진실(빌트인 + 원격 오버레이 + 런타임 판정)
- `apps/server/domain/video_captions/translate_catalog.remote.json` — 원격 카탈로그 원본(리포 main)
- `apps/server/tests/test_translate_catalog.py`
- `apps/server/tests/test_api_translate_models.py`
- `apps/server_desktop/src/translateCatalogAdmin.ts` — 라이브 패널용 카탈로그 조회 + 빌트인 폴백
- `apps/server_desktop/src/translateCatalogAdmin.test.ts`

**수정**
- `apps/server/domain/video_captions/remote_catalog.py` — 코어에 위임(공개 API 불변)
- `apps/server/domain/video_captions/translate_ollama.py` — `_QWEN_OLLAMA_DEFAULTS` 제거
- `apps/server/domain/video_captions/translate_mlx.py` — `QWEN_MLX_MODELS` 제거
- `apps/server/domain/video_captions/translate_cli.py` — `list_translate_engines`·`create_translator` 파생
- `apps/server/domain/video_captions/translate_models.py` — `_TIERS` 제거, 런타임 가드
- `apps/server/api/v1/translate_models.py` — `?refresh`, 미지원 런타임 409
- `apps/server/tests/test_remote_catalog.py` — 패치 대상 재지정
- `apps/desktop/src/console/videoApi.ts` / `VideoCaptionPanel.tsx` — 새로고침 버튼, `reason`
- `apps/server_desktop/src/setup/serverConfig.ts` — `MLX_MODELS` 이관
- `apps/server_desktop/src/setup/MlxModelPanel.tsx` — 카탈로그 소비 + `port` prop
- `apps/server_desktop/src/setup/ServerConfigPanel.tsx` / `ServerConsole.tsx` — `port` 배선

---

### Task 1: `catalog_fetch.py` 코어 추출 (whisper 동작 불변)

순수 리팩터링. 기존 `test_remote_catalog.py`가 안전망이므로, **whisper 동작이 그대로 통과하는 것이 이 태스크의 합격 조건**이다.

**Files:**
- Create: `apps/server/domain/video_captions/catalog_fetch.py`
- Modify: `apps/server/domain/video_captions/remote_catalog.py` (전체 재작성 — 로직이 코어로 이동)
- Test: `apps/server/tests/test_remote_catalog.py` (패치 대상 재지정)

**Interfaces:**
- Produces:
  - `catalog_fetch.CACHE_TTL_SECONDS: int`
  - `catalog_fetch.valid_name(name: object) -> bool`
  - `catalog_fetch.cached_entries(cache_path_fn: Callable[[], Path], parse_fn: Callable[[object], list]) -> list`
  - `catalog_fetch.get_entries(url_fn: Callable[[], str], cache_path_fn: Callable[[], Path], parse_fn: Callable[[object], list], dump_fn: Callable[[object], dict], force: bool = False) -> list`
  - `catalog_fetch._http_get(url: str) -> str` / `catalog_fetch._now() -> float` (test seam — 테스트는 **여기를** 몽키패치한다)
  - `remote_catalog` 공개 API 불변: `RemoteModel`, `cached_models()`, `get_remote_models(force)`, `CACHE_TTL_SECONDS`, `CATALOG_URL_ENV`

- [ ] **Step 1: 기존 테스트의 패치 대상을 `catalog_fetch`로 재지정 (실패하는 테스트 만들기)**

`apps/server/tests/test_remote_catalog.py`의 import에 코어를 추가한다:

```python
from apps.server.domain.video_captions import catalog_fetch as cf
from apps.server.domain.video_captions import remote_catalog as rc
```

그리고 파일 안의 **모든** `monkeypatch.setattr(rc, "_http_get", ...)` → `monkeypatch.setattr(cf, "_http_get", ...)`, `monkeypatch.setattr(rc, "_now", ...)` → `monkeypatch.setattr(cf, "_now", ...)`로 바꾼다 (총 10개 테스트에 걸쳐 있음). `rc.CACHE_TTL_SECONDS`·`rc.CATALOG_URL_ENV` 참조는 **그대로 둔다** (re-export로 유지되므로).

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest apps/server/tests/test_remote_catalog.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.server.domain.video_captions.catalog_fetch'`

- [ ] **Step 3: `catalog_fetch.py` 작성**

```python
"""원격 카탈로그 fetch/캐시 코어 — 스키마 비의존.

whisper(remote_catalog.py)와 번역(translate_catalog.py)이 공유한다. 스키마는
parse_fn/dump_fn 주입으로 분리하고, 이 모듈은 네트워크·TTL 캐시·https 가드·
폴백만 책임진다.

URL과 캐시 경로를 값이 아니라 **함수**로 받는다 — 둘 다 STORAGE_ROOT 등 환경변수와
소비자 모듈에 의존해 import 시점에 확정할 수 없다(호출을 늦춰 순환 import도 피한다).
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger("yeson.video.catalog_fetch")

CACHE_TTL_SECONDS = 6 * 3600
NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _now() -> float:  # test seam
    return time.time()


def _http_get(url: str) -> str:  # test seam
    import requests

    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return resp.text


def valid_name(name: object) -> bool:
    """카탈로그 항목 이름 — 경로 조각으로 쓰이므로 정규식 + 점 이름을 거부한다."""
    return (isinstance(name, str) and bool(NAME_RE.match(name))
            and name not in (".", ".."))


def _read_cache(cache_path_fn: Callable[[], Path],
                parse_fn: Callable[[object], list]) -> tuple[float, list] | None:
    path = cache_path_fn()
    if not path.is_file():
        return None
    try:
        blob = json.loads(path.read_text("utf-8"))
        fetched_at = float(blob.get("fetched_at", 0))
        return fetched_at, parse_fn(blob)
    except Exception as exc:  # 손상된 캐시는 없는 것으로 취급
        logger.warning("catalog_fetch: bad cache file %s: %s", path, exc)
        return None


def _write_cache(cache_path_fn: Callable[[], Path], entries: list,
                 dump_fn: Callable[[object], dict]) -> None:
    path = cache_path_fn()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": _now(), "models": [dump_fn(e) for e in entries]}
    path.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")


def cached_entries(cache_path_fn: Callable[[], Path],
                   parse_fn: Callable[[object], list]) -> list:
    """디스크 캐시만 읽어 반환(네트워크 없음·예외 없음)."""
    try:
        cached = _read_cache(cache_path_fn, parse_fn)
    except Exception as exc:
        logger.warning("catalog_fetch: cache read failed: %s", exc)
        return []
    return cached[1] if cached else []


def get_entries(url_fn: Callable[[], str], cache_path_fn: Callable[[], Path],
                parse_fn: Callable[[object], list],
                dump_fn: Callable[[object], dict], force: bool = False) -> list:
    """검증된 원격 항목. 네트워크/파싱 실패 시 캐시→빈 목록 폴백(예외 없음)."""
    try:
        cached = _read_cache(cache_path_fn, parse_fn)
    except Exception as exc:  # 캐시 읽기 실패도 "캐시 없음" — 절대 raise 금지
        logger.warning("catalog_fetch: cache read failed: %s", exc)
        cached = None
    if not force and cached is not None:
        fetched_at, entries = cached
        if _now() - fetched_at < CACHE_TTL_SECONDS:
            return entries
    url = url_fn()
    if not url.startswith("https://"):
        logger.warning("catalog_fetch: non-https url ignored: %s", url)
        return cached[1] if cached else []
    try:
        text = _http_get(url)
        entries = parse_fn(json.loads(text))
        _write_cache(cache_path_fn, entries, dump_fn)
        return entries
    except Exception as exc:
        logger.warning("catalog_fetch: fetch failed (%s); using cache/builtin", exc)
        return cached[1] if cached else []
```

- [ ] **Step 4: `remote_catalog.py`를 코어 위임으로 재작성**

파일 전체를 다음으로 교체한다(로직이 코어로 이동했으므로 재작성이 최소 패치다):

```python
"""Remote whisper catalog overlay — fetch/validate/cache an optional model list.

새 whisper 모델을 앱 재배포 없이 추가하기 위한 오버레이. 리포 main의
``whisper_catalog.remote.json``을 TTL 캐시로 fetch해 빌트인에 병합한다
(병합은 whisper_models.get_catalog()). 빌트인은 항상 유지되는 baseline이며,
원격은 추가/오버라이드만 한다.

네트워크·캐시·TTL은 catalog_fetch 코어가 담당하고, 이 모듈은 whisper 스키마
(name/repo_id/approx_bytes/label)의 검증만 책임진다.
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from . import catalog_fetch

logger = logging.getLogger("yeson.video.remote_catalog")

DEFAULT_CATALOG_URL = (
    "https://raw.githubusercontent.com/yesonsys03-web/yeson_meet/main/"
    "apps/server/domain/video_captions/whisper_catalog.remote.json"
)
CATALOG_URL_ENV = "YESON_WHISPER_CATALOG_URL"
# 하위호환 re-export — 기존 호출부/테스트가 remote_catalog.CACHE_TTL_SECONDS를 참조한다.
CACHE_TTL_SECONDS = catalog_fetch.CACHE_TTL_SECONDS


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
        if (not catalog_fetch.valid_name(name)
                or not isinstance(repo_id, str) or not repo_id
                or not isinstance(approx, int) or isinstance(approx, bool) or approx <= 0
                or not isinstance(label, str)):
            logger.warning("remote_catalog: skip invalid entry: %r", item)
            continue
        out.append(RemoteModel(name=name, repo_id=repo_id, approx_bytes=approx, label=label))
    return out


def cached_models() -> list[RemoteModel]:
    """디스크 캐시만 읽어 반환(네트워크 없음·예외 없음). get_catalog() 병합용."""
    return catalog_fetch.cached_entries(_cache_path, _parse)


def get_remote_models(force: bool = False) -> list[RemoteModel]:
    """원격 카탈로그의 검증된 모델 목록. 실패 시 캐시→빈 목록 폴백(예외 없음)."""
    return catalog_fetch.get_entries(_catalog_url, _cache_path, _parse, asdict, force)
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `uv run pytest apps/server/tests/test_remote_catalog.py apps/server/tests/test_video_whisper_models.py apps/server/tests/test_api_video_models.py -q`
Expected: PASS — 전부 통과(whisper 동작 불변 증명)

- [ ] **Step 6: 커밋**

```bash
git add apps/server/domain/video_captions/catalog_fetch.py \
        apps/server/domain/video_captions/remote_catalog.py \
        apps/server/tests/test_remote_catalog.py
git commit -m "$(cat <<'EOF'
refactor(video): 원격 카탈로그 fetch/캐시를 catalog_fetch 코어로 추출

번역 카탈로그가 같은 네트워크·TTL·https 가드·폴백을 필요로 한다. 스키마는
parse_fn/dump_fn 주입으로 분리하고 코어는 전송만 책임진다. whisper 공개 API와
캐시 파일명은 불변 — 기존 테스트가 그대로 통과하는 것이 안전망이다.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `translate_catalog.py` — 번역 티어 단일 진실

**Files:**
- Create: `apps/server/domain/video_captions/translate_catalog.py`
- Create: `apps/server/domain/video_captions/translate_catalog.remote.json`
- Test: `apps/server/tests/test_translate_catalog.py`

**Interfaces:**
- Consumes: `catalog_fetch.get_entries/cached_entries/valid_name` (Task 1)
- Produces:
  - `TranslateModel(name: str, label: str, mlx_repo: str | None, mlx_bytes: int, ollama_tag: str | None, ollama_bytes: int)` (frozen dataclass)
  - `BUILTIN: dict[str, TranslateModel]`
  - `get_catalog() -> dict[str, TranslateModel]` — 빌트인 + 원격(디스크 캐시) 오버레이, 네트워크 없음
  - `get_remote_models(force: bool = False) -> list[TranslateModel]` — 네트워크
  - `runtime() -> str` — `"mlx"` | `"ollama"`
  - `unsupported_reason(entry: TranslateModel) -> str | None`
  - `ollama_env_key(name: str) -> str`
  - `CATALOG_URL_ENV = "YESON_TRANSLATE_CATALOG_URL"`

- [ ] **Step 1: 실패하는 테스트 작성**

Create `apps/server/tests/test_translate_catalog.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.server.domain.video_captions import catalog_fetch as cf
from apps.server.domain.video_captions import translate_catalog as tc


@pytest.fixture(autouse=True)
def _storage(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    yield


def _payload(*models: dict) -> str:
    return json.dumps({"version": 1, "models": list(models)})


BOTH = {"name": "qwen_next", "label": "Qwen 12B (로컬)",
        "mlx_repo": "mlx-community/Qwen3.6-12B-4bit", "mlx_bytes": 7_000_000_000,
        "ollama_tag": "qwen3.6:12b", "ollama_bytes": 9_000_000_000}
MLX_ONLY = {"name": "qwen_mlxonly", "label": "MLX 전용",
            "mlx_repo": "mlx-community/X-4bit", "mlx_bytes": 1_000}
OLLAMA_ONLY = {"name": "qwen_ollamaonly", "label": "Ollama 전용",
               "ollama_tag": "x:1b", "ollama_bytes": 1_000}


def _fetch(monkeypatch, *models: dict) -> None:
    monkeypatch.setattr(cf, "_http_get", lambda url: _payload(*models))
    monkeypatch.setattr(cf, "_now", lambda: 1000.0)
    tc.get_remote_models(force=True)


def test_cache_path_is_directly_under_storage_root(tmp_path: Path):
    # mlx_models/ 밑이 아니다 — MLX가 없는 윈도우 서버에 MLX 디렉터리를 만들지 않는다.
    assert tc._cache_path() == tmp_path / "translate_catalog.cache.json"


def test_builtin_only_when_no_remote():
    assert set(tc.get_catalog()) == {"qwen", "qwen_lite", "qwen_hifi"}


def test_remote_adds_new_tier(monkeypatch):
    _fetch(monkeypatch, BOTH)
    cat = tc.get_catalog()
    assert cat["qwen_next"].mlx_repo == "mlx-community/Qwen3.6-12B-4bit"
    assert cat["qwen_next"].ollama_tag == "qwen3.6:12b"
    assert cat["qwen_next"].ollama_bytes == 9_000_000_000


def test_remote_overrides_builtin(monkeypatch):
    _fetch(monkeypatch, {**BOTH, "name": "qwen", "label": "덮어씀"})
    assert tc.get_catalog()["qwen"].label == "덮어씀"


def test_remote_cannot_delete_builtin(monkeypatch):
    _fetch(monkeypatch, BOTH)
    assert {"qwen", "qwen_lite", "qwen_hifi"} <= set(tc.get_catalog())


def test_single_runtime_entries_are_valid(monkeypatch):
    _fetch(monkeypatch, MLX_ONLY, OLLAMA_ONLY)
    cat = tc.get_catalog()
    assert cat["qwen_mlxonly"].ollama_tag is None
    assert cat["qwen_mlxonly"].ollama_bytes == 0      # 없는 쪽은 0으로 정규화
    assert cat["qwen_ollamaonly"].mlx_repo is None
    assert cat["qwen_ollamaonly"].mlx_bytes == 0


def test_skips_malformed_entries_keeps_valid(monkeypatch):
    bad = [
        {"name": "no_runtime", "label": "x"},                                    # 양쪽 다 없음
        {"name": "bad name!", "label": "x", "ollama_tag": "a:1", "ollama_bytes": 1},
        {"name": ".", "label": "x", "ollama_tag": "a:1", "ollama_bytes": 1},
        {"name": "..", "label": "x", "ollama_tag": "a:1", "ollama_bytes": 1},
        {"name": "neg", "label": "x", "ollama_tag": "a:1", "ollama_bytes": -1},  # 음수
        {"name": "boolbytes", "label": "x", "ollama_tag": "a:1", "ollama_bytes": True},
        {"name": "nolabel", "ollama_tag": "a:1", "ollama_bytes": 1},             # label 없음
        "not-a-dict",
    ]
    _fetch(monkeypatch, BOTH, *bad)
    cat = tc.get_catalog()
    assert "qwen_next" in cat
    for name in ("no_runtime", "neg", "boolbytes", "nolabel", "."):
        assert name not in cat


def test_ollama_env_key_reproduces_existing_keys():
    # 하위호환 — 기존 3키를 규칙이 그대로 재현해야 한다.
    assert tc.ollama_env_key("qwen") == "YESON_OLLAMA_QWEN_MODEL"
    assert tc.ollama_env_key("qwen_lite") == "YESON_OLLAMA_QWEN_LITE_MODEL"
    assert tc.ollama_env_key("qwen_hifi") == "YESON_OLLAMA_QWEN_HIFI_MODEL"


def test_unsupported_reason_on_silicon(monkeypatch):
    monkeypatch.setattr(tc, "_is_apple_silicon_mac", lambda: True)
    assert tc.runtime() == "mlx"
    assert tc.unsupported_reason(tc.BUILTIN["qwen"]) is None
    ollama_only = tc.TranslateModel("x", "x", None, 0, "x:1b", 10)
    assert tc.unsupported_reason(ollama_only) == "Ollama 전용"


def test_unsupported_reason_off_silicon(monkeypatch):
    monkeypatch.setattr(tc, "_is_apple_silicon_mac", lambda: False)
    assert tc.runtime() == "ollama"
    assert tc.unsupported_reason(tc.BUILTIN["qwen"]) is None
    mlx_only = tc.TranslateModel("x", "x", "a/b", 10, None, 0)
    assert tc.unsupported_reason(mlx_only) == "실리콘맥 전용"
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest apps/server/tests/test_translate_catalog.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named '...translate_catalog'`

- [ ] **Step 3: `translate_catalog.py` 작성**

```python
"""로컬 번역 모델(Qwen 티어) 카탈로그 — 단일 진실.

티어 정의(MLX 리포·Ollama 태그·크기·라벨)의 유일한 출처. whisper 전사 모델과 같은
원격 오버레이(catalog_fetch)를 쓰되, 번역 티어는 런타임이 둘(실리콘맥=MLX, 그 외=
윈도·인텔맥=Ollama)이라 정체성이 이중이다 — repo_id 하나로 표현되지 않는다.

양쪽 런타임은 optional이고 최소 한쪽은 있어야 한다. 현재 서버가 지원하지 않는
런타임의 티어는 목록에서 빼지 않고 unsupported_reason()으로 '보이되 비활성'
처리한다(video_models.py의 Apple 항목과 동일 정책 — 플랫폼별로 항목이 사라지는
비대칭을 만들지 않는다).

원격은 추가·오버라이드만 가능하고 빌트인 삭제는 불가하다.
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from apps.server.ai.apple_native import _is_apple_silicon_mac

from . import catalog_fetch

logger = logging.getLogger("yeson.video.translate_catalog")

STORAGE_ROOT_ENV = "STORAGE_ROOT"
DEFAULT_STORAGE_ROOT = "/var/lib/yeson-meet/storage"

DEFAULT_CATALOG_URL = (
    "https://raw.githubusercontent.com/yesonsys03-web/yeson_meet/main/"
    "apps/server/domain/video_captions/translate_catalog.remote.json"
)
CATALOG_URL_ENV = "YESON_TRANSLATE_CATALOG_URL"


@dataclass(frozen=True)
class TranslateModel:
    name: str          # 드롭다운 provider 값 = create_translator 라우팅 키
    label: str
    mlx_repo: str | None
    mlx_bytes: int     # mlx_repo가 없으면 0
    ollama_tag: str | None
    ollama_bytes: int  # ollama_tag가 없으면 0


BUILTIN: dict[str, TranslateModel] = {
    "qwen": TranslateModel(
        "qwen", "Qwen 9B (로컬)",
        "mlx-community/Qwen3.5-9B-4bit", 5_000_000_000,
        "qwen3.5:9b", 6_600_000_000),
    "qwen_lite": TranslateModel(
        "qwen_lite", "Qwen 4B (로컬·빠름)",
        "mlx-community/Qwen3.5-4B-4bit", 2_300_000_000,
        "qwen3.5:4b", 3_400_000_000),
    "qwen_hifi": TranslateModel(
        "qwen_hifi", "Qwen 9B (로컬·고품질 8bit)",
        "mlx-community/Qwen3.5-9B-8bit", 10_000_000_000,
        "qwen3.5:9b-q8_0", 10_000_000_000),
}


def _catalog_url() -> str:
    return os.environ.get(CATALOG_URL_ENV, DEFAULT_CATALOG_URL)


def _cache_path() -> Path:
    # STORAGE_ROOT 직하 — 번역엔 대응하는 단일 모델 디렉터리가 없다(MLX는 mlx_models/,
    # Ollama는 Ollama 자체 저장소). mlx_models/ 밑에 두면 MLX가 없는 윈도우 서버에
    # mlx_models 디렉터리가 생긴다.
    root = os.environ.get(STORAGE_ROOT_ENV, DEFAULT_STORAGE_ROOT)
    return Path(root) / "translate_catalog.cache.json"


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _pos_int(value: object) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return None
    return value


def _parse(payload: object) -> list[TranslateModel]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("models")
    if not isinstance(raw, list):
        return []
    out: list[TranslateModel] = []
    for item in raw:
        if not isinstance(item, dict):
            logger.warning("translate_catalog: skip non-dict entry: %r", item)
            continue
        name = item.get("name")
        label = item.get("label")
        mlx_repo = _opt_str(item.get("mlx_repo"))
        ollama_tag = _opt_str(item.get("ollama_tag"))
        if not catalog_fetch.valid_name(name) or not isinstance(label, str):
            logger.warning("translate_catalog: skip invalid entry: %r", item)
            continue
        if mlx_repo is None and ollama_tag is None:
            logger.warning(
                "translate_catalog: entry has neither mlx_repo nor ollama_tag: %r", item)
            continue
        mlx_bytes = _pos_int(item.get("mlx_bytes")) if mlx_repo else 0
        ollama_bytes = _pos_int(item.get("ollama_bytes")) if ollama_tag else 0
        if mlx_bytes is None or ollama_bytes is None:
            logger.warning("translate_catalog: skip entry with invalid *_bytes: %r", item)
            continue
        out.append(TranslateModel(
            name=name, label=label,
            mlx_repo=mlx_repo, mlx_bytes=mlx_bytes,
            ollama_tag=ollama_tag, ollama_bytes=ollama_bytes))
    return out


def get_remote_models(force: bool = False) -> list[TranslateModel]:
    """원격 카탈로그의 검증된 티어 목록. 실패 시 캐시→빈 목록 폴백(예외 없음)."""
    return catalog_fetch.get_entries(_catalog_url, _cache_path, _parse, asdict, force)


def get_catalog() -> dict[str, TranslateModel]:
    """빌트인 baseline에 원격(디스크 캐시)을 오버레이한 유효 티어 목록.

    네트워크는 타지 않는다 — 원격 갱신은 /translate-models 엔드포인트가 담당한다.
    """
    merged = dict(BUILTIN)
    for m in catalog_fetch.cached_entries(_cache_path, _parse):
        merged[m.name] = m
    return merged


def runtime() -> str:
    """이 서버에서 로컬 번역이 쓸 런타임 — 실리콘=mlx, 그 외(윈도·인텔맥)=ollama."""
    return "mlx" if _is_apple_silicon_mac() else "ollama"


def unsupported_reason(entry: TranslateModel) -> str | None:
    """이 서버의 런타임을 지원하지 않는 티어면 사유, 아니면 None.

    None이면 '설치만 하면 쓸 수 있음'이고, 값이 있으면 '이 기기에선 다운로드해도
    소용없음'이다 — 클라는 후자에만 다운로드 버튼을 비활성화한다.
    """
    if runtime() == "mlx":
        return None if entry.mlx_repo else "Ollama 전용"
    return None if entry.ollama_tag else "실리콘맥 전용"


def ollama_env_key(name: str) -> str:
    """티어별 Ollama 태그 오버라이드 env 키.

    규칙이 기존 3키(YESON_OLLAMA_QWEN_MODEL / _QWEN_LITE_ / _QWEN_HIFI_)를 그대로
    재현하므로 하위호환이 유지된다.
    """
    return f"YESON_OLLAMA_{name.upper()}_MODEL"
```

- [ ] **Step 4: 원격 카탈로그 원본 파일 생성**

Create `apps/server/domain/video_captions/translate_catalog.remote.json`:

```json
{
  "version": 1,
  "models": []
}
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `uv run pytest apps/server/tests/test_translate_catalog.py -q`
Expected: PASS — 10개 테스트 전부 통과

- [ ] **Step 6: 커밋**

```bash
git add apps/server/domain/video_captions/translate_catalog.py \
        apps/server/domain/video_captions/translate_catalog.remote.json \
        apps/server/tests/test_translate_catalog.py
git commit -m "$(cat <<'EOF'
feat(video): 번역 티어 단일 소스 translate_catalog + 원격 오버레이

빌트인 3종을 baseline으로 두고 리포 main의 translate_catalog.remote.json을
오버레이한다. 런타임이 둘이라 mlx_repo/ollama_tag 이중 정체성이며 각각 optional
(최소 한쪽 필수). 캐시는 STORAGE_ROOT 직하 — mlx_models/ 밑에 두면 MLX가 없는
윈도우 서버에 MLX 디렉터리가 생긴다.

아직 소비자는 없다(후속 커밋에서 하드코딩 상수를 이 카탈로그로 대체).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `translate_ollama` 파생

`qwen_ollama_model` 시그니처가 그대로라 호출부 파급이 없다.

**Files:**
- Modify: `apps/server/domain/video_captions/translate_ollama.py:32-57` (앵커 내부)
- Test: `apps/server/tests/test_video_translate_ollama.py`

**Interfaces:**
- Consumes: `translate_catalog.get_catalog()`, `translate_catalog.ollama_env_key(name)` (Task 2)
- Produces: `qwen_ollama_model(provider: str) -> str | None` — 시그니처 불변. `ollama_tag`가 없는 티어는 `None`

- [ ] **Step 1: 실패하는 테스트 추가**

`apps/server/tests/test_video_translate_ollama.py` 끝에 추가:

```python
def test_qwen_ollama_model_reads_catalog(monkeypatch):
    from apps.server.domain.video_captions import catalog_fetch as cf
    from apps.server.domain.video_captions import translate_ollama as to

    monkeypatch.setattr(cf, "cached_entries", lambda *a, **k: [])
    assert to.qwen_ollama_model("qwen") == "qwen3.5:9b"
    assert to.qwen_ollama_model("qwen_lite") == "qwen3.5:4b"
    assert to.qwen_ollama_model("qwen_hifi") == "qwen3.5:9b-q8_0"
    assert to.qwen_ollama_model("nope") is None


def test_qwen_ollama_model_env_override(monkeypatch):
    from apps.server.domain.video_captions import translate_ollama as to

    monkeypatch.setenv("YESON_OLLAMA_QWEN_LITE_MODEL", "qwen3.6:4b")
    assert to.qwen_ollama_model("qwen_lite") == "qwen3.6:4b"


def test_qwen_ollama_model_none_for_mlx_only_tier(monkeypatch):
    from apps.server.domain.video_captions import translate_catalog as tc
    from apps.server.domain.video_captions import translate_ollama as to

    mlx_only = tc.TranslateModel("qwen_x", "x", "a/b", 10, None, 0)
    monkeypatch.setattr(tc, "get_catalog", lambda: {"qwen_x": mlx_only})
    assert to.qwen_ollama_model("qwen_x") is None
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest apps/server/tests/test_video_translate_ollama.py -q -k "uses_catalog_tag_not_hardcoded or serves_remote_added_tier"`
Expected: FAIL — 2개 실패. 구 구현은 카탈로그를 아예 조회하지 않으므로 오버라이드된 태그(`qwen9.9:catalog-only`) 대신 하드코딩 `qwen3.5:9b`를 반환하고, 원격 추가 티어(`qwen_next`)에는 `None`을 반환한다.

> **주의 — 위 Step 1의 세 테스트만으로는 RED가 성립하지 않는다.** 구 구현의 `_QWEN_OLLAMA_DEFAULTS.get(provider)`는 미지 provider에 KeyError가 아니라 **`None`을 반환**하고, 기본 태그·env 오버라이드 동작도 신 구현과 값이 같다. 즉 세 테스트는 구·신 양쪽에서 통과해 리팩터링을 검증하지 못한다(하드코딩으로 회귀해도 못 잡는다). **구·신을 가르는 테스트 2개를 반드시 함께 추가한다** — 카탈로그가 빌트인과 *다른* 태그를 줄 때 그 값이 나오는지, 그리고 원격이 추가한 새 티어의 태그가 나오는지.

```python
def test_qwen_ollama_model_uses_catalog_tag_not_hardcoded(monkeypatch):
    """카탈로그가 태그의 단일 출처임을 증명 — 하드코딩 상수로 회귀하면 실패한다."""
    from apps.server.domain.video_captions import translate_catalog as tc
    from apps.server.domain.video_captions import translate_ollama as to

    overridden = tc.TranslateModel(
        "qwen", "Qwen 9B (로컬)", "mlx-community/Qwen3.5-9B-4bit", 10,
        "qwen9.9:catalog-only", 20)
    monkeypatch.setattr(tc, "get_catalog", lambda: {"qwen": overridden})
    monkeypatch.delenv("YESON_OLLAMA_QWEN_MODEL", raising=False)
    assert to.qwen_ollama_model("qwen") == "qwen9.9:catalog-only"


def test_qwen_ollama_model_serves_remote_added_tier(monkeypatch):
    """원격 카탈로그가 추가한 새 티어도 태그를 돌려준다(이 기능의 존재 이유)."""
    from apps.server.domain.video_captions import translate_catalog as tc
    from apps.server.domain.video_captions import translate_ollama as to

    extra = tc.TranslateModel("qwen_next", "Qwen 12B (로컬)", None, 0, "qwen3.6:12b", 30)
    monkeypatch.setattr(tc, "get_catalog", lambda: {"qwen_next": extra})
    monkeypatch.delenv("YESON_OLLAMA_QWEN_NEXT_MODEL", raising=False)
    assert to.qwen_ollama_model("qwen_next") == "qwen3.6:12b"
```

- [ ] **Step 3: 구현 — 상수 제거 후 카탈로그 조회**

`translate_ollama.py`에서 `_QWEN_OLLAMA_DEFAULTS` 블록(32-37행)을 **삭제**하고, `qwen_ollama_model`(51-57행)을 교체한다:

```python
def qwen_ollama_model(provider: str) -> str | None:
    """provider 값 → 실제 Ollama 태그(env 오버라이드 적용). 미지원 값은 None."""
    from .translate_catalog import get_catalog, ollama_env_key

    entry = get_catalog().get(provider)
    if entry is None or not entry.ollama_tag:
        return None
    return (os.environ.get(ollama_env_key(provider)) or "").strip() or entry.ollama_tag
```

모듈 docstring의 11-15행에서 티어 출처를 갱신한다 — `translate_mlx.QWEN_MLX_MODELS와 공유하며` → `translate_catalog.get_catalog()가 단일 출처이며`, `각 티어의 env(YESON_OLLAMA_QWEN_MODEL 등)로 태그만 교체` → `각 티어의 env(YESON_OLLAMA_{티어}_MODEL)로 태그만 교체하거나 원격 카탈로그로 티어를 추가`.

`translate_catalog`는 **함수 내 지연 import**다 — 이 모듈의 httpx 지연 import 관례와 같고, 모듈 로드 순서 결합을 만들지 않는다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest apps/server/tests/test_video_translate_ollama.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add apps/server/domain/video_captions/translate_ollama.py \
        apps/server/tests/test_video_translate_ollama.py
git commit -m "$(cat <<'EOF'
refactor(video): Ollama 태그를 translate_catalog에서 파생

_QWEN_OLLAMA_DEFAULTS 제거. env 키는 YESON_OLLAMA_{티어}_MODEL 규칙으로
파생하며 기존 3키를 그대로 재현한다(하위호환). ollama_tag가 없는 티어는 None.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `translate_cli` 파생 — `create_translator` + `list_translate_engines`

이 태스크는 `QWEN_MLX_MODELS`를 **삭제하지 않는다**(Task 6에서 미참조가 된 뒤 정리). 트리를 항상 초록으로 유지하기 위함이다.

**Files:**
- Modify: `apps/server/domain/video_captions/translate_cli.py:94-128` (`list_translate_engines`), `:264-307` (`create_translator`)
- Test: `apps/server/tests/test_video_translate_cli.py`

**Interfaces:**
- Consumes: `translate_catalog.get_catalog()`, `unsupported_reason(entry)` (Task 2)
- Produces:
  - `list_translate_engines() -> list[dict]` — 각 원소는 `{"value": str, "label": str, "available": bool}`이며 카탈로그 티어는 `"reason": str | None`을 추가로 갖는다
  - `create_translator(provider, cli_model)` — 카탈로그에 있는 이름은 MLX→Ollama 순으로 라우팅

- [ ] **Step 1: 실패하는 테스트 추가**

`apps/server/tests/test_video_translate_cli.py` 끝에 추가:

```python
def test_engines_include_remote_tier(monkeypatch):
    from apps.server.domain.video_captions import translate_catalog as tc
    from apps.server.domain.video_captions import translate_cli as tcli

    extra = tc.TranslateModel("qwen_next", "Qwen 12B (로컬)", "a/b", 10, "x:12b", 10)
    monkeypatch.setattr(tc, "get_catalog", lambda: {**tc.BUILTIN, "qwen_next": extra})
    monkeypatch.setattr(tc, "_is_apple_silicon_mac", lambda: False)
    values = [e["value"] for e in tcli.list_translate_engines()]
    assert "qwen_next" in values
    assert values.index("gemini") == 0  # 정적 엔진 순서 유지


def test_engines_reason_none_for_supported_but_uninstalled(monkeypatch):
    from apps.server.domain.video_captions import translate_catalog as tc
    from apps.server.domain.video_captions import translate_cli as tcli

    monkeypatch.setattr(tc, "_is_apple_silicon_mac", lambda: False)
    monkeypatch.setattr(
        "apps.server.domain.video_captions.translate_ollama.qwen_ollama_available",
        lambda tag: False)
    monkeypatch.setattr(
        "apps.server.domain.video_captions.translate_mlx.qwen_mlx_available",
        lambda repo: False)
    qwen = next(e for e in tcli.list_translate_engines() if e["value"] == "qwen")
    assert qwen["available"] is False   # 미설치
    assert qwen["reason"] is None       # 그러나 다운로드하면 쓸 수 있다


def test_engines_reason_set_for_unsupported_runtime(monkeypatch):
    from apps.server.domain.video_captions import translate_catalog as tc
    from apps.server.domain.video_captions import translate_cli as tcli

    mlx_only = tc.TranslateModel("qwen_x", "MLX 전용", "a/b", 10, None, 0)
    monkeypatch.setattr(tc, "get_catalog", lambda: {"qwen_x": mlx_only})
    monkeypatch.setattr(tc, "_is_apple_silicon_mac", lambda: False)  # 윈도·인텔맥
    entry = next(e for e in tcli.list_translate_engines() if e["value"] == "qwen_x")
    assert entry["reason"] == "실리콘맥 전용"
    assert entry["available"] is False


def test_engines_no_crash_for_ollama_only_tier_on_silicon(monkeypatch):
    # mlx_repo=None을 qwen_mlx_available에 넘기면 mlx_model_installed가
    # None.replace()로 터진다 — 실리콘맥에서만 재현되는 크래시.
    from apps.server.domain.video_captions import translate_catalog as tc
    from apps.server.domain.video_captions import translate_cli as tcli

    ollama_only = tc.TranslateModel("qwen_y", "Ollama 전용", None, 0, "y:1b", 10)
    monkeypatch.setattr(tc, "get_catalog", lambda: {"qwen_y": ollama_only})
    monkeypatch.setattr(tc, "_is_apple_silicon_mac", lambda: True)
    entry = next(e for e in tcli.list_translate_engines() if e["value"] == "qwen_y")
    assert entry["reason"] == "Ollama 전용"


def test_create_translator_routes_remote_tier_to_ollama(monkeypatch):
    from apps.server.domain.video_captions import translate_catalog as tc
    from apps.server.domain.video_captions import translate_cli as tcli
    from apps.server.domain.video_captions.translate_ollama import OllamaTranslator

    extra = tc.TranslateModel("qwen_next", "Qwen 12B", "a/b", 10, "x:12b", 10)
    monkeypatch.setattr(tc, "get_catalog", lambda: {"qwen_next": extra})
    monkeypatch.setattr(
        "apps.server.domain.video_captions.translate_mlx.qwen_mlx_available",
        lambda repo: False)
    monkeypatch.setattr(
        "apps.server.domain.video_captions.translate_ollama.qwen_ollama_available",
        lambda tag: True)
    t = tcli.create_translator("qwen_next")
    assert isinstance(t, OllamaTranslator)
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest apps/server/tests/test_video_translate_cli.py -q -k "remote_tier or reason or ollama_only"`
Expected: FAIL — `KeyError: 'reason'` / `qwen_next`가 목록에 없음

- [ ] **Step 3: `list_translate_engines` 교체 (94-128행)**

```python
def list_translate_engines() -> list[dict]:
    """클라 드롭다운용 — 서버에서 사용 가능한 번역 엔진과 설치 여부.

    qwen 계열 티어는 translate_catalog가 단일 출처다(빌트인 + 원격 오버레이).
    라벨에서 MLX를 노출하지 않는다 — 런타임 선택은 create_translator가 자동.

    available: 지금 실제로 선택 가능한가(설치 여부).
    reason: 이 서버의 런타임을 아예 지원하지 않는 티어에만 채워진다. reason이
      None인데 available=False면 그냥 미설치이므로 다운로드하면 쓸 수 있다.
    """
    from .translate_catalog import get_catalog, unsupported_reason
    from .translate_mlx import qwen_mlx_available
    from .translate_ollama import qwen_ollama_available, qwen_ollama_model

    def _qwen_available(entry) -> bool:
        # mlx_repo가 None이면 qwen_mlx_available에 넘기지 않는다 — 실리콘맥에서
        # mlx_model_installed(None)이 None.replace()로 터진다.
        if entry.mlx_repo and qwen_mlx_available(entry.mlx_repo):
            return True
        return qwen_ollama_available(qwen_ollama_model(entry.name))

    engines = [
        {"value": "gemini", "label": "Gemini",
         "available": bool(os.environ.get("GEMINI_API_KEY"))},
        {"value": "claude", "label": "Claude 구독",
         "available": resolve_cli("claude") is not None},
        {"value": "codex", "label": "Codex 구독",
         "available": resolve_cli("codex") is not None},
        {"value": "agy", "label": "Antigravity",
         "available": resolve_cli("agy") is not None},
        {"value": "opencode", "label": "OpenCode (딥시크 등)",
         "available": resolve_cli("opencode") is not None},
        {"value": "apple", "label": "Apple 온디바이스 (고속)",
         "available": apple_mt_available()},
        {"value": "apple_hifi", "label": "Apple 온디바이스 (고품질·느림)",
         "available": apple_mt_available()},
    ]
    for entry in get_catalog().values():
        reason = unsupported_reason(entry)
        engines.append({
            "value": entry.name,
            "label": entry.label,
            "available": False if reason else _qwen_available(entry),
            "reason": reason,
        })
    return engines
```

- [ ] **Step 4: `create_translator`의 qwen 분기 교체 (276행 import + 289-307행)**

276행의 `from .translate_mlx import QWEN_MLX_MODELS`를 **삭제**하고, 289-307행의 `if provider in QWEN_MLX_MODELS:` 블록을 교체한다:

```python
    from .translate_catalog import get_catalog

    entry = get_catalog().get(provider)
    if entry is not None:
        # 런타임 자동 선택: 실리콘맥 + MLX 모델 설치 → MLX(더 빠름). 그 외(윈도·인텔맥)
        # 또는 MLX 미설치 → Ollama. 둘 다 없으면 설치 안내와 함께 실패.
        from .translate_mlx import QwenMlxTranslator, qwen_mlx_available
        from .translate_ollama import (
            OllamaTranslator,
            qwen_ollama_available,
            qwen_ollama_model,
        )

        if entry.mlx_repo and qwen_mlx_available(entry.mlx_repo):
            return QwenMlxTranslator(entry.mlx_repo)
        ollama_tag = qwen_ollama_model(provider)
        if qwen_ollama_available(ollama_tag):
            return OllamaTranslator(ollama_tag)
        raise TranslationError(
            f"'{provider}' 로컬 번역 불가: 실리콘맥은 MLX 모델을, 그 외 플랫폼은 "
            f"Ollama 서버(:11434)와 모델(`ollama pull {ollama_tag}`)이 필요합니다."
        )
```

이 블록은 기존 위치(apple_hifi 분기 뒤, `_BACKENDS` 분기 앞)를 그대로 유지한다 — gemini/apple이 먼저 매칭되어야 한다.

- [ ] **Step 5: 테스트 통과 확인**

Run: `uv run pytest apps/server/tests/test_video_translate_cli.py apps/server/tests/test_api_video_jobs.py apps/server/tests/test_translate_apple.py -q`
Expected: PASS — `video_jobs`의 provider 검증은 `list_translate_engines` 파생이라 새 티어가 자동 통과

- [ ] **Step 6: 커밋**

```bash
git add apps/server/domain/video_captions/translate_cli.py \
        apps/server/tests/test_video_translate_cli.py
git commit -m "$(cat <<'EOF'
refactor(video): 번역 엔진 목록·라우팅을 translate_catalog에서 파생

list_translate_engines의 qwen 하드코딩 3줄과 create_translator의
QWEN_MLX_MODELS 멤버십 디스패치를 카탈로그 조회로 대체 — 원격 티어가
드롭다운·라우팅·video_jobs 검증까지 자동으로 흐른다.

available(미설치)과 reason(런타임 미지원)을 분리한다. 섞으면 다운로드하면
쓸 수 있는 티어의 버튼이 죽는다. mlx_repo=None을 qwen_mlx_available에
넘기지 않는 가드 포함(실리콘맥 크래시).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `translate_models` 파생 + 런타임 가드

**Files:**
- Modify: `apps/server/domain/video_captions/translate_models.py` (전체 재작성 — `_TIERS` 중심 구조가 카탈로그 중심으로 바뀐다)
- Test: `apps/server/tests/test_translate_models.py` (없으면 생성)

**Interfaces:**
- Consumes: `translate_catalog.get_catalog()`, `runtime()`, `unsupported_reason(entry)` (Task 2); `qwen_ollama_model` (Task 3)
- Produces:
  - `runtime() -> str` (재수출 — API가 `tmods.runtime()`으로 호출)
  - `is_installed(name: str) -> bool`
  - `download_model(name: str) -> None` — 미지원 런타임이면 `RuntimeError`
  - `delete_model(name: str) -> None` — 미지원 런타임이면 `RuntimeError`
  - `list_models() -> dict` — 각 모델에 기존 필드 + `reason: str | None`, `mlx_repo: str | None`, `mlx_bytes: int`, `ollama_tag: str | None`
  - `_downloading: dict[str, bool]` (API가 참조)

- [ ] **Step 1: 실패하는 테스트 작성**

Create `apps/server/tests/test_translate_models.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from apps.server.domain.video_captions import translate_catalog as tc
from apps.server.domain.video_captions import translate_models as tm


@pytest.fixture(autouse=True)
def _storage(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    tm._downloading.clear()
    yield
    tm._downloading.clear()


def _ollama_server(monkeypatch, running: bool = True) -> None:
    monkeypatch.setattr(tc, "_is_apple_silicon_mac", lambda: False)
    monkeypatch.setattr(tm.to, "ollama_running", lambda: running)
    monkeypatch.setattr(tm.to, "ollama_installed", lambda: True)
    monkeypatch.setattr(tm.to, "qwen_ollama_available", lambda tag: False)


def test_list_models_exposes_repo_and_tag(monkeypatch):
    _ollama_server(monkeypatch)
    out = tm.list_models()
    qwen = next(m for m in out["models"] if m["name"] == "qwen")
    assert qwen["mlx_repo"] == "mlx-community/Qwen3.5-9B-4bit"
    assert qwen["ollama_tag"] == "qwen3.5:9b"
    assert qwen["approx_bytes"] == 6_600_000_000   # ollama 런타임이므로 ollama_bytes
    assert qwen["mlx_bytes"] == 5_000_000_000      # 런타임과 무관한 카탈로그 값
    assert qwen["reason"] is None
    assert qwen["downloadable"] is True


def test_list_models_marks_unsupported_tier(monkeypatch):
    _ollama_server(monkeypatch)
    mlx_only = tc.TranslateModel("qwen_x", "MLX 전용", "a/b", 10, None, 0)
    monkeypatch.setattr(tc, "get_catalog", lambda: {"qwen_x": mlx_only})
    entry = tm.list_models()["models"][0]
    assert entry["reason"] == "실리콘맥 전용"
    assert entry["downloadable"] is False


def test_download_model_rejects_unsupported_runtime(monkeypatch):
    _ollama_server(monkeypatch)
    mlx_only = tc.TranslateModel("qwen_x", "MLX 전용", "a/b", 10, None, 0)
    monkeypatch.setattr(tc, "get_catalog", lambda: {"qwen_x": mlx_only})
    pulled = {"n": 0}
    monkeypatch.setattr(tm.to, "pull_model",
                        lambda tag, on_progress=None: pulled.__setitem__("n", 1))
    with pytest.raises(RuntimeError, match="실리콘맥 전용"):
        tm.download_model("qwen_x")
    assert pulled["n"] == 0  # pull_model(None)이 나가면 안 된다


def test_delete_model_rejects_unsupported_runtime(monkeypatch):
    _ollama_server(monkeypatch)
    mlx_only = tc.TranslateModel("qwen_x", "MLX 전용", "a/b", 10, None, 0)
    monkeypatch.setattr(tc, "get_catalog", lambda: {"qwen_x": mlx_only})
    with pytest.raises(RuntimeError, match="실리콘맥 전용"):
        tm.delete_model("qwen_x")


def test_download_model_unknown_name(monkeypatch):
    _ollama_server(monkeypatch)
    with pytest.raises(KeyError):
        tm.download_model("nope")


def test_is_installed_ollama_only_tier_on_silicon(monkeypatch):
    # mlx_repo=None을 mlx_model_installed에 넘기면 터진다(실리콘맥 크래시).
    monkeypatch.setattr(tc, "_is_apple_silicon_mac", lambda: True)
    ollama_only = tc.TranslateModel("qwen_y", "Ollama 전용", None, 0, "y:1b", 10)
    monkeypatch.setattr(tc, "get_catalog", lambda: {"qwen_y": ollama_only})
    assert tm.is_installed("qwen_y") is False
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest apps/server/tests/test_translate_models.py -q`
Expected: FAIL — `KeyError: 'mlx_repo'`, `RuntimeError`가 발생하지 않음

- [ ] **Step 3: `translate_models.py` 재작성**

```python
"""로컬 번역 모델(Qwen) 사용자 다운로드/삭제 관리.

자막메이커 번역용. 티어 정의는 translate_catalog가 단일 출처이며(빌트인 + 원격
오버레이), 이 모듈은 다운로드/삭제/진행률만 책임진다. 런타임은 플랫폼별로 자동
선택되며 translate_cli.create_translator의 기준과 동일하다:
- 실리콘맥 → MLX (`snapshot_download` → {STORAGE_ROOT}/mlx_models/<repo>)
- 그 외(윈도·인텔맥) → Ollama (:11434 /api/pull)

whisper_models.py 패턴(데몬 스레드 블로킹 다운로드 + 인메모리 _downloading/진행률 +
클라 폴링)을 그대로 미러한다.
"""
from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path

from apps.server.ai.mlx_live_translate import mlx_model_dir, mlx_model_installed

from . import ollama_install as oi
from . import translate_catalog as tcat
from . import translate_ollama as to

logger = logging.getLogger("yeson.video.translate_models")

# name -> True while a download thread runs; name -> pct (Ollama pull only).
_downloading: dict[str, bool] = {}
_progress: dict[str, int] = {}
_state_lock = threading.Lock()


def runtime() -> str:
    """이 서버에서 로컬 번역이 쓸 런타임 — 실리콘=mlx, 그 외=ollama."""
    return tcat.runtime()


def _entry(name: str) -> tcat.TranslateModel:
    return tcat.get_catalog()[name]  # KeyError for unknown names is intentional


def _require_supported(name: str) -> tcat.TranslateModel:
    """이 서버의 런타임을 지원하지 않는 티어면 RuntimeError.

    UI 비활성에만 의존하지 않는다 — /translate-models는 무인증(LAN 신뢰경계)이라
    UI를 거치지 않는 호출이 정상 경로다. 가드가 없으면 MLX 전용 티어를 윈도우에
    POST했을 때 tag=None이 pull_model까지 흘러 {"model": null} 요청이 나간다.
    """
    entry = _entry(name)
    reason = tcat.unsupported_reason(entry)
    if reason:
        raise RuntimeError(f"모델 '{name}'은(는) 이 서버에서 사용할 수 없습니다({reason}).")
    return entry


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _snapshot_download(repo_id: str, local_dir: str) -> None:  # test seam
    from huggingface_hub import snapshot_download

    snapshot_download(repo_id=repo_id, local_dir=local_dir)


def is_installed(name: str) -> bool:
    entry = _entry(name)
    if runtime() == "mlx":
        # mlx_repo가 None이면 mlx_model_installed가 None.replace()로 터진다.
        return bool(entry.mlx_repo) and mlx_model_installed(entry.mlx_repo)
    return to.qwen_ollama_available(to.qwen_ollama_model(name))


def download_model(name: str) -> None:
    """블로킹 다운로드 — 호출부가 워커 스레드에서 실행. 중복 실행은 no-op."""
    entry = _require_supported(name)
    with _state_lock:
        if _downloading.get(name):
            logger.info("download_model(%s): already downloading — skip", name)
            return
        _downloading[name] = True
        _progress[name] = 0
    rt = runtime()
    try:
        if rt == "mlx":
            dest = mlx_model_dir(entry.mlx_repo)
            dest.mkdir(parents=True, exist_ok=True)
            logger.info("download_model(%s): MLX snapshot %s", name, entry.mlx_repo)
            _snapshot_download(entry.mlx_repo, str(dest))
        else:
            tag = to.qwen_ollama_model(name)
            logger.info("download_model(%s): ollama pull %s", name, tag)
            to.pull_model(tag, on_progress=lambda pct: _progress.__setitem__(name, pct))
        logger.info("download_model(%s): done", name)
    finally:
        with _state_lock:
            _downloading[name] = False
            _progress.pop(name, None)


def delete_model(name: str) -> None:
    entry = _require_supported(name)
    with _state_lock:
        if _downloading.get(name):
            raise RuntimeError(f"모델 '{name}'은(는) 다운로드 중이라 삭제할 수 없습니다.")
    if runtime() == "mlx":
        shutil.rmtree(mlx_model_dir(entry.mlx_repo), ignore_errors=True)
    else:
        to.delete_model(to.qwen_ollama_model(name))


def _progress_for(name: str, rt: str, entry: tcat.TranslateModel, approx: int) -> int | None:
    if not _downloading.get(name):
        return None
    if rt == "mlx":
        # MLX(snapshot_download)은 콜백이 없어 디스크 크기로 추정(whisper와 동일).
        disk = _dir_size(mlx_model_dir(entry.mlx_repo))
        return min(99, int(disk * 100 / approx)) if approx else None
    return _progress.get(name, 0)


def list_models() -> dict:
    rt = runtime()
    ollama_run = to.ollama_running() if rt == "ollama" else True
    ollama_inst = to.ollama_installed() if rt == "ollama" else True
    models: list[dict] = []
    for entry in tcat.get_catalog().values():
        reason = tcat.unsupported_reason(entry)
        approx = entry.mlx_bytes if rt == "mlx" else entry.ollama_bytes
        models.append({
            "name": entry.name,
            "label": entry.label,
            "runtime": rt,
            "approx_bytes": approx,
            "downloaded": False if reason else is_installed(entry.name),
            "downloading": _downloading.get(entry.name, False),
            "progress": None if reason else _progress_for(entry.name, rt, entry, approx),
            # 이 서버의 런타임을 아예 지원하지 않는 티어면 사유(클라가 회색 비활성).
            # None인데 downloaded=False면 그냥 미설치 — 다운로드하면 쓸 수 있다.
            "reason": reason,
            # 라이브 자막 패널(server_desktop)이 MLX 리포 id로 모델을 식별하고,
            # mlx_bytes로 용량(≈RAM)을 표시한다. approx_bytes는 이 서버의 런타임
            # 값이라 Ollama 서버에서는 MLX 용량을 알 수 없으므로 별도로 싣는다.
            "mlx_repo": entry.mlx_repo,
            "mlx_bytes": entry.mlx_bytes,
            "ollama_tag": entry.ollama_tag,
            # Ollama 런타임인데 미실행이면 다운로드 불가(먼저 Ollama 실행/설치 필요).
            "downloadable": not reason and (rt == "mlx" or ollama_run),
        })
    return {
        "models": models,
        "runtime": rt,
        "ollama_installed": ollama_inst,
        "ollama_running": ollama_run,
        # 반자동 설치 상태(ollama 런타임에서만) — 미설치 시 클라가 '설치' 버튼 표시.
        "ollama_install": oi.status() if rt == "ollama" else None,
    }
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest apps/server/tests/test_translate_models.py -q`
Expected: PASS — 6개 테스트 전부 통과

- [ ] **Step 5: 커밋**

```bash
git add apps/server/domain/video_captions/translate_models.py \
        apps/server/tests/test_translate_models.py
git commit -m "$(cat <<'EOF'
refactor(video): 번역 모델 관리에서 _TIERS 제거 + 런타임 가드

_TIERS/_TIER_BY_NAME을 translate_catalog로 대체. list_models에 reason(런타임
미지원 사유)·mlx_repo·ollama_tag를 노출한다(라이브 패널이 리포 id로 식별).

download/delete에 미지원 런타임 RuntimeError 가드 추가 — UI 비활성에만
의존하면 MLX 전용 티어를 윈도우에 POST했을 때 tag=None이 pull_model까지
흘러 {"model": null} 요청이 나간다.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: `QWEN_MLX_MODELS` 상수 정리

Task 4·5로 모든 소비자가 카탈로그를 보게 됐으므로 상수가 미참조가 된다.

**Files:**
- Modify: `apps/server/domain/video_captions/translate_mlx.py:12, 24-28` (앵커 내부)
- (변경 없음) `apps/server_desktop/src/setup/serverConfig.ts` — 동기화 문구가 애초에 없음(실행 중 확인)

**Interfaces:**
- Produces: `translate_mlx`는 `qwen_mlx_available(model_id)`·`QwenMlxTranslator`만 노출(`QWEN_MLX_MODELS` 삭제)

- [ ] **Step 1: 미참조 확인**

Run: `grep -rn "QWEN_MLX_MODELS" apps/ --include="*.py"`
Expected: `translate_mlx.py` 자신의 정의(24행)와 docstring(12행)만 남아 있어야 한다. 다른 파일이 나오면 Task 4·5가 덜 끝난 것이므로 멈추고 그쪽을 먼저 완료한다.

- [ ] **Step 2: 상수와 동기화 주석 삭제**

`translate_mlx.py`에서 다음을 삭제한다:
- 23행 주석 `# provider 값 → MLX model id. serverConfig.ts MLX_MODELS와 동기화.`
- 24-28행 `QWEN_MLX_MODELS: dict[str, str] = {...}` 전체

docstring 12행 `QWEN_MLX_MODELS는 serverConfig.ts의 MLX_MODELS와 동일하게 유지해야 한다.`를 다음으로 교체한다:

```
티어 정의(MLX 리포 id)는 translate_catalog가 단일 출처다 — 이 모듈은 리포 id를
받아 번역만 수행한다.
```

docstring 8-10행의 `티어 값(qwen/qwen_lite/qwen_hifi)은 공유하고` → `티어 값은 translate_catalog가 정의하고`로 갱신한다.

- [ ] **Step 3: `serverConfig.ts` — 변경 없음 (확인만)**

> **정정(실행 중 발견)**: 이 계획은 원래 `serverConfig.ts:86-87`의 주석에서 "서버 상수와의 수동 동기화 언급을 제거"하라고 지시했으나, **그 문구는 그 파일에 애초에 없다.** 수동 동기화 주장은 `translate_mlx.py` 쪽에만 있었다(docstring 11행 + 상수 위 주석) — 둘 다 Step 2에서 제거된다. `serverConfig.ts`의 주석은 이미 정확하므로 **건드리지 않는다.**
>
> (`MLX_MODELS` 상수 자체는 Task 9에서 `translateCatalogAdmin.ts`의 `BUILTIN_MLX_MODELS`로 이관된다. 그 예고를 코드 주석으로 남기지 않는다 — 코드가 계획의 태스크 번호를 참조하면 계획이 사라진 뒤 무의미해지고, 어차피 Task 9이 이 상수를 통째로 지운다.)

확인만 한다:

```bash
grep -n "동기화" apps/server_desktop/src/setup/serverConfig.ts
```
Expected: 매치 없음.

- [ ] **Step 4: 서버 테스트 전체 통과 확인**

Run: `uv run pytest apps/server/tests -q`
Expected: PASS — 전체 스위트 통과

- [ ] **Step 5: 커밋**

```bash
git add apps/server/domain/video_captions/translate_mlx.py \
        apps/server_desktop/src/setup/serverConfig.ts
git commit -m "$(cat <<'EOF'
refactor(video): 미참조가 된 QWEN_MLX_MODELS 상수 제거

모든 소비자가 translate_catalog를 보게 되어 상수가 죽었다. serverConfig.ts와
수동 동기화하라는 주석도 함께 제거 — 더 이상 동기화 대상이 아니다.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: API — `?refresh` + 미지원 런타임 409

**Files:**
- Modify: `apps/server/api/v1/translate_models.py`
- Test: `apps/server/tests/test_api_translate_models.py`

**Interfaces:**
- Consumes: `translate_catalog.get_remote_models(force)`, `get_catalog()` (Task 2); `tmods.download_model/delete_model/list_models/runtime/_downloading` (Task 5)
- Produces: `GET /api/v1/translate-models?refresh=<bool>`, `POST /api/v1/translate-models/{name}/download`, `DELETE /api/v1/translate-models/{name}`

- [ ] **Step 1: 실패하는 테스트 작성**

Create `apps/server/tests/test_api_translate_models.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.server.api.v1 import translate_models as api
from apps.server.domain.video_captions import translate_catalog as tc
from apps.server.domain.video_captions import translate_models as tmods
from apps.server.main import app


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setattr(tc, "_is_apple_silicon_mac", lambda: False)
    monkeypatch.setattr(tmods.to, "ollama_running", lambda: True)
    monkeypatch.setattr(tmods.to, "ollama_installed", lambda: True)
    monkeypatch.setattr(tmods.to, "qwen_ollama_available", lambda tag: False)
    tmods._downloading.clear()
    yield
    tmods._downloading.clear()


@pytest.fixture
def client():
    return TestClient(app)


def test_list_does_not_refresh_by_default(client, monkeypatch):
    calls = []
    monkeypatch.setattr(tc, "get_remote_models", lambda force=False: calls.append(force) or [])
    r = client.get("/api/v1/translate-models")
    assert r.status_code == 200
    assert calls == [False]
    assert {m["name"] for m in r.json()["models"]} >= {"qwen", "qwen_lite", "qwen_hifi"}


def test_list_refresh_forces_network(client, monkeypatch):
    calls = []
    monkeypatch.setattr(tc, "get_remote_models", lambda force=False: calls.append(force) or [])
    r = client.get("/api/v1/translate-models?refresh=true")
    assert r.status_code == 200
    assert calls == [True]


def test_download_unknown_model_404(client):
    assert client.post("/api/v1/translate-models/nope/download").status_code == 404


def test_download_unsupported_runtime_409_and_no_pull(client, monkeypatch):
    mlx_only = tc.TranslateModel("qwen_x", "MLX 전용", "a/b", 10, None, 0)
    monkeypatch.setattr(tc, "get_catalog", lambda: {"qwen_x": mlx_only})
    spawned = {"n": 0}
    monkeypatch.setattr(api, "_spawn_download", lambda name: spawned.__setitem__("n", 1))
    r = client.post("/api/v1/translate-models/qwen_x/download")
    assert r.status_code == 409
    assert "실리콘맥 전용" in r.json()["detail"]
    assert spawned["n"] == 0  # 다운로드 스레드 자체가 뜨면 안 된다


def test_download_ollama_not_running_409(client, monkeypatch):
    monkeypatch.setattr(tmods.to, "ollama_running", lambda: False)
    r = client.post("/api/v1/translate-models/qwen/download")
    assert r.status_code == 409
    assert "Ollama" in r.json()["detail"]


def test_download_started(client, monkeypatch):
    spawned = {"n": 0}
    monkeypatch.setattr(api, "_spawn_download", lambda name: spawned.__setitem__("n", 1))
    r = client.post("/api/v1/translate-models/qwen/download")
    assert r.status_code == 202
    assert r.json() == {"status": "started"}
    assert spawned["n"] == 1


def test_delete_unsupported_runtime_409(client, monkeypatch):
    mlx_only = tc.TranslateModel("qwen_x", "MLX 전용", "a/b", 10, None, 0)
    monkeypatch.setattr(tc, "get_catalog", lambda: {"qwen_x": mlx_only})
    r = client.delete("/api/v1/translate-models/qwen_x")
    assert r.status_code == 409
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest apps/server/tests/test_api_translate_models.py -q`
Expected: FAIL — `?refresh`가 무시되고(`calls == []`), 미지원 런타임이 409 대신 202

- [ ] **Step 3: 구현**

`apps/server/api/v1/translate_models.py`의 import에 추가:

```python
from starlette.concurrency import run_in_threadpool

from apps.server.domain.video_captions import translate_catalog as tcat
```

`list_translate_models`(40-42행)를 교체:

```python
@router.get("")
async def list_translate_models(refresh: bool = False) -> dict:
    # 원격 카탈로그 갱신은 블로킹 requests.get이므로 스레드풀로 오프로드(루프 정지 방지).
    # force=False면 TTL 캐시가 신선할 때 네트워크를 타지 않는다(탭 열 때 갱신).
    await run_in_threadpool(tcat.get_remote_models, refresh)
    return tmods.list_models()
```

`download_translate_model`(45-59행)을 교체:

```python
@router.post("/{name}/download", status_code=status.HTTP_202_ACCEPTED)
async def download_translate_model(name: str) -> dict:
    catalog = tcat.get_catalog()
    if name not in catalog:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown model")
    # 이 서버의 런타임을 지원하지 않는 티어는 거부 — 무인증 API라 UI 비활성에만
    # 의존할 수 없다. 통과시키면 tag=None이 pull_model까지 흘러간다.
    reason = tcat.unsupported_reason(catalog[name])
    if reason:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"모델 '{name}'은(는) 이 서버에서 사용할 수 없습니다({reason}).")
    if tmods.is_installed(name):
        return {"status": "already_downloaded"}
    if tmods._downloading.get(name):
        return {"status": "downloading"}
    # Ollama 런타임인데 서버가 안 떠 있으면 pull 자체가 불가 — 명확히 409로 안내.
    if tmods.runtime() == "ollama" and not tmods.to.ollama_running():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Ollama 서버가 실행 중이 아닙니다. Ollama를 설치·실행한 뒤 다시 시도하세요.")
    _spawn_download(name)
    return {"status": "started"}
```

`delete_translate_model`(62-69행)을 교체:

```python
@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_translate_model(name: str) -> None:
    if name not in tcat.get_catalog():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown model")
    try:
        tmods.delete_model(name)   # 미지원 런타임·다운로드 중이면 RuntimeError
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest apps/server/tests/test_api_translate_models.py apps/server/tests/test_api_video_jobs.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add apps/server/api/v1/translate_models.py \
        apps/server/tests/test_api_translate_models.py
git commit -m "$(cat <<'EOF'
feat(video): /translate-models?refresh + 미지원 런타임 409

video_models의 검증된 패턴 미러 — 원격 갱신은 run_in_threadpool로 오프로드
(블로킹 requests.get이 이벤트 루프를 세운다). 검증은 _TIER_BY_NAME 대신
get_catalog()를 본다.

download/delete에 미지원 런타임 409 추가. 무인증 API(LAN 신뢰경계)라
UI를 거치지 않는 호출이 정상 경로다.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: 자막메이커 탭 — 새로고침 버튼 + `reason`

**Files:**
- Modify: `apps/desktop/src/console/videoApi.ts:86-117`
- Modify: `apps/desktop/src/console/VideoCaptionPanel.tsx` (import 7행, `refreshCatalog` 174-180행 부근, 번역 탭 렌더 ~940-980행)
- Test: `apps/desktop/src/console/videoApi.test.ts`

**Interfaces:**
- Consumes: `GET /api/v1/translate-models?refresh=1` (Task 7)
- Produces:
  - `TranslateModelInfo`에 `reason: string | null`, `mlx_repo: string | null`, `ollama_tag: string | null` 추가
  - `refreshTranslateModels(): Promise<TranslateModelsResponse>`

- [ ] **Step 1: 실패하는 테스트 추가**

`apps/desktop/src/console/videoApi.test.ts` 끝에 추가(파일 상단의 기존 fetch 모킹 관례를 따른다):

```ts
it("refreshTranslateModels가 refresh=1로 호출한다", async () => {
  const seen: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    seen.push(String(url));
    return new Response(JSON.stringify({
      models: [], runtime: "ollama", ollama_installed: true,
      ollama_running: true, ollama_install: null,
    }), { status: 200, headers: { "content-type": "application/json" } });
  }));
  const { refreshTranslateModels } = await import("./videoApi");
  await refreshTranslateModels();
  expect(seen[0]).toContain("/api/v1/translate-models?refresh=1");
});
```

- [ ] **Step 2: 실패 확인**

Run: `pnpm --dir apps/desktop test -- videoApi`
Expected: FAIL — `refreshTranslateModels` is not exported

- [ ] **Step 3: `videoApi.ts` 수정**

`TranslateModelInfo`(86-95행)에 필드를 추가한다:

```ts
export type TranslateModelInfo = {
  name: string;
  label: string;
  runtime: "mlx" | "ollama";
  approx_bytes: number;
  downloaded: boolean;
  downloading: boolean;
  progress: number | null;
  downloadable: boolean;
  // 이 서버의 런타임을 아예 지원하지 않는 티어에만 채워진다(예: "실리콘맥 전용").
  // null인데 downloaded=false면 그냥 미설치 — 다운로드하면 쓸 수 있다.
  reason: string | null;
  mlx_repo: string | null;
  ollama_tag: string | null;
};
```

`listTranslateModels` 아래에 추가한다:

```ts
export async function refreshTranslateModels(): Promise<TranslateModelsResponse> {
  return request(`${apiBase()}/api/v1/translate-models?refresh=1`, {});
}
```

- [ ] **Step 4: `VideoCaptionPanel.tsx` 수정**

7행 import에 `refreshTranslateModels`를 추가한다.

`refreshCatalog`(174-183행)를 두 탭 모두 갱신하도록 교체한다. 기존의 "실패해도 기존 목록 유지" catch는 그대로 살린다 — 원격은 부가 정보다:

```tsx
  const refreshCatalog = useCallback(async () => {
    setRefreshingCatalog(true);
    try {
      if (modelTab === "translate" && translateMeta !== null) {
        const tm = await refreshTranslateModels();
        setTranslateMeta(tm);
        setTranslateModels(tm.models);
      } else {
        setModels(await refreshVideoModels());
      }
    } catch {
      // 실패해도 기존 목록 유지(원격은 부가 정보)
    } finally {
      setRefreshingCatalog(false);
    }
  }, [modelTab, translateMeta]);
```

`translateMeta`(128행)와 `translateModels`(126행)는 이미 선언되어 있고, `refresh` 콜백(158-159행)이 둘을 함께 세팅하는 관례를 그대로 따른 것이다.

번역 탭의 안내 문구(948-952행) **뒤**, `translateModels.map` (953행) **앞**에 전사 탭과 동일한 버튼을 추가한다:

```tsx
        <button type="button" style={{ ...consoleStyles.mutedAction, alignSelf: "flex-start" }}
          disabled={refreshingCatalog}
          onClick={() => void refreshCatalog()}>
          {refreshingCatalog ? "새로고침 중…" : "카탈로그 새로고침"}
        </button>
```

번역 모델 행의 액션 분기(963-980행)에 `m.reason` 가드를 **맨 앞에** 추가한다. 기존 분기(`m.downloading` → `m.downloaded` → 다운로드 버튼)의 style·onClick·라벨은 그대로 두고 앞에 한 갈래만 얹는 것이다:

```tsx
              {m.reason ? (
                <span style={{ fontSize: 13, opacity: 0.7 }}>{m.reason}</span>
              ) : m.downloading ? (
                <span style={{ fontSize: 13 }}>다운로드 중… {m.progress ?? 0}%</span>
              ) : m.downloaded ? (
                <>
                  <span style={{ fontSize: 13, color: "#30a46c" }}>설치됨</span>
                  <button type="button" style={consoleStyles.mutedAction}
                    onClick={() => void deleteTranslateModel(m.name).then(refresh)
                      .catch((e) => setError(e instanceof Error ? e.message : String(e)))}>
                    삭제
                  </button>
                </>
              ) : (
                <button type="button" style={consoleStyles.mutedAction} disabled={!m.downloadable}
                  onClick={() => void downloadTranslateModel(m.name).then(refresh)
                    .catch((e) => setError(e instanceof Error ? e.message : String(e)))}>
                  다운로드
                </button>
              )}
```

- [ ] **Step 5: 테스트·타입 확인**

Run: `pnpm --dir apps/desktop test && pnpm --dir apps/desktop exec tsc --noEmit`
Expected: PASS — 테스트 통과 + 타입 에러 없음

- [ ] **Step 6: 커밋**

```bash
git add apps/desktop/src/console/videoApi.ts \
        apps/desktop/src/console/VideoCaptionPanel.tsx \
        apps/desktop/src/console/videoApi.test.ts
git commit -m "$(cat <<'EOF'
feat(video): 번역 모델 탭에 '카탈로그 새로고침' + 미지원 사유 표시

전사 모델 탭과 동일한 버튼·상태 관례. reason이 있는 티어(이 서버의 런타임을
지원하지 않음)만 회색 사유 라벨로 표시하고 다운로드 버튼을 숨긴다 — 단순
미설치(reason=null)는 그대로 다운로드 가능해야 한다.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: 라이브 자막 패널 — 카탈로그 소비 + 빌트인 폴백

**Files:**
- Create: `apps/server_desktop/src/translateCatalogAdmin.ts`
- Test: `apps/server_desktop/src/translateCatalogAdmin.test.ts`
- Modify: `apps/server_desktop/src/setup/serverConfig.ts:86-92` (`MLX_MODELS` 삭제)
- Modify: `apps/server_desktop/src/setup/MlxModelPanel.tsx` (앵커 내부)
- Modify: `apps/server_desktop/src/setup/ServerConfigPanel.tsx:38, 253`
- Modify: `apps/server_desktop/src/ServerConsole.tsx:585`

**Interfaces:**
- Consumes: `GET /api/v1/translate-models` 응답의 `models[].mlx_repo` / `label` / `mlx_bytes` (Task 5). **`approx_bytes`는 쓰지 않는다** — 그건 이 서버의 런타임 값이라 Ollama 서버(윈도·인텔맥)에서는 MLX 용량이 아니다.
- Produces:
  - `LiveMlxModel = { id: string; label: string; bytes: number }`
  - `BUILTIN_MLX_MODELS: LiveMlxModel[]`
  - `listLiveMlxModels(port: number): Promise<LiveMlxModel[]>` — 실패 시 `BUILTIN_MLX_MODELS`

- [ ] **Step 1: 실패하는 테스트 작성**

Create `apps/server_desktop/src/translateCatalogAdmin.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";
import { BUILTIN_MLX_MODELS, listLiveMlxModels } from "./translateCatalogAdmin";

describe("listLiveMlxModels", () => {
  it("mlx_repo가 있는 항목만 변환한다", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      models: [
        { name: "qwen", label: "Qwen 9B (로컬)", mlx_repo: "mlx-community/Qwen3.5-9B-4bit",
          mlx_bytes: 5000000000, ollama_tag: "qwen3.5:9b" },
        { name: "only_ollama", label: "Ollama 전용", mlx_repo: null,
          mlx_bytes: 0, ollama_tag: "x:1b" },
      ],
    }), { status: 200, headers: { "content-type": "application/json" } })));
    const out = await listLiveMlxModels(8000);
    expect(out).toEqual([
      { id: "mlx-community/Qwen3.5-9B-4bit", label: "Qwen 9B (로컬)", bytes: 5000000000 },
    ]);
  });

  it("서버가 안 떠 있으면 빌트인으로 폴백한다", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    }));
    expect(await listLiveMlxModels(8000)).toEqual(BUILTIN_MLX_MODELS);
  });

  it("구버전 번들(404)이면 빌트인으로 폴백한다", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 404 })));
    expect(await listLiveMlxModels(8000)).toEqual(BUILTIN_MLX_MODELS);
  });

  it("빌트인은 현행 3종을 유지한다", () => {
    expect(BUILTIN_MLX_MODELS.map((m) => m.id)).toEqual([
      "mlx-community/Qwen3.5-9B-4bit",
      "mlx-community/Qwen3.5-4B-4bit",
      "mlx-community/Qwen3.5-9B-8bit",
    ]);
  });
});
```

- [ ] **Step 2: 실패 확인**

Run: `pnpm --dir apps/server_desktop test -- translateCatalogAdmin`
Expected: FAIL — `Cannot find module './translateCatalogAdmin'`

- [ ] **Step 3: `translateCatalogAdmin.ts` 작성**

```ts
// 라이브 자막 번역용 MLX 모델 목록 — 서버 카탈로그(빌트인+원격)에서 받아온다.
// videoJobsAdmin.ts 관례를 미러: 번들 서버의 루프백 REST(127.0.0.1:<port>),
// translate-models API는 무인증(LAN 신뢰경계)이라 로그인 게이트가 없다.
//
// 목록만 서버에서 받고, 설치 상태 확인·다운로드는 Tauri 커맨드가 계속 담당한다.
// ServerConfigPanel은 서버가 아직 안 떠 있는 초기 설정 단계에서도 동작해야 하므로,
// 조회 실패 시 BUILTIN_MLX_MODELS로 조용히 폴백한다.
const API = "/api/v1";

export type LiveMlxModel = { id: string; label: string; bytes: number };

// serverConfig.ts의 MLX_MODELS에서 이관 — 서버 조회 실패 시의 폴백을 겸한다.
export const BUILTIN_MLX_MODELS: LiveMlxModel[] = [
  { id: "mlx-community/Qwen3.5-9B-4bit", label: "Qwen 9B (로컬)", bytes: 5_000_000_000 },
  { id: "mlx-community/Qwen3.5-4B-4bit", label: "Qwen 4B (로컬·빠름)", bytes: 2_300_000_000 },
  { id: "mlx-community/Qwen3.5-9B-8bit", label: "Qwen 9B (로컬·고품질 8bit)", bytes: 10_000_000_000 },
];

type TranslateModelRow = {
  label: string;
  mlx_repo: string | null;
  mlx_bytes: number;
};

function base(port: number): string {
  return `http://127.0.0.1:${port}`;
}

export async function listLiveMlxModels(port: number): Promise<LiveMlxModel[]> {
  try {
    const r = await fetch(`${base(port)}${API}/translate-models`);
    if (!r.ok) return BUILTIN_MLX_MODELS;  // 구버전 번들엔 라우트가 없다(404)
    const body = (await r.json()) as { models?: TranslateModelRow[] };
    const rows = (body.models ?? []).filter((m) => Boolean(m.mlx_repo));
    if (rows.length === 0) return BUILTIN_MLX_MODELS;
    return rows.map((m) => ({
      id: m.mlx_repo as string,
      label: m.label,
      bytes: m.mlx_bytes,
    }));
  } catch {
    return BUILTIN_MLX_MODELS;  // 서버 미기동 — 초기 설정 단계의 정상 경로다
  }
}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pnpm --dir apps/server_desktop test -- translateCatalogAdmin`
Expected: PASS — 4개 테스트 통과

- [ ] **Step 5: `serverConfig.ts`에서 `MLX_MODELS` 삭제**

86-92행의 주석과 `MLX_MODELS` 상수를 삭제한다. `mlxModelStatus`·`downloadMlxModel`은 **그대로 둔다**(Tauri 커맨드는 계속 쓴다).

- [ ] **Step 6: `MlxModelPanel.tsx` 수정 (앵커 내부)**

import(7행)을 교체:

```tsx
import { downloadMlxModel, mlxModelStatus } from "./serverConfig";
import { BUILTIN_MLX_MODELS, type LiveMlxModel, listLiveMlxModels } from "../translateCatalogAdmin";
```

컴포넌트 시그니처와 상태·refresh를 교체:

```tsx
export function MlxModelPanel(props: {
  selectedModel: string;
  onSelectModel: (id: string) => void;
  port: number;
}) {
  const [models, setModels] = useState<LiveMlxModel[]>(BUILTIN_MLX_MODELS);
  const [installed, setInstalled] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");

  const refresh = async () => {
    // 목록은 서버 카탈로그(빌트인+원격), 설치 여부는 Tauri — 서버가 없어도 동작한다.
    const list = await listLiveMlxModels(props.port);
    setModels(list);
    const entries = await Promise.all(
      list.map(async (m) => [m.id, await mlxModelStatus(m.id)] as const),
    );
    setInstalled(Object.fromEntries(entries));
  };
```

`useEffect`의 의존성 주석은 그대로 두고, 렌더의 `MLX_MODELS`를 `models`로 바꾼다:

```tsx
      {models.map((m) => (
        <div key={m.id} style={styles.row}>
          <label style={styles.radioLabel}>
            <input
              type="radio"
              name="mlx-model"
              checked={(props.selectedModel || models[0]?.id) === m.id}
              onChange={() => props.onSelectModel(m.id)}
            />
            <span>{m.label} · 약 {(m.bytes / 1_000_000_000).toFixed(1)}GB</span>
```

나머지(칩·다운로드 버튼·progress·error·styles)는 그대로 둔다.

> 라벨에 용량을 붙이는 이유: 이관 전 라이브 라벨은 `Qwen3.5 9B (기본 — 품질 우선, RAM ~5GB)`처럼 RAM 힌트를 담고 있었는데 카탈로그 라벨엔 없다. `mlx_bytes`가 사실상 RAM 사용량이라 정보값을 유지한다.

- [ ] **Step 7: `port` 배선**

`apps/server_desktop/src/setup/ServerConfigPanel.tsx` 38행:

```tsx
export default function ServerConfigPanel(props: { port: number }) {
```

253행:

```tsx
        <MlxModelPanel selectedModel={mlxModel} onSelectModel={setMlxModel} port={props.port} />
```

`apps/server_desktop/src/ServerConsole.tsx` 585행:

```tsx
            <ServerConfigPanel port={port} />
```

- [ ] **Step 8: 전체 확인**

Run: `pnpm --dir apps/server_desktop test && pnpm --dir apps/server_desktop exec tsc --noEmit`
Expected: PASS — 테스트 통과 + 타입 에러 없음

Run: `uv run pytest apps/server/tests -q && pnpm --dir apps/desktop test`
Expected: PASS — 서버·클라 전체 회귀 없음

- [ ] **Step 9: 커밋**

```bash
git add apps/server_desktop/src/translateCatalogAdmin.ts \
        apps/server_desktop/src/translateCatalogAdmin.test.ts \
        apps/server_desktop/src/setup/serverConfig.ts \
        apps/server_desktop/src/setup/MlxModelPanel.tsx \
        apps/server_desktop/src/setup/ServerConfigPanel.tsx \
        apps/server_desktop/src/ServerConsole.tsx
git commit -m "$(cat <<'EOF'
feat(server-console): 라이브 MLX 모델 목록을 서버 카탈로그에서 조회

MLX_MODELS 하드코딩을 translateCatalogAdmin으로 이관 — 원격 카탈로그에 티어를
추가하면 자막메이커와 라이브 자막 양쪽에 반영된다. 목록만 HTTP로 받고 설치
확인·다운로드는 Tauri 유지, 조회 실패 시 빌트인 폴백이라 서버가 안 떠 있는
초기 설정 단계에서도 그대로 동작한다.

라벨에 용량을 병기해 이관 전 RAM 힌트의 정보값을 유지한다.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## 완료 후 수동 검증

원격 카탈로그가 실제로 도는지는 자동 테스트로 덮이지 않는다(네트워크·실기기).

- [ ] `translate_catalog.remote.json`에 티어를 하나 추가해 브랜치에 push한 뒤, `YESON_TRANSLATE_CATALOG_URL`을 그 브랜치 raw URL로 지정하고 "카탈로그 새로고침" → 새 티어가 뜨는지.
- [ ] **실리콘맥**: 새 티어 다운로드 → 자막메이커 번역 실행 → JSON 배열 KO 계약 통과 확인.
- [ ] **윈도우**: Ollama 실행 상태에서 새 티어 다운로드(`ollama pull`) → 번역 실행 확인. **`{STORAGE_ROOT}`에 `mlx_models` 디렉터리가 생기지 않았는지** 확인.
- [ ] 서버 콘솔에서 **서버를 정지한 채** 설정 패널을 열어 MLX 모델 3종이 그대로 보이는지(빌트인 폴백).
- [ ] 원격 JSON이 빈 상태에서 자막메이커·라이브 패널이 v1.3.8과 동일하게 보이는지(회귀 기준선).

**릴리스 절차 항목**: 원격 카탈로그에 티어를 올리기 전, **두 런타임 각각**(실리콘맥 MLX / 윈도우·인텔맥 Ollama)에서 `build_translation_prompt` → JSON 배열 KO 계약을 지키는지 실기 검증한다. whisper와 달리 번역은 프롬프트 계약에 묶여 있어(`_extract_json_array` 실패 시 `TranslationError`) 검증 비용이 티어당 2회다.
