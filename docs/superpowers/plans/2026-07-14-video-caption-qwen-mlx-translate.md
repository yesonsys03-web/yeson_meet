# 자막메이커 로컬 MLX Qwen 번역 엔진 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 자막메이커 번역 엔진 드롭다운에 로컬 MLX Qwen 2종(9B/4B)을 추가해, 실리콘맥에서 온디바이스로 EN→KO 자막을 번역한다.

**Architecture:** 라이브 자막용 MLX 워커(`mlx_worker.py`)에 범용 raw-generate 요청 타입을 additive로 추가하고, `MlxWorkerClient`에 `model_id` 인자와 `generate()` 메서드를 더한다. 신규 `QwenMlxTranslator`(TranslationProvider)가 잡당 워커 1회 기동·유지하며 `build_translation_prompt` 배치 프롬프트를 보내고 JSON 배열을 받는다. 번역 목록/라우팅에 `qwen`/`qwen_lite` provider를 추가하고, 파이프라인이 잡 종료 시 워커를 `aclose`한다.

**Tech Stack:** Python 3.12, asyncio subprocess, mlx-lm(실리콘맥 전용, 지연 import), faster-whisper 파이프라인, pytest + pytest-asyncio.

## Global Constraints

- 실리콘맥 전용. 인텔맥·윈도우·구버전 macOS에서는 `available=false`로만 노출(항목 삭제 금지).
- 모든 워커/클라이언트 변경은 **additive·하위호환** — 라이브 자막(하이브리드 B) 동작 불변.
- `mlx_lm` import는 워커 서브프로세스(`run_worker`) 안에서만 — 서버 본체/인텔·리눅스 빌드 오염 금지.
- 번역 provider 값: `qwen` → `mlx-community/Qwen3.5-9B-4bit`, `qwen_lite` → `mlx-community/Qwen3.5-4B-4bit`. `apps/server_desktop/src/setup/serverConfig.ts`의 `MLX_MODELS`와 동일 유지.
- 전사(모델) 계층 변경 금지 — whisper/Apple 그대로.
- 가장 작은 패치. 요청한 파일만 수정. 파일 전체 재작성 금지.
- pytest는 리포 루트에서 `testpaths = ["apps/server/tests"]`. mlx 미설치 CI/인텔맥에서도 전 테스트 통과해야 함(실모델 로드 없는 페이크/monkeypatch 테스트만).
- 커밋은 feature 브랜치에서. main 직접 push·자기 PR 머지 금지(사용자 머지).

---

### Task 1: MLX 워커에 raw-generate 요청 타입 추가

**Files:**
- Modify: `apps/server/ai/mlx_worker.py:47-114` (`_make_translate`, `run_worker`)
- Test: `apps/server/tests/test_mlx_worker_raw.py` (신규)

**Interfaces:**
- Consumes: 기존 `YESON_MLX_FAKE=1` 페이크 모드, `YESON_MLX_MODEL_PATH`.
- Produces: 워커 stdin에 `{"id": N, "prompt": "<str>"}` 요청 → stdout `{"id": N, "text": "<str>", "gen_ms": M}`. 기존 `{"id": N, "en": "<str>", "context": [...]}` → `{"id": N, "ko": "<str>", "gen_ms": M}` 경로는 불변.

- [ ] **Step 1: Write the failing test**

`apps/server/tests/test_mlx_worker_raw.py`:

```python
from __future__ import annotations

import json
import subprocess
import sys

import pytest


def _run_fake_worker(requests: list[dict]) -> list[dict]:
    """YESON_MLX_FAKE=1 워커를 서브프로세스로 띄워 요청들을 보내고 응답 JSON들을 모은다."""
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "from apps.server.ai.mlx_worker import run_worker; raise SystemExit(run_worker())"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
        env={"YESON_MLX_FAKE": "1", "PYTHONPATH": "."},
    )
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in requests)
    out, _err = proc.communicate(payload, timeout=30)
    events = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def test_raw_prompt_request_returns_text():
    events = _run_fake_worker([{"id": 1, "prompt": "Translate: hello"}])
    ready = [e for e in events if e.get("type") == "status"]
    assert ready and ready[0]["state"] == "ready"
    resp = [e for e in events if e.get("id") == 1]
    assert resp, f"no id=1 response in {events}"
    assert "text" in resp[0]
    assert "Translate: hello" in resp[0]["text"]  # fake echo


def test_structured_en_request_still_works():
    events = _run_fake_worker([{"id": 2, "en": "hello", "context": []}])
    resp = [e for e in events if e.get("id") == 2]
    assert resp and "ko" in resp[0]
    assert resp[0]["ko"] == "[fake] hello"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest apps/server/tests/test_mlx_worker_raw.py -v`
