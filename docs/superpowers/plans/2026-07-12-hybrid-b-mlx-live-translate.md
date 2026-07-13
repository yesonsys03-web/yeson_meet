# 하이브리드 B (전사 Apple + 번역 MLX) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 라이브 자막 파이프라인에서 전사는 기존 Apple 경로를 유지하고, 파이널 문장의 번역만 MLX 로컬 LLM(Qwen3.5-9B-4bit) 워커 서브프로세스로 교체한다 — 완전 오프라인, 환각 가드 + Apple KO 폴백, 자막 무중단.

**Architecture:** 새 데코레이터 프로바이더 `MlxRefinedAppleProvider`가 기존 `AppleLiveTranslateProvider`를 감싼다(파셜 통과, 파이널 홀드→MLX 정제→가드→확정). MLX는 패키징 서버 바이너리가 자기 자신을 env 플래그(`YESON_MLX_WORKER=1`)로 재실행한 워커 프로세스에서 mlx-lm으로 실행되고, JSONL stdin/stdout로 통신한다. 스펙: `docs/superpowers/specs/2026-07-12-hybrid-b-mlx-live-translate-design.md`

**Tech Stack:** Python 3.12 / asyncio / mlx-lm ≥0.31 / huggingface_hub / FastAPI 서버(기존) / Tauri(Rust+React) 서버 콘솔 / PyInstaller onedir

## Global Constraints

- Swift `apple-live-translate` 바이너리와 `AppleLiveTranslateProvider`, `AudioLiveSession`은 무변경.
- 데코레이터는 예외를 위로 던지지 않는다(inner 스트림 예외 재전파 제외) — 워커가 어떤 식으로 죽어도 Apple KO로 자막 계속.
- seq/provider_segment 무변경 통과 (AISequenceNormalizer 계약 유지).
- `mlx`/`mlx-lm` import는 워커 진입점(`run_worker`)·다운로드 진입점 내부에서만 (서버 본체는 mlx 없는 환경에서 임포트 가능해야 함 — 인텔 빌드 회귀 방지).
- 워커 프로세스 env에 `HF_HUB_OFFLINE=1` (회의 중 네트워크 0).
- 프로바이더 명: `apple_mlx_live_translate` (별칭 `apple_mlx`). 기본 모델: `mlx-community/Qwen3.5-9B-4bit`.
- 모델 저장 위치: `{STORAGE_ROOT}/mlx_models/<model_id의 '/'를 '--'로 치환>/`.
- 상수: 문장 타임아웃 6.0s, 홀드 상한 3문장, ready 타임아웃 120s, 워커 재스폰 최대 2회.
- 새 Python 파일은 앵커 주석(`# === ANCHOR: NAME_START/END ===`)으로 감싼다 (VibeLign 규칙).
- 커밋 메시지는 한국어, 기존 스타일(`feat(apple-live): …`)을 따르고 Co-Authored-By 트레일러를 붙인다.

## File Structure

- Create: `apps/server/ai/mlx_live_translate.py` — 가드 함수, 모델 경로/게이팅, `MlxWorkerClient`, `MlxRefinedAppleProvider`
- Create: `apps/server/ai/mlx_worker.py` — 워커 루프(`run_worker`) + 다운로드(`run_download`) (mlx-lm 지연 import)
- Create: `apps/server/tests/test_mlx_live_translate.py`, `apps/server/tests/test_mlx_worker.py`
- Modify: `apps/server/ws/sidecar.py` — `create_ai_provider()` 분기 1개
- Modify: `apps/server_desktop/sidecar/server_entry.py` — 원샷 모드 분기 2개 (worker/download)
- Modify: `apps/server/pyproject.toml` — `[project.optional-dependencies] mlx`
- Modify: `apps/server_desktop/scripts/build-server.sh` — arm 분기에서 mlx extra 설치 + collect
- Modify: `apps/server_desktop/src-tauri/src/server_config.rs` — `yeson_mlx_model` 필드
- Modify: `apps/server_desktop/src-tauri/src/server_process.rs` — env 주입 + `mlx_model_status`/`mlx_download_model` 커맨드
- Modify: `apps/server_desktop/src/setup/serverConfig.ts` — 타입/invoke 래퍼
- Create: `apps/server_desktop/src/setup/MlxModelPanel.tsx` — 모델 관리 UI
- Modify: `apps/server_desktop/src/setup/ServerConfigPanel.tsx` — 패널 삽입 + provider 옵션

---

### Task 1: 환각 가드 함수

**Files:**
- Create: `apps/server/ai/mlx_live_translate.py`
- Test: `apps/server/tests/test_mlx_live_translate.py`

**Interfaces:**
- Produces: `guard_mlx_ko(en: str, ko: str) -> str | None` — 통과 시 `None`, 불합격 시 사유 문자열(`"foreign_script" | "invented_number" | "length_ratio" | "english_leak" | "repetition" | "empty"`). Task 5가 사용.

- [ ] **Step 1: 실패하는 테스트 작성** — 2026-07-12 벤치 실측 실패 사례를 픽스처로 사용

```python
# apps/server/tests/test_mlx_live_translate.py
# === ANCHOR: TEST_MLX_LIVE_TRANSLATE_START ===
from __future__ import annotations

from apps.server.ai.mlx_live_translate import guard_mlx_ko


class TestGuardMlxKo:
    def test_clean_translation_passes(self):
        assert guard_mlx_ko(
            "And I put all of my projects in my documents folder.",
            "그리고 저는 모든 프로젝트를 문서 폴더에 저장합니다.",
        ) is None

    def test_partial_english_terms_allowed(self):
        # 기술 자막에서 흔한 부분 영어 잔존은 허용
        assert guard_mlx_ko(
            "Please turn this into a landing page.",
            "이걸 landing page로 만들어 주세요.",
        ) is None

    def test_cjk_hanzi_rejected(self):
        assert guard_mlx_ko("So this is codex.", "이것이 코다克斯입니다.") == "foreign_script"

    def test_kana_rejected(self):
        assert guard_mlx_ko("Let's do it.", "해보ましょう.") == "foreign_script"

    def test_cyrillic_rejected(self):
        assert guard_mlx_ko("Open codex.", "코드КС를 여세요.") == "foreign_script"

    def test_replacement_char_rejected(self):
        assert guard_mlx_ko("Open it.", "여세요�.") == "foreign_script"

    def test_invented_number_rejected(self):
        # 벤치 실측: EN에 숫자가 없는데 "53만 달러" 환각
        assert guard_mlx_ko(
            "I will create a new project.", "53만 달러로 새 프로젝트를 만들 것입니다."
        ) == "invented_number"

    def test_number_present_in_en_passes(self):
        assert guard_mlx_ko("On base 44.", "베이스 44에서요.") is None

    def test_en_digit_missing_in_ko_allowed(self):
        # KO가 숫자를 한글로 풀어쓴 경우 허용 (EN→KO 방향 누락은 통과)
        assert guard_mlx_ko("It takes 2 minutes.", "이 분 정도 걸립니다.") is None

    def test_empty_rejected(self):
        assert guard_mlx_ko("Hello there.", "") == "empty"
        assert guard_mlx_ko("Hello there.", "   ") == "empty"

    def test_length_explosion_rejected(self):
        assert guard_mlx_ko("Hi.", "이 문장은 원문보다 지나치게 길어진 설명 폭주 사례입니다." * 3) == "length_ratio"

    def test_length_collapse_rejected(self):
        long_en = "And I can say, please, turn this into a landing page, a good learning resource for my viewers."
        assert guard_mlx_ko(long_en, "네.") == "length_ratio"

    def test_english_leak_rejected(self):
        assert guard_mlx_ko(
            "I can mention any file created within this folder.",
            "I can mention any file 폴더.",
        ) == "english_leak"

    def test_repetition_rejected(self):
        # 벤치 실측: "분류하고 분류하여" 류 반복 붕괴
        chunk = "분류하고 정리하여 저장하는 "
        assert guard_mlx_ko(
            "Sort and organize the files in the folder now.", chunk * 4
        ) == "repetition"
# === ANCHOR: TEST_MLX_LIVE_TRANSLATE_END ===
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest apps/server/tests/test_mlx_live_translate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.server.ai.mlx_live_translate'`

- [ ] **Step 3: 최소 구현**