Expected: FAIL — `test_raw_prompt_request_returns_text` 에서 id=1 응답이 없음(현재 워커는 `en` 없으면 요청을 스킵).

- [ ] **Step 3: Write minimal implementation**

`apps/server/ai/mlx_worker.py`의 `_make_translate()`를 두 클로저 반환으로 변경. 함수 시그니처/본문 교체:

```python
def _make_translate():
    """(structured_translate, generate_raw) 두 클로저를 반환. 모델/토크나이저 공유.

    structured_translate(en, context) -> ko : 라이브 문장별 번역(기존 로직).
    generate_raw(prompt) -> text            : 임의 프롬프트 원문 생성(배치 자막용).
    """
    if os.environ.get("YESON_MLX_FAKE") == "1":
        return (lambda en, context: f"[fake] {en}",
                lambda prompt: f"[fake-raw] {prompt}")

    model_path = os.environ.get("YESON_MLX_MODEL_PATH", "")
    if not model_path or not os.path.isfile(os.path.join(model_path, "config.json")):
        _emit({"type": "status", "state": "error",
               "reason": f"missing_mlx_model: {model_path or '(unset)'}"})
        raise SystemExit(1)

    # 지연 import — mlx 미설치 플랫폼에서 서버 본체를 오염시키지 않는다.
    from mlx_lm import generate, load
    from mlx_lm.sample_utils import make_sampler

    model, tokenizer = load(model_path)
    sampler = make_sampler(temp=0.0)

    def _strip_think(text: str) -> str:
        out = text.strip()
        if "</think>" in out:
            out = out.split("</think>", 1)[1].strip()
        return out

    def _structured_translate(en: str, context: list[list[str]]) -> str:
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user(context, en)},
        ]
        try:
            prompt = tokenizer.apply_chat_template(
                messages, add_generation_prompt=True, enable_thinking=False)
        except TypeError:  # enable_thinking 미지원 템플릿
            prompt = tokenizer.apply_chat_template(messages, add_generation_prompt=True)
        text = generate(model, tokenizer, prompt=prompt, max_tokens=256,
                        sampler=sampler, verbose=False)
        return _strip_think(text)

    def _generate_raw(user_prompt: str) -> str:
        messages = [{"role": "user", "content": user_prompt}]
        try:
            prompt = tokenizer.apply_chat_template(
                messages, add_generation_prompt=True, enable_thinking=False)
        except TypeError:
            prompt = tokenizer.apply_chat_template(messages, add_generation_prompt=True)
        text = generate(model, tokenizer, prompt=prompt, max_tokens=4096,
                        sampler=sampler, verbose=False)
        return _strip_think(text)

    return (_structured_translate, _generate_raw)
```

`run_worker()`의 상단·루프를 교체:

```python
def run_worker() -> int:
    try:
        translate, generate_raw = _make_translate()
    except SystemExit as exc:
        return int(exc.code or 1)
    except Exception as exc:  # noqa: BLE001 — 기동 실패는 반드시 status:error로 표면화
        _emit({"type": "status", "state": "error",
               "reason": f"mlx_startup_failed: {type(exc).__name__}: {exc}"})
        return 1
    _emit({"type": "status", "state": "ready"})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            req_id = req["id"]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            print(f"mlx-worker: bad request line: {line[:120]}", file=sys.stderr, flush=True)
            continue
        t0 = time.perf_counter()
        if "prompt" in req:
            try:
                text = generate_raw(str(req["prompt"]))
            except Exception as exc:  # noqa: BLE001 — 요청 하나의 실패가 워커를 죽이면 안 됨
                print(f"mlx-worker: raw generate failed: {exc}", file=sys.stderr, flush=True)
                text = ""
            _emit({"id": req_id, "text": text, "gen_ms": round((time.perf_counter() - t0) * 1000)})
            continue
        try:
            en = str(req["en"])
            context = [[str(a), str(b)] for a, b in req.get("context", [])]
        except (KeyError, TypeError, ValueError):
            print(f"mlx-worker: bad request line: {line[:120]}", file=sys.stderr, flush=True)
            continue
        try:
            ko = translate(en, context)
        except Exception as exc:  # noqa: BLE001 — 요청 하나의 실패가 워커를 죽이면 안 됨
            print(f"mlx-worker: translate failed: {exc}", file=sys.stderr, flush=True)
            ko = ""
        _emit({"id": req_id, "ko": ko, "gen_ms": round((time.perf_counter() - t0) * 1000)})
    return 0  # stdin EOF = 정상 종료
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest apps/server/tests/test_mlx_worker_raw.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add apps/server/ai/mlx_worker.py apps/server/tests/test_mlx_worker_raw.py
git commit -m "feat(video/mlx): add raw-generate request type to MLX worker"
```

---

### Task 2: MlxWorkerClient에 model_id 인자 + generate() 메서드 추가

**Files:**
- Modify: `apps/server/ai/mlx_live_translate.py:109-146` (`MlxWorkerClient.__init__`, `start`), 그리고 `translate()` 뒤(약 210행)에 `generate()` 추가
- Test: `apps/server/tests/test_mlx_worker_client_generate.py` (신규)

**Interfaces:**
- Consumes: Task 1의 워커 raw 프로토콜(`{"id","prompt"}` → `{"id","text"}`).
- Produces: `MlxWorkerClient(model_id: str | None = None, argv=None, ready_timeout=...)`; `async def generate(self, prompt: str, timeout: float) -> str`. 기존 `translate(en, context, timeout)`는 불변.

- [ ] **Step 1: Write the failing test**

`apps/server/tests/test_mlx_worker_client_generate.py`:

```python
from __future__ import annotations

import sys
import textwrap

import pytest

from apps.server.ai.mlx_live_translate import MlxWorkerClient


def _script_argv(tmp_path, body: str) -> list[str]:
    script = tmp_path / "fake_raw_worker.py"
    script.write_text(textwrap.dedent(body))
    return [sys.executable, str(script)]


RAW_ECHO_WORKER = """\
    import json, sys
    print(json.dumps({"type": "status", "state": "ready"}), flush=True)
    for line in sys.stdin:
        req = json.loads(line)
        print(json.dumps({"id": req["id"], "text": "RAW:" + req["prompt"], "gen_ms": 1}), flush=True)
"""


async def test_generate_roundtrip(tmp_path):
    client = MlxWorkerClient(argv=_script_argv(tmp_path, RAW_ECHO_WORKER))
    await client.start()
    try:
        out = await client.generate("hello prompt", timeout=5.0)
        assert out == "RAW:hello prompt"
    finally:
        await client.close()


async def test_model_id_sets_env(tmp_path, monkeypatch):
    captured = {}

    async def fake_create(*argv, **kwargs):
        captured["env"] = kwargs.get("env", {})
        raise RuntimeError("stop before real spawn")

    import apps.server.ai.mlx_live_translate as mod
    monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", fake_create)
    client = MlxWorkerClient(model_id="mlx-community/Qwen3.5-4B-4bit")
    with pytest.raises(Exception):
        await client.start()
    assert "Qwen3.5-4B-4bit" in captured["env"]["YESON_MLX_MODEL_PATH"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest apps/server/tests/test_mlx_worker_client_generate.py -v`
Expected: FAIL — `MlxWorkerClient`에 `model_id` 인자 없음(`TypeError`) / `generate` 메서드 없음(`AttributeError`).

- [ ] **Step 3: Write minimal implementation**

`MlxWorkerClient.__init__` 시그니처와 필드에 `model_id` 추가:

```python
    def __init__(self, argv: list[str] | None = None,
                 ready_timeout: float = DEFAULT_READY_TIMEOUT_SECONDS,
                 model_id: str | None = None) -> None:
        self._argv = argv
        self._ready_timeout = ready_timeout
        self._model_id = model_id
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_file: tempfile._TemporaryFileWrapper | None = None
        self._next_id = 0
        self._lock = asyncio.Lock()  # 요청은 순차 — 워커도 순차 처리
```

`start()`의 env 구성에서 model_id를 반영(기존 `"YESON_MLX_MODEL_PATH": str(mlx_model_dir(mlx_model_id())),` 줄 교체):