```python
# apps/server/ai/mlx_live_translate.py
# === ANCHOR: MLX_LIVE_TRANSLATE_START ===
"""하이브리드 B: 파이널 번역만 MLX 로컬 LLM으로 정제하는 데코레이터 프로바이더.

스펙: docs/superpowers/specs/2026-07-12-hybrid-b-mlx-live-translate-design.md
- 파셜은 inner(Apple) 그대로 통과, 파이널은 홀드 후 MLX KO로 확정(가드 통과 시).
- 가드 불합격/타임아웃/워커 사망/백로그 초과 → Apple KO 폴백. 자막 무중단이 최우선.
"""
from __future__ import annotations

import re

# --- 환각 가드 --------------------------------------------------------------
# 2026-07-12 벤치 실측 실패 유형을 각각 겨냥한 5규칙. 전부 정규식/문자열 연산.
_FOREIGN_RE = re.compile(
    "[一-鿿"      # CJK 한자
    "぀-ヿ"        # 히라가나+가타카나
    "Ѐ-ӿ"        # 키릴
    "฀-๿"        # 태국 문자
    "�]"              # 깨진 문자
)
_DIGIT_RUN_RE = re.compile(r"\d+")
_ASCII_ALPHA_RE = re.compile(r"[A-Za-z]")
# 10자 이상 구절이 (원본 포함) 3회 이상 등장 = 같은 구절이 2회 더 반복
_REPEAT_RE = re.compile(r"(.{10,}?)(?:.*?\1){2,}", re.DOTALL)

_LEN_RATIO_MIN = 0.2
_LEN_RATIO_MAX = 3.0
_ASCII_LEAK_MAX = 0.6


def guard_mlx_ko(en: str, ko: str) -> str | None:
    """MLX 번역 결과 검증. 통과 시 None, 불합격 시 사유 문자열."""
    ko_stripped = ko.strip()
    if not ko_stripped:
        return "empty"
    if _FOREIGN_RE.search(ko_stripped):
        return "foreign_script"
    en_digits = set(_DIGIT_RUN_RE.findall(en))
    for run in _DIGIT_RUN_RE.findall(ko_stripped):
        if run not in en_digits:
            return "invented_number"
    ratio = len(ko_stripped) / max(1, len(en.strip()))
    if not (_LEN_RATIO_MIN <= ratio <= _LEN_RATIO_MAX):
        return "length_ratio"
    ascii_alpha = len(_ASCII_ALPHA_RE.findall(ko_stripped))
    if ascii_alpha / max(1, len(ko_stripped)) > _ASCII_LEAK_MAX:
        return "english_leak"
    if _REPEAT_RE.search(ko_stripped):
        return "repetition"
    return None
# === ANCHOR: MLX_LIVE_TRANSLATE_END ===
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest apps/server/tests/test_mlx_live_translate.py -v`
Expected: PASS (14 tests)

주의: `test_en_digit_missing_in_ko_allowed`의 "이 분"이 length_ratio에 걸리지 않는지 확인 — `len("이 분 정도 걸립니다.")/len("It takes 2 minutes.")` ≈ 0.65로 통과. 실패하는 테스트가 있으면 정규식이 아니라 임계값 상수를 의심할 것 (스펙 값 고정: 0.2/3.0/60%).

- [ ] **Step 5: 커밋**

```bash
git add apps/server/ai/mlx_live_translate.py apps/server/tests/test_mlx_live_translate.py
git commit -m "feat(mlx-live): 환각 가드 5규칙 — 벤치 실측 실패 사례 픽스처 기반

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: 모델 경로 해석 + 게이팅

**Files:**
- Modify: `apps/server/ai/mlx_live_translate.py` (앵커 내부, 가드 아래에 추가)
- Test: `apps/server/tests/test_mlx_live_translate.py` (추가)

**Interfaces:**
- Consumes: `apps.server.ai.apple_native.apple_stt_available()` (기존)
- Produces:
  - `DEFAULT_MLX_MODEL = "mlx-community/Qwen3.5-9B-4bit"`
  - `mlx_model_id() -> str` — env `YESON_MLX_MODEL` 또는 기본값
  - `mlx_model_dir(model_id: str) -> Path` — `{STORAGE_ROOT}/mlx_models/<id '/'→'--'>`
  - `mlx_model_installed(model_id: str) -> bool` — `config.json` 존재 여부
  - `mlx_live_available() -> bool` — apple_stt_available() AND 모델 설치됨

- [ ] **Step 1: 실패하는 테스트 추가**

```python
# apps/server/tests/test_mlx_live_translate.py 에 추가
import os
from apps.server.ai.mlx_live_translate import (
    DEFAULT_MLX_MODEL,
    mlx_live_available,
    mlx_model_dir,
    mlx_model_id,
    mlx_model_installed,
)