```python
            "YESON_MLX_MODEL_PATH": str(mlx_model_dir(self._model_id or mlx_model_id())),
```

`translate()` 메서드 뒤(약 210~211행, `return str(resp.get("ko", ""))` 다음 빈 줄)에 `generate()` 추가:

```python
    async def generate(self, prompt: str, timeout: float) -> str:
        """임의 프롬프트를 워커에 보내 원문 출력을 받는다(배치 자막 번역용).

        translate()의 왕복 구조를 그대로 미러하되 요청은 {"id","prompt"},
        응답은 {"id","text"}를 읽는다. 순차 처리(_lock)·총예산 타임아웃 동일.
        """
        if not self.alive:
            raise MlxWorkerUnavailable("worker not running")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        async with self._lock:
            proc = self._proc
            if (proc is None or proc.returncode is not None
                    or proc.stdin is None or proc.stdout is None):
                raise MlxWorkerUnavailable("worker not running")
            self._next_id += 1
            req_id = self._next_id
            req = {"id": req_id, "prompt": prompt}
            try:
                proc.stdin.write((json.dumps(req, ensure_ascii=False) + "\n").encode())
                await proc.stdin.drain()
            except (ConnectionError, BrokenPipeError, RuntimeError) as exc:
                raise MlxWorkerUnavailable(f"worker pipe closed: {exc}") from exc
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise asyncio.TimeoutError()
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
                if not line:  # EOF = 워커 사망
                    raise MlxWorkerUnavailable("worker died mid-request")
                try:
                    resp = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if resp.get("id") == req_id:
                    return str(resp.get("text", ""))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest apps/server/tests/test_mlx_worker_client_generate.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run existing MLX live tests for regression**

Run: `python -m pytest apps/server/tests/test_mlx_live_translate.py -v`
Expected: PASS (기존과 동일 — model_id 기본값 None이라 라이브 경로 불변).

- [ ] **Step 6: Commit**

```bash
git add apps/server/ai/mlx_live_translate.py apps/server/tests/test_mlx_worker_client_generate.py
git commit -m "feat(video/mlx): add model_id arg and generate() to MlxWorkerClient"
```

---

### Task 3: QwenMlxTranslator + 게이팅 (translate_mlx.py 신규)

**Files:**
- Create: `apps/server/domain/video_captions/translate_mlx.py`
- Test: `apps/server/tests/test_translate_mlx.py` (신규)

**Interfaces:**
- Consumes: `MlxWorkerClient(model_id=..., generate=...)` (Task 2), `build_translation_prompt`(translate.py), `_extract_json_array`·`TranslationError`(translate_cli.py), `guard_mlx_ko`·`mlx_model_installed`(mlx_live_translate.py), `_is_apple_silicon_mac`(apple_native.py).
- Produces:
  - `QWEN_MLX_MODELS: dict[str, str]` = `{"qwen": "mlx-community/Qwen3.5-9B-4bit", "qwen_lite": "mlx-community/Qwen3.5-4B-4bit"}`.
  - `qwen_mlx_available(model_id: str) -> bool`.
  - `class QwenMlxTranslator` with `translate_batch(texts: list[str]) -> list[str]` and `async def aclose()`.

- [ ] **Step 1: Write the failing test**

`apps/server/tests/test_translate_mlx.py`:

```python
from __future__ import annotations

import json

import pytest

from apps.server.domain.video_captions import translate_mlx as tm
from apps.server.domain.video_captions.translate import TranslationError


class _FakeClient:
    """MlxWorkerClient 시늉 — generate만 사용."""
    def __init__(self, *, model_id=None, reply="", start_error=False):
        self._reply = reply
        self._start_error = start_error
        self.model_id = model_id
        self.alive = False
        self.closed = False
        self.prompts: list[str] = []

    async def start(self):
        if self._start_error:
            from apps.server.ai.mlx_live_translate import MlxWorkerUnavailable
            raise MlxWorkerUnavailable("no model")
        self.alive = True

    async def generate(self, prompt, timeout):
        self.prompts.append(prompt)
        return self._reply

    async def close(self):
        self.closed = True
        self.alive = False


def _translator(reply="", start_error=False):
    def factory(*, model_id=None, **kw):
        return _FakeClient(model_id=model_id, reply=reply, start_error=start_error)
    return tm.QwenMlxTranslator("mlx-community/Qwen3.5-9B-4bit", client_factory=factory)


async def test_empty_returns_empty():
    out = await _translator().translate_batch([])
    assert out == []


async def test_batch_parses_json_array():
    t = _translator(reply='["안녕","잘 가"]')
    out = await t.translate_batch(["hello", "goodbye"])
    assert out == ["안녕", "잘 가"]
    await t.aclose()


async def test_worker_reused_across_calls():
    t = _translator(reply='["가"]')
    await t.translate_batch(["a"])
    client_after_first = t._client
    await t.translate_batch(["b"])
    assert t._client is client_after_first  # 재기동 없음


async def test_unparseable_raises_translation_error():
    t = _translator(reply="not json at all")
    with pytest.raises(TranslationError):
        await t.translate_batch(["hello"])


async def test_count_mismatch_raises_translation_error():
    t = _translator(reply='["one"]')
    with pytest.raises(TranslationError):
        await t.translate_batch(["a", "b"])


async def test_start_failure_raises_translation_error():
    t = _translator(start_error=True)
    with pytest.raises(TranslationError):
        await t.translate_batch(["hello"])


async def test_guard_reject_keeps_source():
    # 반복 환각(같은 10자+ 구절 3회) → guard 리젝트 → 원문(EN) 유지
    bad = "가나다라마바사아자차" * 3
    t = _translator(reply=json.dumps([bad], ensure_ascii=False))
    out = await t.translate_batch(["source english"])
    assert out == ["source english"]


def test_available_gating(monkeypatch):
    monkeypatch.setattr(tm, "_is_apple_silicon_mac", lambda: True)
    monkeypatch.setattr(tm, "mlx_model_installed", lambda mid: True)
    assert tm.qwen_mlx_available("mlx-community/Qwen3.5-9B-4bit") is True
    monkeypatch.setattr(tm, "mlx_model_installed", lambda mid: False)
    assert tm.qwen_mlx_available("mlx-community/Qwen3.5-9B-4bit") is False
    monkeypatch.setattr(tm, "mlx_model_installed", lambda mid: True)
    monkeypatch.setattr(tm, "_is_apple_silicon_mac", lambda: False)
    assert tm.qwen_mlx_available("mlx-community/Qwen3.5-9B-4bit") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest apps/server/tests/test_translate_mlx.py -v`
Expected: FAIL — `translate_mlx` 모듈 없음(`ModuleNotFoundError`).

- [ ] **Step 3: Write minimal implementation**

`apps/server/domain/video_captions/translate_mlx.py`:

```python
# === ANCHOR: TRANSLATE_MLX_START ===
"""로컬 MLX Qwen 배치 번역 (translate.py의 TranslationProvider plug point).

라이브 자막용 MLX 워커(mlx_worker.run_worker)를 잡당 1회 기동·유지하며,
build_translation_prompt(글로서리+의성어+간결 자막 지시)를 raw 프롬프트로 보내
JSON 배열 KO를 받는다. 서브프로세스 격리(크래시·메모리)로 라이브와 동일 아키텍처.
실리콘맥 전용 — mlx-lm/모델은 워커 안에서만 로드된다.

QWEN_MLX_MODELS는 serverConfig.ts의 MLX_MODELS와 동일하게 유지해야 한다.
"""
from __future__ import annotations

import logging

from apps.server.ai.apple_native import _is_apple_silicon_mac
from apps.server.ai.mlx_live_translate import (
    MlxWorkerClient,
    MlxWorkerUnavailable,
    guard_mlx_ko,
    mlx_model_installed,
)
from .translate import TranslationError, build_translation_prompt
from .translate_cli import _extract_json_array

logger = logging.getLogger("yeson.video.translate_mlx")

# provider 값 → MLX model id. serverConfig.ts MLX_MODELS와 동기화.
QWEN_MLX_MODELS: dict[str, str] = {
    "qwen": "mlx-community/Qwen3.5-9B-4bit",
    "qwen_lite": "mlx-community/Qwen3.5-4B-4bit",
}

DEFAULT_BATCH_TIMEOUT = 300.0


def qwen_mlx_available(model_id: str) -> bool:
    """실리콘맥이고 해당 MLX 모델이 설치되어 있는가.

    MLX 번역은 Apple STT 바이너리·macOS 26과 무관하므로 apple_stt_available()을
    쓰지 않고 실리콘 여부 + 모델 설치만 본다.
    """
    return _is_apple_silicon_mac() and mlx_model_installed(model_id)