class TestModelResolution:
    def test_default_model_id(self, monkeypatch):
        monkeypatch.delenv("YESON_MLX_MODEL", raising=False)
        assert mlx_model_id() == DEFAULT_MLX_MODEL == "mlx-community/Qwen3.5-9B-4bit"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("YESON_MLX_MODEL", "mlx-community/Qwen3.5-4B-4bit")
        assert mlx_model_id() == "mlx-community/Qwen3.5-4B-4bit"

    def test_model_dir_sanitizes_slash(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
        d = mlx_model_dir("mlx-community/Qwen3.5-9B-4bit")
        assert d == tmp_path / "mlx_models" / "mlx-community--Qwen3.5-9B-4bit"

    def test_installed_requires_config_json(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
        model = "mlx-community/Qwen3.5-9B-4bit"
        assert mlx_model_installed(model) is False
        d = mlx_model_dir(model)
        d.mkdir(parents=True)
        (d / "config.json").write_text("{}")
        assert mlx_model_installed(model) is True

    def test_available_needs_both_gates(self, monkeypatch, tmp_path):
        import apps.server.ai.mlx_live_translate as mod
        monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
        monkeypatch.delenv("YESON_MLX_MODEL", raising=False)
        # 모델 미설치 + apple 게이팅 True → False
        monkeypatch.setattr(mod, "apple_stt_available", lambda: True)
        assert mlx_live_available() is False
        # 모델 설치 + apple 게이팅 False → False
        d = mlx_model_dir(DEFAULT_MLX_MODEL)
        d.mkdir(parents=True)
        (d / "config.json").write_text("{}")
        monkeypatch.setattr(mod, "apple_stt_available", lambda: False)
        assert mlx_live_available() is False
        # 둘 다 → True
        monkeypatch.setattr(mod, "apple_stt_available", lambda: True)
        assert mlx_live_available() is True
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest apps/server/tests/test_mlx_live_translate.py::TestModelResolution -v`
Expected: FAIL — `ImportError: cannot import name 'DEFAULT_MLX_MODEL'`

- [ ] **Step 3: 구현** (가드 함수 아래에 추가)

```python
import os
from pathlib import Path

from apps.server.ai.apple_native import apple_stt_available

# --- 모델 해석/게이팅 ---------------------------------------------------------
DEFAULT_MLX_MODEL = "mlx-community/Qwen3.5-9B-4bit"
_MODEL_ENV = "YESON_MLX_MODEL"
_STORAGE_ROOT_ENV = "STORAGE_ROOT"
_DEFAULT_STORAGE_ROOT = "/var/lib/yeson-meet/storage"  # glossary.py와 동일 관례


def mlx_model_id() -> str:
    return os.environ.get(_MODEL_ENV, "").strip() or DEFAULT_MLX_MODEL


def mlx_model_dir(model_id: str) -> Path:
    root = os.environ.get(_STORAGE_ROOT_ENV) or _DEFAULT_STORAGE_ROOT
    return Path(root) / "mlx_models" / model_id.replace("/", "--")


def mlx_model_installed(model_id: str) -> bool:
    return (mlx_model_dir(model_id) / "config.json").is_file()


def mlx_live_available() -> bool:
    """apple 전사 게이팅(macOS 26+/arm/바이너리) + 선택 모델 설치 여부."""
    return apple_stt_available() and mlx_model_installed(mlx_model_id())
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest apps/server/tests/test_mlx_live_translate.py -v`
Expected: PASS (19 tests)

- [ ] **Step 5: 커밋**

```bash
git add apps/server/ai/mlx_live_translate.py apps/server/tests/test_mlx_live_translate.py
git commit -m "feat(mlx-live): 모델 경로 해석(STORAGE_ROOT/mlx_models) + 가용성 게이팅

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: MLX 워커 (페이크 모드 + 실모델 경로 + 진입점 분기)

**Files:**
- Create: `apps/server/ai/mlx_worker.py`
- Modify: `apps/server_desktop/sidecar/server_entry.py` (main()의 원샷 모드 블록)
- Test: `apps/server/tests/test_mlx_worker.py`

**Interfaces:**
- Consumes: env `YESON_MLX_MODEL_PATH`(로컬 모델 디렉터리), `YESON_MLX_FAKE=1`(페이크 모드)
- Produces (JSONL stdout 프로토콜 — Task 4의 클라이언트가 소비):
  - 기동: `{"type":"status","state":"ready"}` 또는 `{"type":"status","state":"error","reason":"..."}` 후 exit 1
  - 요청(stdin): `{"id":1,"en":"...","context":[["en","ko"],...],"glossary":{}}`
  - 응답: `{"id":1,"ko":"...","gen_ms":123}`
  - `run_worker() -> int`, `run_download(model_id: str) -> int` (server_entry에서 호출)

- [ ] **Step 1: 실패하는 테스트 작성** — 페이크 모드를 실제 서브프로세스로 검증

```python
# apps/server/tests/test_mlx_worker.py
# === ANCHOR: TEST_MLX_WORKER_START ===
from __future__ import annotations

import json
import subprocess
import sys


def _spawn_fake_worker():
    return subprocess.Popen(
        [sys.executable, "-c",
         "from apps.server.ai.mlx_worker import run_worker; import sys; sys.exit(run_worker())"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env={"YESON_MLX_FAKE": "1", "PATH": "/usr/bin:/bin"},
    )


class TestFakeWorker:
    def test_ready_then_echo_roundtrip(self):
        proc = _spawn_fake_worker()
        try:
            ready = json.loads(proc.stdout.readline())
            assert ready == {"type": "status", "state": "ready"}
            req = {"id": 7, "en": "Hello there.", "context": [["Hi.", "안녕."]], "glossary": {}}
            proc.stdin.write(json.dumps(req) + "\n")
            proc.stdin.flush()
            resp = json.loads(proc.stdout.readline())
            assert resp["id"] == 7
            assert resp["ko"] == "[fake] Hello there."   # 페이크 = 에코
            assert isinstance(resp["gen_ms"], int)
        finally:
            proc.stdin.close()
            assert proc.wait(timeout=5) == 0  # stdin EOF → 정상 종료

    def test_bad_json_line_ignored(self):
        proc = _spawn_fake_worker()
        try:
            proc.stdout.readline()  # ready
            proc.stdin.write("not-json\n")
            proc.stdin.write(json.dumps({"id": 1, "en": "A.", "context": [], "glossary": {}}) + "\n")
            proc.stdin.flush()
            resp = json.loads(proc.stdout.readline())
            assert resp["id"] == 1  # 깨진 줄은 무시하고 다음 요청 처리
        finally:
            proc.stdin.close()
            proc.wait(timeout=5)

    def test_missing_model_reports_error(self):
        proc = subprocess.Popen(
            [sys.executable, "-c",
             "from apps.server.ai.mlx_worker import run_worker; import sys; sys.exit(run_worker())"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
            env={"YESON_MLX_MODEL_PATH": "/nonexistent/model", "PATH": "/usr/bin:/bin"},
        )
        try:
            status = json.loads(proc.stdout.readline())
            assert status["type"] == "status" and status["state"] == "error"
            assert status["reason"].startswith("missing_mlx_model")
            assert proc.wait(timeout=5) == 1
        finally:
            proc.stdin.close()
# === ANCHOR: TEST_MLX_WORKER_END ===
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest apps/server/tests/test_mlx_worker.py -v`
Expected: FAIL — `ModuleNotFoundError` (워커 서브프로세스가 stdout에 아무것도 못 씀 → json 파싱 에러로 표면화될 수 있음; 어느 쪽이든 FAIL 확인)

주의: 서브프로세스가 `apps.server` 패키지를 찾으려면 repo 루트에서 pytest를 실행해야 한다(기존 테스트와 동일 전제). env에 `PYTHONPATH`가 필요하면 `_spawn_fake_worker`의 env에 `"PYTHONPATH": os.getcwd()`를 추가하라.

- [ ] **Step 3: 워커 구현**

```python
# apps/server/ai/mlx_worker.py
# === ANCHOR: MLX_WORKER_START ===
"""MLX 번역 워커 + 모델 다운로드 원샷 (서버 바이너리 자기-재실행 진입점).

- run_worker(): stdin JSONL 요청 → stdout JSONL 응답. 모델 로드 후 status:ready.
  mlx-lm import는 이 함수 안에서만 (인텔/리눅스 빌드에서 서버 본체 임포트 보호).
- run_download(model_id): huggingface에서 {STORAGE_ROOT}/mlx_models/<id>로 스냅샷
  다운로드, 진행 상황을 JSONL로 stdout에 출력 (콘솔이 파싱).
- YESON_MLX_FAKE=1: 모델 없이 에코 응답 — 프로토콜 테스트/번들 스모크용.
"""
from __future__ import annotations

import json
import os
import sys
import time

_SYSTEM_PROMPT = (
    "You are a professional simultaneous interpreter for a live business meeting. "
    "Translate the current English sentence into natural, fluent Korean. "
    "The English comes from live speech recognition and may contain recognition "
    "errors, disfluencies, or odd punctuation — infer the intended meaning from "
    "context and translate that meaning. Use the preceding dialogue as context. "
    "Use consistent polite Korean (합니다체). "
    "Output ONLY the Korean translation of the current sentence — no quotes, "
    "no explanations."
)


def _build_user(context: list[list[str]], en: str) -> str:
    parts: list[str] = []
    if context:
        parts.append("Preceding dialogue:")
        for c_en, c_ko in context:
            parts.append(f"EN: {c_en}")
            parts.append(f"KO: {c_ko}")
        parts.append("")
    parts.append("Current sentence:")
    parts.append(f"EN: {en}")
    return "\n".join(parts)


def _emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def _make_translate():
    """(en, context) -> ko 클로저를 만든다. 페이크/실모델 분기."""
    if os.environ.get("YESON_MLX_FAKE") == "1":
        return lambda en, context: f"[fake] {en}"

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

    def _translate(en: str, context: list[list[str]]) -> str:
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
        ko = text.strip()
        if "</think>" in ko:
            ko = ko.split("</think>", 1)[1].strip()
        return ko

    return _translate


def run_worker() -> int:
    try:
        translate = _make_translate()
    except SystemExit as exc:
        return int(exc.code or 1)
    _emit({"type": "status", "state": "ready"})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            req_id = req["id"]
            en = str(req["en"])
            context = [[str(a), str(b)] for a, b in req.get("context", [])]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            print(f"mlx-worker: bad request line: {line[:120]}", file=sys.stderr, flush=True)
            continue
        t0 = time.perf_counter()
        try:
            ko = translate(en, context)
        except Exception as exc:  # noqa: BLE001 — 요청 하나의 실패가 워커를 죽이면 안 됨
            print(f"mlx-worker: translate failed: {exc}", file=sys.stderr, flush=True)
            ko = ""
        _emit({"id": req_id, "ko": ko, "gen_ms": round((time.perf_counter() - t0) * 1000)})
    return 0  # stdin EOF = 정상 종료


def run_download(model_id: str) -> int:
    """모델 스냅샷을 {STORAGE_ROOT}/mlx_models/<id>로 받는다. 진행률 JSONL 출력."""
    from apps.server.ai.mlx_live_translate import mlx_model_dir

    target = mlx_model_dir(model_id)
    target.mkdir(parents=True, exist_ok=True)
    _emit({"type": "download", "state": "start", "model": model_id, "dir": str(target)})
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(model_id, local_dir=str(target))
    except Exception as exc:  # noqa: BLE001 — 콘솔에 읽을 수 있는 실패 사유 전달
        _emit({"type": "download", "state": "error", "reason": f"{type(exc).__name__}: {exc}"})
        return 1
    if not (target / "config.json").is_file():
        _emit({"type": "download", "state": "error", "reason": "config.json missing after download"})
        return 1
    _emit({"type": "download", "state": "done", "model": model_id})
    return 0
# === ANCHOR: MLX_WORKER_END ===
```

- [ ] **Step 4: server_entry 분기 추가** — `apps/server_desktop/sidecar/server_entry.py`의 `main()`에서 기존 원샷 모드 블록(`YESON_BOOTSTRAP_ADMIN` 분기) **바로 위**에 삽입:

```python
    # MLX 번역 워커 모드: 모델 로드 후 stdin JSONL 루프 (uvicorn 없음).
    if os.environ.get("YESON_MLX_WORKER") == "1":
        from apps.server.ai.mlx_worker import run_worker

        return run_worker()

    # MLX 모델 다운로드 원샷: 진행률 JSONL 출력 후 종료 (uvicorn 없음).
    mlx_download = os.environ.get("YESON_MLX_DOWNLOAD", "")
    if mlx_download:
        from apps.server.ai.mlx_worker import run_download

        return run_download(mlx_download)
```

(둘 다 DB 접근이 없으므로 `DATABASE_URL` 설정 이후 어디든 무방하나, 가장 이른 원샷 위치에 둔다.)

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest apps/server/tests/test_mlx_worker.py -v`
Expected: PASS (3 tests)

Run(진입점 수동 확인): `YESON_MLX_FAKE=1 YESON_MLX_WORKER=1 uv run python -m apps.server_desktop.sidecar.server_entry <<< '{"id":1,"en":"Hi.","context":[],"glossary":{}}'`
Expected: `{"type": "status", "state": "ready"}` 줄과 `{"id": 1, "ko": "[fake] Hi.", ...}` 줄 출력 후 종료코드 0

- [ ] **Step 6: 커밋**

```bash
git add apps/server/ai/mlx_worker.py apps/server/tests/test_mlx_worker.py apps/server_desktop/sidecar/server_entry.py
git commit -m "feat(mlx-live): MLX 워커/다운로드 원샷 진입점 — JSONL 프로토콜 + 페이크 모드

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: 워커 클라이언트 (spawn/ready/요청 상관/사망 감지)

**Files:**
- Modify: `apps/server/ai/mlx_live_translate.py`
- Test: `apps/server/tests/test_mlx_live_translate.py` (추가)

**Interfaces:**
- Consumes: Task 3의 워커 JSONL 프로토콜
- Produces (Task 5가 사용):
  - `class MlxWorkerUnavailable(RuntimeError)` — 기동 실패(영구성은 호출자가 판단하지 않음; 데코레이터는 폴백만 한다)
  - `class MlxWorkerClient:`
    - `__init__(self, argv: list[str] | None = None, ready_timeout: float = 120.0)` — argv=None이면 자기 재실행 argv 계산(frozen: `[sys.executable]`, dev: `[sys.executable, "-m", "apps.server_desktop.sidecar.server_entry"]`), env에 `YESON_MLX_WORKER=1`, `YESON_MLX_MODEL_PATH=<mlx_model_dir(mlx_model_id())>`, `HF_HUB_OFFLINE=1` 주입
    - `async def start(self) -> None` — ready 대기, 실패 시 `MlxWorkerUnavailable`
    - `async def translate(self, en: str, context: list[tuple[str, str]], timeout: float) -> str` — 응답 ko 반환; 워커 사망 시 `MlxWorkerUnavailable`, 초과 시 `asyncio.TimeoutError`
    - `alive: bool` (property)
    - `async def close(self) -> None` — stdin EOF 후 kill 보증

- [ ] **Step 1: 실패하는 테스트 추가** — 테스트는 argv 오버라이드로 페이크 워커/불량 워커를 주입 (apple_live_translate 테스트의 `_fake_bin` 패턴)

```python
# apps/server/tests/test_mlx_live_translate.py 에 추가
import asyncio
import sys
import textwrap

import pytest

from apps.server.ai.mlx_live_translate import MlxWorkerClient, MlxWorkerUnavailable


def _script_argv(tmp_path, body: str) -> list[str]:
    script = tmp_path / "fake_worker.py"
    script.write_text(textwrap.dedent(body))
    return [sys.executable, str(script)]


ECHO_WORKER = """\
    import json, sys
    print(json.dumps({"type": "status", "state": "ready"}), flush=True)
    for line in sys.stdin:
        req = json.loads(line)
        print(json.dumps({"id": req["id"], "ko": "KO:" + req["en"], "gen_ms": 1}), flush=True)
"""

NEVER_READY_WORKER = """\
    import time
    time.sleep(60)
"""

DIES_AFTER_READY_WORKER = """\
    import json, sys
    print(json.dumps({"type": "status", "state": "ready"}), flush=True)
    sys.exit(9)
"""


class TestMlxWorkerClient:
    def test_start_and_translate(self, tmp_path):
        async def run():
            client = MlxWorkerClient(argv=_script_argv(tmp_path, ECHO_WORKER))
            await client.start()
            assert client.alive
            ko = await client.translate("Hello.", [("Hi.", "안녕.")], timeout=5.0)
            assert ko == "KO:Hello."
            await client.close()
            assert not client.alive
        asyncio.run(run())

    def test_ready_timeout_raises_unavailable(self, tmp_path):
        async def run():
            client = MlxWorkerClient(
                argv=_script_argv(tmp_path, NEVER_READY_WORKER), ready_timeout=0.5)
            with pytest.raises(MlxWorkerUnavailable):
                await client.start()
            assert not client.alive
        asyncio.run(run())

    def test_death_during_translate_raises_unavailable(self, tmp_path):
        async def run():
            client = MlxWorkerClient(argv=_script_argv(tmp_path, DIES_AFTER_READY_WORKER))
            await client.start()
            with pytest.raises(MlxWorkerUnavailable):
                await client.translate("Hello.", [], timeout=5.0)
        asyncio.run(run())
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest apps/server/tests/test_mlx_live_translate.py::TestMlxWorkerClient -v`
Expected: FAIL — `ImportError: cannot import name 'MlxWorkerClient'`

- [ ] **Step 3: 구현** (mlx_live_translate.py에 추가)

```python
import asyncio
import contextlib
import json
import logging
import sys

logger = logging.getLogger("yeson.ai.mlx_live_translate")

DEFAULT_READY_TIMEOUT_SECONDS = 120.0  # 5GB 모델 콜드 페이지-인 감안 (스펙)


class MlxWorkerUnavailable(RuntimeError):
    """워커 기동 실패/사망 — 데코레이터는 Apple KO 폴백으로만 대응한다."""


def _worker_argv() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]  # PyInstaller 번들: 자기 자신 재실행
    return [sys.executable, "-m", "apps.server_desktop.sidecar.server_entry"]


class MlxWorkerClient:
    def __init__(self, argv: list[str] | None = None,
                 ready_timeout: float = DEFAULT_READY_TIMEOUT_SECONDS) -> None:
        self._argv = argv
        self._ready_timeout = ready_timeout
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._lock = asyncio.Lock()  # 요청은 순차 — 워커도 순차 처리

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        argv = self._argv if self._argv is not None else _worker_argv()
        env = dict(os.environ)
        env.update({
            "YESON_MLX_WORKER": "1",
            "YESON_MLX_MODEL_PATH": str(mlx_model_dir(mlx_model_id())),
            "HF_HUB_OFFLINE": "1",  # 회의 중 네트워크 0 (스펙)
        })
        self._proc = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=env)
        try:
            line = await asyncio.wait_for(
                self._proc.stdout.readline(), timeout=self._ready_timeout)
            event = json.loads(line) if line else {}
        except (asyncio.TimeoutError, json.JSONDecodeError) as exc:
            await self.close()
            raise MlxWorkerUnavailable(f"worker not ready: {exc!r}") from exc
        if event.get("type") != "status" or event.get("state") != "ready":
            reason = event.get("reason", "no ready event")
            await self.close()
            raise MlxWorkerUnavailable(f"worker start failed: {reason}")

    async def translate(self, en: str, context: list[tuple[str, str]],
                        timeout: float) -> str:
        if not self.alive:
            raise MlxWorkerUnavailable("worker not running")
        async with self._lock:
            self._next_id += 1
            req_id = self._next_id
            req = {"id": req_id, "en": en,
                   "context": [[a, b] for a, b in context], "glossary": {}}
            assert self._proc is not None and self._proc.stdin and self._proc.stdout
            self._proc.stdin.write((json.dumps(req, ensure_ascii=False) + "\n").encode())
            await self._proc.stdin.drain()
            while True:
                line = await asyncio.wait_for(self._proc.stdout.readline(), timeout=timeout)
                if not line:  # EOF = 워커 사망
                    raise MlxWorkerUnavailable("worker died mid-request")
                try:
                    resp = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if resp.get("id") == req_id:
                    return str(resp.get("ko", ""))

    async def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        with contextlib.suppress(Exception):
            if proc.stdin:
                proc.stdin.close()
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest apps/server/tests/test_mlx_live_translate.py -v`
Expected: PASS (전체)

- [ ] **Step 5: 커밋**

```bash
git add apps/server/ai/mlx_live_translate.py apps/server/tests/test_mlx_live_translate.py
git commit -m "feat(mlx-live): 워커 클라이언트 — ready 타임아웃/요청 상관/사망 감지/kill 보증

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: 데코레이터 프로바이더 `MlxRefinedAppleProvider`

**Files:**
- Modify: `apps/server/ai/mlx_live_translate.py`
- Test: `apps/server/tests/test_mlx_live_translate.py` (추가)

**Interfaces:**
- Consumes: Task 1 `guard_mlx_ko`, Task 4 `MlxWorkerClient`, 기존 `STTProvider`/`TranslatedUtterance`(`apps.server.ai.providers`), 기존 `apply_ko_corrections`(`apps.server.ai.glossary`), 기존 `AppleLiveTranslateProvider`
- Produces (Task 6이 사용):
  - `class MlxRefinedAppleProvider:` — `STTProvider` 구현
    - `__init__(self, inner=None, client_factory=None, sentence_timeout=6.0, max_pending=3, max_respawns=2)` — inner=None이면 `AppleLiveTranslateProvider()`, client_factory=None이면 `MlxWorkerClient`
    - `def stream(self, audio, lang_hint) -> AsyncIterator[TranslatedUtterance]`

**동작 규칙 (스펙 고정):** 파셜 즉시 통과. 파이널은 홀드 큐에 넣고 순차 정제 — MLX 성공+가드 통과 시 `text_ko=apply_ko_corrections(mlx_ko)`로 교체, 그 외(가드 불합격/6s 타임아웃/워커 사망/미기동/홀드 3 초과 시 최고참부터/inner 스트림 종료 시 잔여)는 Apple KO 그대로. 파이널은 도착 순서대로 발행. 문맥은 직전 3개 **발행된** 파이널의 (en, 발행 ko). 워커는 stream() 시작 시 백그라운드 기동(기동 전 파이널은 Apple KO), 사망 시 재기동 최대 2회. inner 예외는 잔여 홀드를 Apple KO로 발행한 뒤 재전파.

- [ ] **Step 1: 실패하는 테스트 추가**

```python
# apps/server/tests/test_mlx_live_translate.py 에 추가
from datetime import datetime, timezone

from apps.server.ai.mlx_live_translate import MlxRefinedAppleProvider
from apps.server.ai.providers import TranslatedUtterance


def _utt(seq, en, ko, final, segment=1):
    now = datetime.now(timezone.utc)
    return TranslatedUtterance(seq=seq, text_en=en, text_ko=ko, started_at=now,
                               ended_at=now, is_final=final, provider_segment=segment)


class _FakeInner:
    """미리 정의된 utterance 시퀀스를 방출하는 STTProvider."""
    def __init__(self, utterances, error_after=None):
        self._utterances = utterances
        self._error_after = error_after

    async def stream(self, audio, lang_hint):
        for i, u in enumerate(self._utterances):
            if self._error_after is not None and i == self._error_after:
                raise RuntimeError("inner boom")
            yield u


class _FakeClient:
    """MlxWorkerClient 시늉: 응답 사전/지연/사망 시나리오 주입."""
    def __init__(self, responses=None, start_error=False, hang=False):
        self._responses = responses or {}
        self._start_error = start_error
        self._hang = hang
        self.requests: list[tuple[str, list]] = []
        self.closed = False
        self.alive = False

    async def start(self):
        if self._start_error:
            raise MlxWorkerUnavailable("no model")
        self.alive = True

    async def translate(self, en, context, timeout):
        self.requests.append((en, list(context)))
        if self._hang:
            await asyncio.sleep(timeout + 1)  # wait_for가 아니라 호출자가 timeout 처리
            raise asyncio.TimeoutError()
        if en in self._responses:
            resp = self._responses[en]
            if isinstance(resp, Exception):
                self.alive = False
                raise resp
            return resp
        return f"MLX:{en}"

    async def close(self):
        self.closed = True
        self.alive = False


async def _collect(provider):
    async def _no_audio():
        return
        yield  # pragma: no cover
    return [u async for u in provider.stream(_no_audio(), "en")]


class TestMlxRefinedAppleProvider:
    def test_partial_passthrough_final_refined(self):
        inner = _FakeInner([
            _utt(1, "Hello", "안녕(파셜)", final=False),
            _utt(1, "Hello there.", "안녕하세요(애플)", final=True),
        ])
        client = _FakeClient(responses={"Hello there.": "안녕하십니까(MLX)"})
        provider = MlxRefinedAppleProvider(inner=inner, client_factory=lambda: client)

        out = asyncio.run(_collect(provider))
        assert out[0].text_ko == "안녕(파셜)" and not out[0].is_final
        finals = [u for u in out if u.is_final]
        assert finals[0].text_ko == "안녕하십니까(MLX)"
        assert finals[0].seq == 1
        assert client.closed  # 스트림 종료 시 워커 정리

    def test_guard_reject_falls_back_to_apple(self):
        inner = _FakeInner([_utt(1, "Open codex.", "코덱스를 여세요(애플)", final=True)])
        client = _FakeClient(responses={"Open codex.": "코다克斯를 여세요"})  # 한자 혼입
        provider = MlxRefinedAppleProvider(inner=inner, client_factory=lambda: client)
        out = asyncio.run(_collect(provider))
        assert out[0].text_ko == "코덱스를 여세요(애플)"

    def test_worker_start_failure_means_apple_only(self):
        inner = _FakeInner([_utt(1, "Hello there.", "안녕하세요(애플)", final=True)])
        client = _FakeClient(start_error=True)
        provider = MlxRefinedAppleProvider(inner=inner, client_factory=lambda: client)
        out = asyncio.run(_collect(provider))
        assert out[0].text_ko == "안녕하세요(애플)"  # 예외 없이 폴백

    def test_worker_death_falls_back_and_respawns(self):
        inner = _FakeInner([
            _utt(1, "One.", "하나(애플)", final=True),
            _utt(2, "Two.", "둘(애플)", final=True),
        ])
        dead_client = _FakeClient(responses={"One.": MlxWorkerUnavailable("died")})
        fresh_client = _FakeClient(responses={"Two.": "둘(MLX)"})
        clients = [dead_client, fresh_client]
        provider = MlxRefinedAppleProvider(inner=inner, client_factory=lambda: clients.pop(0))
        out = asyncio.run(_collect(provider))
        finals = {u.seq: u.text_ko for u in out if u.is_final}
        assert finals[1] == "하나(애플)"   # 사망 → 폴백
        assert finals[2] == "둘(MLX)"     # 재스폰 후 정상 정제

    def test_context_uses_emitted_finals(self):
        inner = _FakeInner([
            _utt(1, "One.", "하나(애플)", final=True),
            _utt(2, "Two.", "둘(애플)", final=True),
        ])
        client = _FakeClient(responses={"One.": "하나(MLX)", "Two.": "둘(MLX)"})
        provider = MlxRefinedAppleProvider(inner=inner, client_factory=lambda: client)
        asyncio.run(_collect(provider))
        # 두 번째 요청의 문맥에 첫 번째의 (en, 발행 ko)가 들어간다
        assert client.requests[1][1] == [("One.", "하나(MLX)")]

    def test_inner_error_flushes_holds_then_reraises(self):
        inner = _FakeInner(
            [_utt(1, "One.", "하나(애플)", final=True)], error_after=1)
        client = _FakeClient(hang=True)  # 정제가 끝나기 전에 inner가 죽는 상황
        provider = MlxRefinedAppleProvider(
            inner=inner, client_factory=lambda: client, sentence_timeout=0.2)

        async def run():
            got = []
            with pytest.raises(RuntimeError, match="inner boom"):
                async def _no_audio():
                    return
                    yield  # pragma: no cover
                async for u in provider.stream(_no_audio(), "en"):
                    got.append(u)
            return got

        got = asyncio.run(run())
        finals = [u for u in got if u.is_final]
        assert finals and finals[0].text_ko == "하나(애플)"  # 홀드 플러시 후 재전파
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest apps/server/tests/test_mlx_live_translate.py::TestMlxRefinedAppleProvider -v`
Expected: FAIL — `ImportError: cannot import name 'MlxRefinedAppleProvider'`

- [ ] **Step 3: 구현** (mlx_live_translate.py에 추가)

```python
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import replace

from apps.server.ai.glossary import apply_ko_corrections
from apps.server.ai.providers import STTProvider, TranslatedUtterance

DEFAULT_SENTENCE_TIMEOUT_SECONDS = 6.0
DEFAULT_MAX_PENDING = 3
DEFAULT_MAX_RESPAWNS = 2
_CONTEXT_WINDOW = 3
_SENTINEL = object()  # inner 스트림 종료 표식


class MlxRefinedAppleProvider:
    """Apple 라이브 스트림의 파이널 KO만 MLX로 정제하는 데코레이터 (스펙 §데이터 흐름)."""

    def __init__(
        self,
        inner: STTProvider | None = None,
        client_factory: Callable[[], MlxWorkerClient] | None = None,
        sentence_timeout: float = DEFAULT_SENTENCE_TIMEOUT_SECONDS,
        max_pending: int = DEFAULT_MAX_PENDING,
        max_respawns: int = DEFAULT_MAX_RESPAWNS,
    ) -> None:
        if inner is None:
            from apps.server.ai.apple_live_translate import AppleLiveTranslateProvider

            inner = AppleLiveTranslateProvider()
        self._inner = inner
        self._client_factory = client_factory or MlxWorkerClient
        self._sentence_timeout = sentence_timeout
        self._max_pending = max_pending
        self._max_respawns = max_respawns

    async def stream(
        self, audio: AsyncIterator[bytes], lang_hint: str
    ) -> AsyncIterator[TranslatedUtterance]:
        out_q: asyncio.Queue = asyncio.Queue()
        holds: deque[TranslatedUtterance] = deque()
        context: deque[tuple[str, str]] = deque(maxlen=_CONTEXT_WINDOW)
        client: MlxWorkerClient | None = None
        client_ready = False
        respawns = 0
        inner_error: BaseException | None = None

        async def _ensure_client() -> MlxWorkerClient | None:
            """기동 시도. 실패는 None — 파이널은 Apple KO로 흐른다."""
            nonlocal client, client_ready, respawns
            if client_ready and client is not None and client.alive:
                return client
            if respawns > self._max_respawns:
                return None
            try:
                fresh = self._client_factory()
                await fresh.start()
            except MlxWorkerUnavailable as exc:
                respawns += 1
                logger.warning("mlx worker unavailable (attempt %d): %s", respawns, exc)
                return None
            client, client_ready = fresh, True
            return client

        async def _refine(utterance: TranslatedUtterance) -> TranslatedUtterance:
            """파이널 1건 정제. 어떤 실패든 Apple KO 그대로 반환 (예외 금지)."""
            nonlocal client_ready, respawns
            active = await _ensure_client()
            if active is None:
                return utterance
            try:
                mlx_ko = await active.translate(
                    utterance.text_en, list(context), timeout=self._sentence_timeout)
            except asyncio.TimeoutError:
                logger.warning("mlx sentence timeout seq=%d", utterance.seq)
                return utterance
            except MlxWorkerUnavailable as exc:
                client_ready = False
                respawns += 1
                logger.warning("mlx worker died (respawn %d/%d): %s",
                               respawns, self._max_respawns, exc)
                return utterance
            reason = guard_mlx_ko(utterance.text_en, mlx_ko)
            if reason is not None:
                logger.info("mlx_guard_reject reason=%s seq=%d", reason, utterance.seq)
                return utterance
            return replace(utterance, text_ko=apply_ko_corrections(mlx_ko))

        async def _pump_inner() -> None:
            """inner 스트림 소비: 파셜 즉시 발행, 파이널은 홀드 큐 → 순차 정제."""
            nonlocal inner_error
            try:
                async for utterance in self._inner.stream(audio, lang_hint):
                    if not utterance.is_final:
                        await out_q.put(utterance)
                        continue
                    holds.append(utterance)
                    # 백로그: 홀드가 상한 초과면 최고참부터 MLX 생략(Apple KO 즉시)
                    while len(holds) > self._max_pending:
                        stale = holds.popleft()
                        logger.info("mlx_backlog_skip seq=%d", stale.seq)
                        context.append((stale.text_en, stale.text_ko))
                        await out_q.put(stale)
                    # 순차 정제 (워커도 순차 — 홀드 최고참부터)
                    while holds:
                        pending = holds.popleft()
                        refined = await _refine(pending)
                        context.append((refined.text_en, refined.text_ko))
                        await out_q.put(refined)
            except BaseException as exc:  # noqa: BLE001 — 잔여 홀드 플러시 후 재전파
                inner_error = exc
            finally:
                while holds:  # inner 종료/예외: 잔여 홀드는 Apple KO로 플러시
                    await out_q.put(holds.popleft())
                await out_q.put(_SENTINEL)

        pump = asyncio.create_task(_pump_inner())
        # 워커는 백그라운드 선기동 — 로드가 끝나기 전 파이널은 _ensure_client의
        # respawn 카운트를 소모하지 않도록 start를 미리 시도해 둔다.
        warmup = asyncio.create_task(_ensure_client())
        try:
            while True:
                item = await out_q.get()
                if item is _SENTINEL:
                    break
                yield item
            if inner_error is not None:
                raise inner_error
        finally:
            warmup.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await warmup
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pump
            if client is not None:
                await client.close()
```

구현 주의(설계 의도):
- `_pump_inner`가 정제까지 인라인으로 수행하므로 **정제 중에는 inner에서 새 utterance를 못 읽는다** → 그 사이 파셜이 밀릴 수 있다. Apple 파셜은 0.5s 스로틀이고 정제는 p50 ~2.1s라 실사용 체감은 "파셜 갱신이 정제 중 일시 정지". 이것이 수용 불가로 판정되면(실회의 평가) 정제를 별도 태스크로 분리하는 후속을 연다 — 단 이번 구현에서는 스펙의 단순성(순차·순서 보장)을 우선한다.
- 백로그 상한이 사실상 "정제 1건이 6s 타임아웃까지 걸리는 동안 쌓인 파이널"에만 작동하는 구조인 것도 같은 단순화의 결과다.

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest apps/server/tests/test_mlx_live_translate.py -v`
Expected: PASS (전체). `test_inner_error_flushes_holds_then_reraises`는 hang 클라이언트(0.2s 타임아웃) 때문에 ~0.5s 소요 — 1s 넘게 걸리면 타임아웃 전달 경로를 의심할 것.

- [ ] **Step 5: 커밋**

```bash
git add apps/server/ai/mlx_live_translate.py apps/server/tests/test_mlx_live_translate.py
git commit -m "feat(mlx-live): 데코레이터 프로바이더 — 파이널 홀드 정제/가드 폴백/백로그/재스폰

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: 프로바이더 등록 (`create_ai_provider`)

**Files:**
- Modify: `apps/server/ws/sidecar.py` (`create_ai_provider`, 현재 ~121행 — apple 분기 아래)
- Test: `apps/server/tests/test_mlx_live_translate.py` (추가)

**Interfaces:**
- Consumes: Task 2 `mlx_live_available`, Task 5 `MlxRefinedAppleProvider`
- Produces: env `YESON_AI_PROVIDER=apple_mlx_live_translate`(또는 `apple_mlx`)로 세션이 하이브리드 B를 사용

- [ ] **Step 1: 실패하는 테스트 추가**

```python
# apps/server/tests/test_mlx_live_translate.py 에 추가
from apps.server.ws.sidecar import create_ai_provider


class TestProviderRegistration:
    def test_registered_when_available(self, monkeypatch):
        import apps.server.ws.sidecar as sidecar_mod
        monkeypatch.setenv("YESON_AI_PROVIDER", "apple_mlx_live_translate")
        monkeypatch.setattr(
            "apps.server.ai.mlx_live_translate.mlx_live_available", lambda: True)
        provider = sidecar_mod.create_ai_provider()
        assert type(provider).__name__ == "MlxRefinedAppleProvider"

    def test_alias_apple_mlx(self, monkeypatch):
        monkeypatch.setenv("YESON_AI_PROVIDER", "apple_mlx")
        monkeypatch.setattr(
            "apps.server.ai.mlx_live_translate.mlx_live_available", lambda: True)
        assert create_ai_provider() is not None

    def test_unavailable_returns_none(self, monkeypatch):
        # 게이팅 미충족 → None (S2 count-only) — apple provider와 동일 관례
        monkeypatch.setenv("YESON_AI_PROVIDER", "apple_mlx_live_translate")
        monkeypatch.setattr(
            "apps.server.ai.mlx_live_translate.mlx_live_available", lambda: False)
        assert create_ai_provider() is None
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest apps/server/tests/test_mlx_live_translate.py::TestProviderRegistration -v`
Expected: FAIL — 미지의 provider 명이라 gemini 폴백 경로로 빠져 None 또는 다른 타입 반환

- [ ] **Step 3: 구현** — `apps/server/ws/sidecar.py`의 `create_ai_provider()`에서 기존 apple 분기(`if provider_name in {"apple_live_translate", "apple"}:` 블록) **바로 아래**에 삽입:

```python
    if provider_name in {"apple_mlx_live_translate", "apple_mlx"}:
        from apps.server.ai import mlx_live_translate

        if not mlx_live_translate.mlx_live_available():
            return None  # 게이팅/모델 미설치 — S2 count-only 모드 유지
        return mlx_live_translate.MlxRefinedAppleProvider()
```

(모듈 참조 형태의 import는 테스트 monkeypatch가 `mlx_live_available`를 대체할 수 있게 하기 위함이다 — `from x import y` 형태로 바꾸지 말 것.)

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest apps/server/tests/test_mlx_live_translate.py apps/server/tests/test_apple_live_translate.py -v`
Expected: PASS (기존 apple 테스트 포함 전체 — 회귀 없음 확인)

- [ ] **Step 5: 커밋**

```bash
git add apps/server/ws/sidecar.py apps/server/tests/test_mlx_live_translate.py
git commit -m "feat(mlx-live): YESON_AI_PROVIDER=apple_mlx_live_translate 등록

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: 의존성 + 빌드 스크립트 (arm 전용 mlx)

**Files:**
- Modify: `apps/server/pyproject.toml`
- Modify: `apps/server_desktop/scripts/build-server.sh`

**Interfaces:**
- Produces: dev 환경 `uv pip install -e './apps/server[mlx]'`로 mlx-lm 사용 가능; arm 맥 번들에 mlx 포함, 인텔/윈도우 번들 무변경

- [ ] **Step 1: pyproject에 옵션 extra 추가** — `[dependency-groups]` 위에 삽입:

```toml
[project.optional-dependencies]
# 하이브리드 B(MLX 로컬 번역) — 실리콘맥 전용. 서버 본체 import와 무관하며
# 워커 진입점(run_worker/run_download)에서만 지연 import된다.
mlx = ["mlx-lm>=0.31; sys_platform == 'darwin' and platform_machine == 'arm64'"]
```

- [ ] **Step 2: build-server.sh에 arm 분기 추가** — `uv pip install --python ... ./apps/server "pyinstaller>=6.21"` 줄 **바로 아래**에:

```bash
# 하이브리드 B: 실리콘맥 번들에만 mlx-lm 포함 (인텔맥 회귀 방지 — 510741b 방침).
MLX_COLLECT_FLAGS=()
if [[ "$(uname -sm)" == "Darwin arm64" ]]; then
    echo "Adding mlx-lm (Apple Silicon only)…"
    VIRTUAL_ENV="${BUILD_VENV}" uv pip install --python "${BUILD_VENV}/bin/python" \
        './apps/server[mlx]'
    MLX_COLLECT_FLAGS=(--collect-all mlx --collect-all mlx_lm)
fi
```

그리고 pyinstaller 호출의 collect 플래그 목록(예: `--collect-all docx \` 근처)에 한 줄 추가:

```bash
    "${MLX_COLLECT_FLAGS[@]}" \
```

주의: bash의 빈 배열 확장은 `set -u`에서 안전하도록 `"${MLX_COLLECT_FLAGS[@]:-}"`가 아니라 위처럼 `()` 초기화를 반드시 유지한 채 `"${MLX_COLLECT_FLAGS[@]}"`를 쓴다 (bash 4.4+에서 빈 배열 확장은 no-op).

- [ ] **Step 3: 검증 (arm 맥에서)**

Run: `uv pip install -e './apps/server[mlx]' && uv run python -c "import mlx_lm; print('mlx-lm OK')"`
Expected: `mlx-lm OK`

Run(문법 검증): `bash -n apps/server_desktop/scripts/build-server.sh`
Expected: 출력 없음(성공). 전체 번들 빌드는 Task 11 스모크에서 수행.

- [ ] **Step 4: 커밋**

```bash
git add apps/server/pyproject.toml apps/server_desktop/scripts/build-server.sh
git commit -m "build(mlx-live): mlx-lm extra(arm 전용) + 번들 collect 분기

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: 서버 콘솔 Rust — 설정 필드 + env 주입 + 다운로드 커맨드

**Files:**
- Modify: `apps/server_desktop/src-tauri/src/server_config.rs`
- Modify: `apps/server_desktop/src-tauri/src/server_process.rs`
- Modify: `apps/server_desktop/src-tauri/src/lib.rs` (커맨드 핸들러 등록)

**Interfaces:**
- Consumes: Task 3의 다운로드 원샷(`YESON_MLX_DOWNLOAD=<model_id>` env, 진행 JSONL stdout)
- Produces (Task 9의 TS가 invoke):
  - `ServerConfig`/`ServerConfigInput`/meta에 `yeson_mlx_model: String` (빈 값 = 기본 모델)
  - 서버 spawn env에 `YESON_MLX_MODEL` 주입
  - `#[tauri::command] mlx_model_status(model_id: String) -> Result<bool, String>` — `<app_data>/storage/mlx_models/<id '/'→'--'>/config.json` 존재 여부
  - `#[tauri::command] mlx_download_model(app: AppHandle, model_id: String) -> Result<String, String>` — 서버 바이너리를 `YESON_MLX_DOWNLOAD` env로 실행, stdout JSONL 줄을 `mlx-download-progress` 이벤트로 emit, 완료/실패 메시지 반환

- [ ] **Step 1: server_config.rs 필드 추가** — 기존 `yeson_ai_provider` 필드와 같은 방식으로:
  - `ServerConfig` 구조체에 `#[serde(default)] pub yeson_mlx_model: String,`
  - `ServerConfigInput`에 동일 필드, `apply()`(~152행 스타일)에 `self.yeson_mlx_model = input.yeson_mlx_model.trim().to_string();`
  - meta 프로젝션에 `mlx_model: String` (비밀 아님 — 값 그대로 노출)
  - 기존 config round-trip 테스트(~280행 `assert_eq!(meta.provider, ...)` 부근)에 `yeson_mlx_model` 왕복 assert 1개 추가

- [ ] **Step 2: server_process.rs env 주입** — 서버 spawn 빌더의 `.env("YESON_AI_PROVIDER", &provider)`(~290행) 바로 아래:

```rust
        .env("YESON_MLX_MODEL", &config.yeson_mlx_model)
```

(빈 문자열이면 Python 쪽 `mlx_model_id()`가 기본 모델로 해석한다 — Rust에서 기본값을 중복 정의하지 않는다.)

- [ ] **Step 3: 다운로드/상태 커맨드 추가** — server_process.rs 끝부분, `install_fast_translation`(~1202행) 아래에 같은 스타일로:

```rust
fn mlx_model_dir(app_data: &std::path::Path, model_id: &str) -> std::path::PathBuf {
    app_data
        .join("storage")
        .join("mlx_models")
        .join(model_id.replace('/', "--"))
}

#[tauri::command]
pub fn mlx_model_status(app: tauri::AppHandle, model_id: String) -> Result<bool, String> {
    let app_data = app
        .path()
        .app_data_dir()
        .map_err(|e| format!("app_data_dir 실패: {e}"))?;
    Ok(mlx_model_dir(&app_data, &model_id).join("config.json").is_file())
}

#[tauri::command]
pub async fn mlx_download_model(
    app: tauri::AppHandle,
    model_id: String,
) -> Result<String, String> {
    use std::io::{BufRead, BufReader};
    use tauri::Emitter;

    let bin = locate_server_binary() // 기존 spawn 경로가 쓰는 바이너리 로케이터 재사용
        .ok_or_else(|| "yeson-server 바이너리를 찾을 수 없습니다".to_string())?;
    let app_data = app
        .path()
        .app_data_dir()
        .map_err(|e| format!("app_data_dir 실패: {e}"))?;
    let storage_root = app_data.join("storage");

    let mut child = std::process::Command::new(&bin)
        .env("YESON_MLX_DOWNLOAD", &model_id)
        .env("STORAGE_ROOT", &storage_root)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map_err(|e| format!("다운로드 프로세스 실행 실패: {e}"))?;

    let stdout = child.stdout.take().ok_or("stdout 캡처 실패")?;
    for line in BufReader::new(stdout).lines().map_while(Result::ok) {
        let _ = app.emit("mlx-download-progress", &line); // JSONL 줄 그대로 전달
    }
    let status = child.wait().map_err(|e| format!("다운로드 대기 실패: {e}"))?;
    if status.success() {
        Ok(format!("{model_id} 다운로드 완료"))
    } else {
        Err(format!("{model_id} 다운로드 실패 — 콘솔 로그 확인"))
    }
}
```

주의: `locate_server_binary()`는 이 파일에 이미 있는 서버 바이너리 탐색 함수를 재사용한다(실제 함수명을 spawn 경로에서 확인해 맞출 것 — `starting yeson-server:` 로그를 만드는 지점이 쓰는 그 로케이터). `mlx_download_model`은 async command라 blocking read가 Tauri 런타임을 막지 않도록 `#[tauri::command(async)]` 시맨틱(별도 스레드 실행)이 적용된다.

- [ ] **Step 4: lib.rs 핸들러 등록** — 기존 `invoke_handler(tauri::generate_handler![...])` 목록에 `mlx_model_status, mlx_download_model` 추가.

- [ ] **Step 5: 검증**

Run: `cargo test --manifest-path apps/server_desktop/src-tauri/Cargo.toml server_config`
Expected: PASS (round-trip에 yeson_mlx_model 포함)

Run: `cargo check --manifest-path apps/server_desktop/src-tauri/Cargo.toml`
Expected: 에러 0

- [ ] **Step 6: 커밋**

```bash
git add apps/server_desktop/src-tauri/src/server_config.rs apps/server_desktop/src-tauri/src/server_process.rs apps/server_desktop/src-tauri/src/lib.rs
git commit -m "feat(server-console): yeson_mlx_model 설정 + MLX 모델 상태/다운로드 커맨드

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: 서버 콘솔 UI — provider 옵션 + 모델 관리 패널

**Files:**
- Modify: `apps/server_desktop/src/setup/serverConfig.ts`
- Create: `apps/server_desktop/src/setup/MlxModelPanel.tsx`
- Modify: `apps/server_desktop/src/setup/ServerConfigPanel.tsx`

**Interfaces:**
- Consumes: Task 8의 `mlx_model_status`/`mlx_download_model` 커맨드 + `mlx-download-progress` 이벤트
- Produces: 운영자가 콘솔에서 (1) 엔진 "Apple 전사 + 로컬 LLM 번역 (실험적)" 선택, (2) 모델 다운로드/상태 확인, (3) 기본 모델(9B/4B) 선택

- [ ] **Step 1: serverConfig.ts 확장**

```typescript
// ServerConfigInput에 추가:
  yesonMlxModel: string;
// ServerConfigMeta에 추가:
  mlxModel: string;
// EMPTY_META에 추가:
  mlxModel: "",

export const MLX_MODELS = [
  { id: "mlx-community/Qwen3.5-9B-4bit", label: "Qwen3.5 9B (기본 — 품질 우선, RAM ~5GB)" },
  { id: "mlx-community/Qwen3.5-4B-4bit", label: "Qwen3.5 4B (저사양 — 지연 절반, RAM ~2.3GB)" },
] as const;

export async function mlxModelStatus(modelId: string): Promise<boolean> {
  if (!hasTauriRuntime()) return false;
  return invoke<boolean>("mlx_model_status", { modelId });
}

export async function downloadMlxModel(modelId: string): Promise<string> {
  return invoke<string>("mlx_download_model", { modelId });
}
```

- [ ] **Step 2: MlxModelPanel.tsx 작성**

```tsx
// apps/server_desktop/src/setup/MlxModelPanel.tsx
// MLX 로컬 번역 모델 관리 — apple_mlx_live_translate provider 전용 (실리콘맥).
import { listen } from "@tauri-apps/api/event";
import { useEffect, useState } from "react";
import { MLX_MODELS, downloadMlxModel, mlxModelStatus } from "./serverConfig";

export function MlxModelPanel(props: {
  selectedModel: string;
  onSelectModel: (id: string) => void;
}) {
  const [installed, setInstalled] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");

  const refresh = async () => {
    const entries = await Promise.all(
      MLX_MODELS.map(async (m) => [m.id, await mlxModelStatus(m.id)] as const),
    );
    setInstalled(Object.fromEntries(entries));
  };
  useEffect(() => {
    void refresh();
    const unlisten = listen<string>("mlx-download-progress", (e) => setProgress(e.payload));
    return () => {
      void unlisten.then((fn) => fn());
    };
  }, []);

  const download = async (id: string) => {
    setBusy(id);
    setError("");
    try {
      await downloadMlxModel(id);
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
      setProgress("");
    }
  };

  return (
    <section>
      <h3>로컬 번역 모델 (MLX — 실리콘맥 전용)</h3>
      {MLX_MODELS.map((m) => (
        <div key={m.id}>
          <label>
            <input
              type="radio"
              name="mlx-model"
              checked={(props.selectedModel || MLX_MODELS[0].id) === m.id}
              onChange={() => props.onSelectModel(m.id)}
            />
            {m.label} {installed[m.id] ? "✅ 설치됨" : "⬇︎ 미설치"}
          </label>
          {!installed[m.id] && (
            <button type="button" disabled={busy !== null} onClick={() => void download(m.id)}>
              {busy === m.id ? "다운로드 중…" : "다운로드"}
            </button>
          )}
        </div>
      ))}
      {busy && progress && <pre>{progress}</pre>}
      {error && <p role="alert">{error}</p>}
      <p>모델 변경·설치 후에는 서버를 재시작해야 적용됩니다.</p>
    </section>
  );
}
```

(스타일/마크업은 ServerConfigPanel.tsx의 기존 섹션 관례에 맞춰 조정하라 — 클래스명·컴포넌트 프리미티브를 그대로 따를 것.)

- [ ] **Step 3: ServerConfigPanel.tsx 연결**
  - provider 선택 UI(기존 `yesonAiProvider` 입력/셀렉트 위치)에 옵션 추가: value `apple_mlx_live_translate`, label `Apple 전사 + 로컬 LLM 번역 (실험적)`
  - config state에 `yesonMlxModel` 배선(로드 시 `meta.mlxModel`, 저장 시 input에 포함)
  - provider가 `apple_mlx_live_translate`일 때 `<MlxModelPanel selectedModel={...} onSelectModel={...} />` 렌더 (기존 `installFastTranslation` 버튼 렌더 조건과 같은 자리 관례)

- [ ] **Step 4: 검증**

Run: `pnpm -C apps/server_desktop typecheck` (스크립트가 없으면 `pnpm -C apps/server_desktop exec tsc --noEmit`)
Expected: 에러 0

Run(수동): `pnpm -C apps/server_desktop tauri dev` → 설정 화면에서 provider 선택 → 모델 패널 표시·상태 배지 확인 → 4B 다운로드 버튼 → 진행 JSONL 표시 → `✅ 설치됨` 전환

- [ ] **Step 5: 커밋**

```bash
git add apps/server_desktop/src/setup/serverConfig.ts apps/server_desktop/src/setup/MlxModelPanel.tsx apps/server_desktop/src/setup/ServerConfigPanel.tsx
git commit -m "feat(server-console): MLX 엔진 선택 + 모델 다운로드/기본모델 UI

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: 실모델 스모크 (수동, arm 맥)

**Files:** 없음 (검증 전용)

- [ ] **Step 1: 모델 준비** — 콘솔 버튼 또는 CLI로:

```bash
STORAGE_ROOT="$HOME/Library/Application Support/com.yeson.server-console/storage" \
YESON_MLX_DOWNLOAD=mlx-community/Qwen3.5-9B-4bit \
uv run python -m apps.server_desktop.sidecar.server_entry
```

Expected: `{"type": "download", "state": "start", ...}` → `{"type": "download", "state": "done", ...}`, 종료코드 0

- [ ] **Step 2: 실모델 워커 왕복**

```bash
STORAGE_ROOT="$HOME/Library/Application Support/com.yeson.server-console/storage" \
YESON_MLX_WORKER=1 \
YESON_MLX_MODEL_PATH="$HOME/Library/Application Support/com.yeson.server-console/storage/mlx_models/mlx-community--Qwen3.5-9B-4bit" \
uv run python -m apps.server_desktop.sidecar.server_entry \
  <<< '{"id":1,"en":"Let us get started with the demo.","context":[],"glossary":{}}'
```

Expected: ready까지 120s 이내(웜 캐시 ~5s), 응답 `ko`가 자연스러운 합니다체 한국어, `gen_ms` < 4000

- [ ] **Step 3: 엔드투엔드** — 서버 콘솔에서 provider를 `apple_mlx_live_translate`로 변경 → 서버 재시작 → 데스크톱 앱으로 짧은 세션 → 서버 로그에서 확인:
  - `AI live session starting` 이후 Gemini connect 로그가 **없고**
  - 파이널 자막이 발행되며 `mlx_guard_reject`/`mlx_backlog_skip`이 있다면 사유와 빈도가 합리적인지

- [ ] **Step 4: 결과 기록** — 스파이크 노트(`2026-07-12-mlx-live-translate-spike.md`)에 실기기 ready 시간·문장 지연 실측을 한 줄 추가하고 커밋:

```bash
git add docs/superpowers/specs/2026-07-12-mlx-live-translate-spike.md
git commit -m "docs(mlx-live): 실기기 스모크 실측 기록

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: 번들 빌드 스모크 (arm 맥)

**Files:** 없음 (검증 전용; 문제 발견 시 Task 7 산출물 수정)

- [ ] **Step 1: 번들 빌드**

Run: `bash apps/server_desktop/scripts/build-server.sh`
Expected: 성공, "Adding mlx-lm (Apple Silicon only)…" 로그 확인

- [ ] **Step 2: 번들 워커 페이크 스모크** — PyInstaller 산출물이 mlx import 없이(페이크) 그리고 mlx 포함(실모델 경로 미설정 시 missing_mlx_model 에러) 모두 올바르게 동작하는지:

```bash
BIN=$(ls target/server-dist/yeson-server/yeson-server)
echo '{"id":1,"en":"Hi.","context":[],"glossary":{}}' | YESON_MLX_FAKE=1 YESON_MLX_WORKER=1 "$BIN"
# Expected: ready + {"id":1,"ko":"[fake] Hi.",...} + exit 0
YESON_MLX_WORKER=1 YESON_MLX_MODEL_PATH=/nonexistent "$BIN" < /dev/null
# Expected: {"type":"status","state":"error","reason":"missing_mlx_model: ..."} + exit 1
```

- [ ] **Step 3: 기존 번들 스모크 회귀**

Run: `bash apps/server_desktop/scripts/smoke-server-bundle.sh`
Expected: 기존과 동일하게 PASS (mlx 추가로 인한 부작용 없음)

- [ ] **Step 4: 커밋** (수정이 있었던 경우에만)

---

## Self-Review 기록

- 스펙 커버리지: 파셜 통과(T5) / 홀드 확정(T5) / 가드 5규칙(T1) / Apple KO 폴백·재스폰≤2·타임아웃 6s·백로그 3(T5) / 게이팅·missing_mlx_model(T2·T3) / HF_HUB_OFFLINE(T4) / 콘솔 버튼·진행률·기본모델(T8·T9) / arm 전용 패키징(T7·T11) / 실모델 스모크(T10) — 전 항목 태스크 존재.
- 스펙과의 의도적 편차 2건: (1) 워커 실행이 argv 플래그가 아니라 env 플래그(`YESON_MLX_WORKER=1`) — server_entry의 기존 원샷 모드 관례를 따름. (2) "영구 에러 운영자 알림" — 게이팅 미충족은 provider=None(S2 count-only, 기존 apple 관례)으로 처리하고, 세션 중 워커 실패는 운영자 알림 없이 Apple KO 폴백+로그로 처리한다. 알림은 자막이 실제로 중단되는 경우가 아니므로 YAGNI; 실회의 평가에서 필요하면 후속.
- 타입 일관성: `guard_mlx_ko`(T1→T5), `mlx_model_dir/mlx_model_id`(T2→T4·T8), 워커 프로토콜(T3→T4), `MlxWorkerClient.translate(en, context, timeout)`(T4→T5), `MlxRefinedAppleProvider()`(T5→T6) — 시그니처 상호 참조 확인 완료.