class QwenMlxTranslator:
    """TranslationProvider — 로컬 MLX Qwen 배치 번역. 워커는 지연 기동·유지, aclose로 종료."""

    def __init__(self, model_id: str, *, client_factory=None,
                 timeout: float = DEFAULT_BATCH_TIMEOUT):
        self._model_id = model_id
        self._client_factory = client_factory or MlxWorkerClient
        self._timeout = timeout
        self._client = None

    async def _ensure_client(self):
        if self._client is not None and getattr(self._client, "alive", False):
            return self._client
        try:
            client = self._client_factory(model_id=self._model_id)
            await client.start()
        except MlxWorkerUnavailable as exc:
            raise TranslationError(f"MLX 워커 기동 실패: {exc}") from exc
        self._client = client
        return client

    async def translate_batch(self, texts: list[str]) -> list[str]:
        if not texts:
            return []
        client = await self._ensure_client()
        prompt = build_translation_prompt(texts)
        try:
            raw = await client.generate(prompt, timeout=self._timeout)
        except (MlxWorkerUnavailable, TimeoutError) as exc:
            raise TranslationError(f"MLX 배치 번역 실패: {exc}") from exc
        out = _extract_json_array(raw, len(texts))
        if out is None:
            raise TranslationError(f"MLX 번역 출력 파싱 실패: {raw[:200]!r}")
        # 환각 가드: 불합격 줄은 원문(EN) 유지(검수 단계에서 눈에 띄게).
        guarded: list[str] = []
        for src, ko in zip(texts, out):
            reason = guard_mlx_ko(src, ko)
            if reason is not None:
                logger.info("mlx_video_guard_reject reason=%s src=%r", reason, src[:60])
                guarded.append(src)
            else:
                guarded.append(ko)
        return guarded

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.close()
# === ANCHOR: TRANSLATE_MLX_END ===
```

참고: `asyncio.TimeoutError`는 Python 3.11+에서 `TimeoutError`의 별칭이므로 `except (..., TimeoutError)`가 워커 타임아웃을 포섭한다.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest apps/server/tests/test_translate_mlx.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add apps/server/domain/video_captions/translate_mlx.py apps/server/tests/test_translate_mlx.py
git commit -m "feat(video/mlx): add QwenMlxTranslator batch provider + gating"
```

---

### Task 4: 번역 목록 + 라우팅에 qwen/qwen_lite 추가

**Files:**
- Modify: `apps/server/domain/video_captions/translate_cli.py:94-111` (`list_translate_engines`), `:247-290` (`create_translator`)
- Test: `apps/server/tests/test_video_translate_cli.py` (기존 파일에 추가)

**Interfaces:**
- Consumes: `QWEN_MLX_MODELS`, `qwen_mlx_available`, `QwenMlxTranslator` (Task 3).
- Produces: `list_translate_engines()`에 `qwen`·`qwen_lite` 항목; `create_translator("qwen")` → `QwenMlxTranslator`.

- [ ] **Step 1: Write the failing test**

`apps/server/tests/test_video_translate_cli.py` 하단에 추가:

```python
def test_list_engines_includes_qwen(monkeypatch):
    from apps.server.domain.video_captions import translate_mlx as tm
    monkeypatch.setattr(tm, "qwen_mlx_available", lambda mid: True)
    engines = tc.list_translate_engines()
    values = {e["value"] for e in engines}
    assert "qwen" in values
    assert "qwen_lite" in values
    qwen = next(e for e in engines if e["value"] == "qwen")
    assert qwen["available"] is True


def test_list_engines_qwen_unavailable(monkeypatch):
    from apps.server.domain.video_captions import translate_mlx as tm
    monkeypatch.setattr(tm, "qwen_mlx_available", lambda mid: False)
    engines = tc.list_translate_engines()
    qwen = next(e for e in engines if e["value"] == "qwen")
    assert qwen["available"] is False


def test_create_translator_qwen():
    from apps.server.domain.video_captions.translate_mlx import QwenMlxTranslator
    translator = tc.create_translator(provider="qwen")
    assert isinstance(translator, QwenMlxTranslator)
    assert translator._model_id == "mlx-community/Qwen3.5-9B-4bit"


def test_create_translator_qwen_lite():
    from apps.server.domain.video_captions.translate_mlx import QwenMlxTranslator
    translator = tc.create_translator(provider="qwen_lite")
    assert isinstance(translator, QwenMlxTranslator)
    assert translator._model_id == "mlx-community/Qwen3.5-4B-4bit"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest apps/server/tests/test_video_translate_cli.py -k qwen -v`
Expected: FAIL — 목록에 qwen 없음 / `create_translator("qwen")`가 "알 수 없는 provider"로 `TranslationError`.

- [ ] **Step 3: Write minimal implementation**

`list_translate_engines()`의 `return [ ... ]` 안, `apple_hifi` 딕셔너리 뒤에 2항목 추가(지연 import는 함수 첫 줄에):

```python
def list_translate_engines() -> list[dict]:
    """클라 드롭다운용 — 서버에서 사용 가능한 번역 엔진과 설치 여부."""
    from .translate_mlx import QWEN_MLX_MODELS, qwen_mlx_available
    return [
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
        {"value": "qwen", "label": "Qwen 9B (MLX 로컬)",
         "available": qwen_mlx_available(QWEN_MLX_MODELS["qwen"])},
        {"value": "qwen_lite", "label": "Qwen 4B (MLX 로컬·빠름)",
         "available": qwen_mlx_available(QWEN_MLX_MODELS["qwen_lite"])},
    ]
```

`create_translator()`의 `if provider == "apple_hifi":` 블록 뒤(약 269행, `if provider in _BACKENDS:` 앞)에 추가:

```python
    if provider in QWEN_MLX_MODELS:
        from .translate_mlx import QwenMlxTranslator
        return QwenMlxTranslator(QWEN_MLX_MODELS[provider])
```

그리고 `create_translator` 함수 첫 줄(또는 위 분기 직전)에서 매핑을 지연 import:

```python
    from .translate_mlx import QWEN_MLX_MODELS
```

(함수 상단 `provider = ...` 계산 직후에 두어 `if provider in QWEN_MLX_MODELS:` 에서 참조 가능하게.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest apps/server/tests/test_video_translate_cli.py -v`
Expected: PASS (기존 + 신규 4개 모두 통과).

- [ ] **Step 5: Commit**

```bash
git add apps/server/domain/video_captions/translate_cli.py apps/server/tests/test_video_translate_cli.py
git commit -m "feat(video/mlx): expose qwen/qwen_lite in translate engines + routing"
```

---

### Task 5: 파이프라인 잡 종료 시 워커 aclose

**Files:**
- Modify: `apps/server/domain/video_captions/pipeline.py:287-290`
- Test: `apps/server/tests/test_video_pipeline.py` (기존 파일에 추가)

**Interfaces:**
- Consumes: `QwenMlxTranslator.aclose()` (Task 3) — 옵셔널 프로토콜(`getattr`).
- Produces: 없음(생명주기 정리만). 다른 번역기(gemini/CLI/apple)는 `aclose` 없어 무영향.

- [ ] **Step 1: Write the failing test**

`apps/server/tests/test_video_pipeline.py` 하단에 추가. 파이프라인 전체를 돌리지 않고, 정리 로직을 검증하는 헬퍼 단위 테스트로 좁힌다:

```python
def test_translator_aclose_helper_calls_when_present():
    """pipeline이 쓰는 옵셔널 aclose 정리 규약을 검증."""
    import asyncio

    from apps.server.domain.video_captions.pipeline import _maybe_aclose_translator

    class WithAclose:
        def __init__(self):
            self.closed = False

        async def aclose(self):
            self.closed = True

    class WithoutAclose:
        pass

    t1 = WithAclose()
    asyncio.get_event_loop().run_until_complete(_maybe_aclose_translator(t1))
    assert t1.closed is True

    # aclose 없는 번역기는 예외 없이 무시
    asyncio.get_event_loop().run_until_complete(_maybe_aclose_translator(WithoutAclose()))
```

(참고: 기존 test_video_pipeline.py가 `pytest.mark.asyncio`/async 스타일이면 그에 맞춰 `async def` + `await _maybe_aclose_translator(...)`로 작성. 위는 동기 스타일 예시.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest apps/server/tests/test_video_pipeline.py -k aclose -v`
Expected: FAIL — `_maybe_aclose_translator` 없음(`ImportError`).

- [ ] **Step 3: Write minimal implementation**

`apps/server/domain/video_captions/pipeline.py`에 헬퍼 추가(모듈 상단, import 뒤 적당한 위치):

```python
async def _maybe_aclose_translator(translator) -> None:
    """서브프로세스 워커를 쓰는 번역기(QwenMlxTranslator)는 잡 종료 시 정리한다.
    aclose가 없는 번역기(gemini/CLI/apple)는 무시한다."""
    aclose = getattr(translator, "aclose", None)
    if aclose is not None:
        await aclose()
```

그리고 287-290 블록을 try/finally로 감싼다:

```python
        translator = create_translator(
            provider=translate_provider, cli_model=translate_cli_model)
        try:
            ko_segments = await translate_segments(
                en_segments, translator, progress_cb=on_translate_progress)
        finally:
            await _maybe_aclose_translator(translator)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest apps/server/tests/test_video_pipeline.py -v`
Expected: PASS (기존 + 신규).

- [ ] **Step 5: Commit**

```bash
git add apps/server/domain/video_captions/pipeline.py apps/server/tests/test_video_pipeline.py
git commit -m "feat(video/mlx): close MLX worker on job completion"
```

---

### Task 6: 전체 서버 테스트 회귀 + 문서 체크박스

**Files:**
- Modify: 없음(검증). 필요 시 `docs/` ROADMAP/PRD 자막메이커 섹션 체크박스.

- [ ] **Step 1: 전체 서버 테스트**

Run: `python -m pytest apps/server/tests -q`
Expected: 전부 PASS(신규 포함, 기존 회귀 0). mlx 실모델 로드 없음.

- [ ] **Step 2: import 오염 점검(인텔/리눅스 안전)**

Run: `python -c "import apps.server.domain.video_captions.translate_cli as t; print([e['value'] for e in t.list_translate_engines()])"`
Expected: `qwen`/`qwen_lite` 포함 목록 출력, `mlx_lm` import 에러 없음(워커 밖에서는 mlx import 안 함).

- [ ] **Step 3: (해당 시) 문서 체크박스 갱신 + 커밋**

자막메이커 관련 ROADMAP/PRD에 "MLX Qwen 번역 옵션" 항목이 있으면 체크. 없으면 스킵.

```bash
git add -A
git commit -m "docs(video/mlx): mark qwen translate option done" || echo "no docs change"
```

---

## 수동 검증 (실리콘맥 실기기 — 자동 테스트 밖)

1. 서버 콘솔 `MlxModelPanel`에서 Qwen 9B 다운로드(미설치 시).
2. 자막메이커 클라 번역 엔진에서 "Qwen 9B (MLX 로컬)" 활성 확인, 4B는 미설치면 비활성.
3. 짧은 영상 base 전사 + Qwen 9B 번역 완주 → 자막 품질/의성어/용어 확인.
4. 인텔맥/윈도우 빌드에서 두 항목 비활성으로만 노출 + 서버 정상 기동.

---

## Self-Review

**Spec coverage:**
- 번역 목록 2항목 + 게이팅 → Task 4 + Task 3(`qwen_mlx_available`). ✔
- QwenMlxTranslator(배치·글로서리·의성어·가드) → Task 3. ✔
- 워커 raw-generate additive → Task 1. ✔
- MlxWorkerClient model_id + generate → Task 2. ✔
- create_translator 라우팅 → Task 4. ✔
- 파이프라인 aclose → Task 5. ✔
- 프론트 무변경(서버 available 자동 비활성) → 코드 태스크 없음(의도), 수동 검증 2·4로 확인. ✔
- 다운로드는 기존 서버 콘솔 재사용(비목표) → 태스크 없음(의도). ✔
- 모델 카탈로그 동기화(py↔ts) → Global Constraints + Task 3 주석. ✔

**Placeholder scan:** 코드 스텝은 실제 코드 포함. Task 5의 async 스타일 주석은 "기존 파일 관례에 맞추라"는 구체 지시(플레이스홀더 아님). ✔

**Type consistency:** `QWEN_MLX_MODELS`(dict), `qwen_mlx_available(model_id)->bool`, `QwenMlxTranslator(model_id, *, client_factory, timeout)`·`translate_batch(list)->list`·`aclose()`, `MlxWorkerClient(model_id=...)`·`generate(prompt, timeout)->str`, `_maybe_aclose_translator(translator)` — Task 1~5 전반 일치. ✔
