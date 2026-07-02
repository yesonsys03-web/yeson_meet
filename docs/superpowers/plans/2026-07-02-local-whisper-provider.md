# local_whisper 완전 무료 로컬 자막 provider — 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 클라우드 호출 0원의 EN→KO 자막 provider(`local_whisper`)를 추가하고 서버 콘솔 드롭다운에서 선택 가능하게 한다.

**Architecture:** faster-whisper base int8이 16kHz PCM을 ~1초 주기로 재전사하고, LocalAgreement-2가 두 연속 가설의 공통 접두만 확정한다. 확정 EN을 문장 단위로 조립해 CTranslate2 Opus-MT(en→ko int8)로 번역, `apply_ko_corrections()` 후처리를 거쳐 기존 `TranslatedUtterance` partial/final 규약으로 방출한다. 모델은 최초 사용 시 `{STORAGE_ROOT}/models/`에 다운로드·캐시된다.

**Tech Stack:** faster-whisper(≥1.1), ctranslate2(faster-whisper 의존으로 포함), sentencepiece(≥0.2), numpy. 스펙: `docs/superpowers/specs/2026-07-02-local-whisper-provider-design.md`

## Global Constraints

- provider 이름: `local_whisper` (별칭 `local`, `whisper_local`)
- env: `YESON_LOCAL_MT_MODEL_DIR`, `YESON_LOCAL_MT_MODEL_URL`, `YESON_LOCAL_MT_MODEL_SHA256`, `YESON_LOCAL_MT_TARGET_PREFIX`, `YESON_LOCAL_WHISPER_MODEL`(기본 `base`), `YESON_LOCAL_WHISPER_THREADS`(기본 `4`)
- 모델 캐시 루트: `{STORAGE_ROOT}/models/` (`STORAGE_ROOT` env, 기본 `/var/lib/yeson-meet/storage` — `glossary.py:26-27`과 동일 규약)
- wire 프로토콜 무변경: `TranslatedUtterance`(`apps/server/ai/providers.py:11-24`) 그대로. 세그먼트당 seq=1 시작, `stream()` 재호출마다 `provider_segment` 증가(AISequenceNormalizer 규약)
- 신규 Python 파일은 기존 패턴대로 파일 전체를 `# === ANCHOR: <NAME>_START ===` / `_END ===`로 감싼다
- 작업 브랜치: `local_whisper` (main에서 분기)
- 테스트 실행: 저장소 루트에서 `.venv/bin/python -m pytest apps/server/tests/<file> -q`
- 커밋 메시지 끝에 `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- final의 KO는 불변. partial의 KO는 스로틀(1회/초) 재번역이라 다시 써질 수 있음(허용)

---

### Task 1: 브랜치 + 의존성 + 모델 경로/다운로드 모듈 `local_models.py`

**Files:**
- Modify: `apps/server/pyproject.toml:6-25` (deps 추가)
- Create: `apps/server/ai/local_models.py`
- Test: `apps/server/tests/test_local_models.py`

**Interfaces:**
- Produces: `models_root() -> Path`, `whisper_download_root() -> Path`, `mt_model_dir() -> Path`, `ensure_mt_model() -> Path`(모델 보장, 실패 시 `RuntimeError`), `MT_REQUIRED_FILES: tuple[str, ...]`

- [ ] **Step 1: 브랜치 생성**

```bash
git checkout -b local_whisper
```

- [ ] **Step 2: pyproject deps 추가**

`apps/server/pyproject.toml`의 `dependencies` 리스트 끝(`"python-docx>=1.1",` 다음)에 추가:

```toml
  "faster-whisper>=1.1",
  "sentencepiece>=0.2",
  "numpy>=1.26",
```

- [ ] **Step 3: 의존성 설치**

```bash
uv sync
```
실패 시(워크스페이스 구성이 아니면): `uv pip install --python .venv/bin/python faster-whisper sentencepiece numpy`
확인: `.venv/bin/python -c "import faster_whisper, sentencepiece, ctranslate2; print('ok')"` → `ok`

- [ ] **Step 4: 실패 테스트 작성** — `apps/server/tests/test_local_models.py`

```python
"""Tests for local model path resolution + MT model download/ensure."""
import hashlib
import io
import tarfile
from pathlib import Path

import pytest

from apps.server.ai import local_models


def _make_mt_tarball(tmp_path: Path) -> tuple[Path, str]:
    """Build a minimal fake MT model tarball (top-level files) + its sha256."""
    src = tmp_path / "src_model"
    src.mkdir()
    for name in local_models.MT_REQUIRED_FILES:
        (src / name).write_bytes(b"fake-" + name.encode())
    tar_path = tmp_path / "mt.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        for name in local_models.MT_REQUIRED_FILES:
            tar.add(src / name, arcname=name)
    digest = hashlib.sha256(tar_path.read_bytes()).hexdigest()
    return tar_path, digest


def test_models_root_uses_storage_root_env(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    assert local_models.models_root() == tmp_path / "models"
    assert local_models.whisper_download_root() == tmp_path / "models" / "whisper"


def test_mt_model_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("YESON_LOCAL_MT_MODEL_DIR", str(tmp_path / "custom"))
    assert local_models.mt_model_dir() == tmp_path / "custom"


def test_ensure_mt_model_returns_existing_dir(monkeypatch, tmp_path):
    model_dir = tmp_path / "mt"
    model_dir.mkdir()
    for name in local_models.MT_REQUIRED_FILES:
        (model_dir / name).write_bytes(b"x")
    monkeypatch.setenv("YESON_LOCAL_MT_MODEL_DIR", str(model_dir))
    assert local_models.ensure_mt_model() == model_dir


def test_ensure_mt_model_downloads_and_extracts(monkeypatch, tmp_path):
    tar_path, digest = _make_mt_tarball(tmp_path)
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.delenv("YESON_LOCAL_MT_MODEL_DIR", raising=False)
    monkeypatch.setenv("YESON_LOCAL_MT_MODEL_URL", tar_path.as_uri())
    monkeypatch.setenv("YESON_LOCAL_MT_MODEL_SHA256", digest)
    result = local_models.ensure_mt_model()
    assert result == tmp_path / "storage" / "models" / "mt-en-ko"
    for name in local_models.MT_REQUIRED_FILES:
        assert (result / name).read_bytes() == b"fake-" + name.encode()


def test_ensure_mt_model_rejects_bad_checksum(monkeypatch, tmp_path):
    tar_path, _ = _make_mt_tarball(tmp_path)
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.delenv("YESON_LOCAL_MT_MODEL_DIR", raising=False)
    monkeypatch.setenv("YESON_LOCAL_MT_MODEL_URL", tar_path.as_uri())
    monkeypatch.setenv("YESON_LOCAL_MT_MODEL_SHA256", "0" * 64)
    with pytest.raises(RuntimeError, match="checksum"):
        local_models.ensure_mt_model()
    # 실패한 다운로드가 캐시 디렉터리를 남기지 않아야 함
    assert not (tmp_path / "storage" / "models" / "mt-en-ko").exists()


def test_ensure_mt_model_no_url_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.delenv("YESON_LOCAL_MT_MODEL_DIR", raising=False)
    monkeypatch.delenv("YESON_LOCAL_MT_MODEL_URL", raising=False)
    with pytest.raises(RuntimeError, match="YESON_LOCAL_MT_MODEL"):
        local_models.ensure_mt_model()
```

- [ ] **Step 5: 실패 확인**

```bash
.venv/bin/python -m pytest apps/server/tests/test_local_models.py -q
```
Expected: FAIL — `ModuleNotFoundError: No module named 'apps.server.ai.local_models'`

- [ ] **Step 6: 구현** — `apps/server/ai/local_models.py`

```python
# === ANCHOR: LOCAL_MODELS_START ===
"""Model path resolution + download cache for the local_whisper provider.

Whisper weights are fetched by faster-whisper's own downloader into
``whisper_download_root()``. The CTranslate2 MT model (Opus-MT en->ko, int8)
is a tarball we host as a GitHub Release asset: ``ensure_mt_model()`` downloads
it once (sha256-verified, atomic extract) into ``{STORAGE_ROOT}/models/mt-en-ko``.
Operators can bypass the download entirely with ``YESON_LOCAL_MT_MODEL_DIR``.
"""
from __future__ import annotations

import hashlib
import logging
import os
import tarfile
import tempfile
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

STORAGE_ROOT_ENV = "STORAGE_ROOT"
DEFAULT_STORAGE_ROOT = "/var/lib/yeson-meet/storage"  # glossary.py와 동일 규약
MT_MODEL_DIR_ENV = "YESON_LOCAL_MT_MODEL_DIR"
MT_MODEL_URL_ENV = "YESON_LOCAL_MT_MODEL_URL"
MT_MODEL_SHA256_ENV = "YESON_LOCAL_MT_MODEL_SHA256"
MT_DIRNAME = "mt-en-ko"
# CTranslate2 변환 결과에 반드시 있어야 하는 파일들 (model.bin + Marian spm 쌍).
MT_REQUIRED_FILES: tuple[str, ...] = ("model.bin", "source.spm", "target.spm")


def models_root() -> Path:
    root = os.environ.get(STORAGE_ROOT_ENV) or DEFAULT_STORAGE_ROOT
    return Path(root) / "models"


def whisper_download_root() -> Path:
    return models_root() / "whisper"


def mt_model_dir() -> Path:
    explicit = os.environ.get(MT_MODEL_DIR_ENV)
    if explicit:
        return Path(explicit)
    return models_root() / MT_DIRNAME


def _is_complete(model_dir: Path) -> bool:
    return all((model_dir / name).is_file() for name in MT_REQUIRED_FILES)


def ensure_mt_model() -> Path:
    """Return a ready MT model dir, downloading it on first use.

    Raises RuntimeError with an operator-actionable message on any failure —
    the caller (provider.stream) lets it propagate so the reconnect loop
    retries with backoff and the console log shows the reason.
    """
    model_dir = mt_model_dir()
    if _is_complete(model_dir):
        return model_dir
    url = os.environ.get(MT_MODEL_URL_ENV)
    if not url:
        raise RuntimeError(
            "local_whisper MT model missing: set YESON_LOCAL_MT_MODEL_DIR to a "
            f"converted CTranslate2 model dir, or YESON_LOCAL_MT_MODEL_URL to a "
            f"model tarball (expected files: {', '.join(MT_REQUIRED_FILES)})"
        )
    expected_sha = os.environ.get(MT_MODEL_SHA256_ENV, "").strip().lower()
    model_dir.parent.mkdir(parents=True, exist_ok=True)
    logger.info("local_whisper: downloading MT model", extra={"mt_url": url})
    with tempfile.TemporaryDirectory(dir=model_dir.parent) as tmp:
        tar_path = Path(tmp) / "mt.tar.gz"
        urllib.request.urlretrieve(url, tar_path)  # noqa: S310 — operator-set URL
        if expected_sha:
            digest = hashlib.sha256(tar_path.read_bytes()).hexdigest()
            if digest != expected_sha:
                raise RuntimeError(
                    f"local_whisper MT model checksum mismatch: got {digest}, "
                    f"expected {expected_sha}"
                )
        extract_dir = Path(tmp) / "extract"
        extract_dir.mkdir()
        with tarfile.open(tar_path, "r:gz") as tar:
            tar.extractall(extract_dir, filter="data")
        # 자산이 최상위 파일들 또는 단일 하위 디렉터리 어느 쪽이어도 수용.
        candidate = extract_dir
        if not _is_complete(candidate):
            subdirs = [p for p in extract_dir.iterdir() if p.is_dir()]
            if len(subdirs) == 1 and _is_complete(subdirs[0]):
                candidate = subdirs[0]
            else:
                raise RuntimeError(
                    "local_whisper MT tarball is missing required files: "
                    + ", ".join(MT_REQUIRED_FILES)
                )
        candidate.replace(model_dir)  # 같은 파일시스템(tmp가 부모 안) → 원자적
    logger.info("local_whisper: MT model ready", extra={"mt_dir": str(model_dir)})
    return model_dir
# === ANCHOR: LOCAL_MODELS_END ===
```

- [ ] **Step 7: 통과 확인**

```bash
.venv/bin/python -m pytest apps/server/tests/test_local_models.py -q
```
Expected: 6 passed

- [ ] **Step 8: 커밋**

```bash
git add apps/server/pyproject.toml uv.lock apps/server/ai/local_models.py apps/server/tests/test_local_models.py
git commit -m "feat(local-whisper): model path/download cache module + deps"
```

---

### Task 2: LocalAgreement-2 확정기 `local_agreement.py`

**Files:**
- Create: `apps/server/ai/local_agreement.py`
- Test: `apps/server/tests/test_local_agreement.py`

**Interfaces:**
- Produces: `Word(text: str, end: float)` frozen dataclass; `LocalAgreementConfirmer` — `feed(words: list[Word]) -> list[Word]`(새로 확정된 단어들), `reset() -> None`(오디오 트림 후 호출), `confirmed_count: int` property

- [ ] **Step 1: 실패 테스트 작성** — `apps/server/tests/test_local_agreement.py`

```python
"""LocalAgreement-2: confirm only the common prefix of two consecutive hypotheses."""
from apps.server.ai.local_agreement import LocalAgreementConfirmer, Word


def _w(*texts: str) -> list[Word]:
    return [Word(text=t, end=float(i + 1)) for i, t in enumerate(texts)]


def test_first_hypothesis_confirms_nothing():
    c = LocalAgreementConfirmer()
    assert c.feed(_w("hello", "world")) == []


def test_second_matching_hypothesis_confirms_prefix():
    c = LocalAgreementConfirmer()
    c.feed(_w("hello", "world", "foo"))
    newly = c.feed(_w("hello", "world", "bar"))
    assert [w.text for w in newly] == ["hello", "world"]
    assert c.confirmed_count == 2


def test_confirmed_words_never_reemitted():
    c = LocalAgreementConfirmer()
    c.feed(_w("a", "b"))
    c.feed(_w("a", "b", "c"))          # confirms a b
    newly = c.feed(_w("a", "b", "c", "d"))  # confirms only c
    assert [w.text for w in newly] == ["c"]


def test_mismatch_confirms_nothing_new():
    c = LocalAgreementConfirmer()
    c.feed(_w("a", "b"))
    c.feed(_w("a", "b"))               # confirms a b
    newly = c.feed(_w("x", "y", "z"))  # prefix diverges before confirmed_count
    assert newly == []
    assert c.confirmed_count == 2      # 확정분은 절대 후퇴하지 않음


def test_comparison_is_case_and_punct_insensitive():
    c = LocalAgreementConfirmer()
    c.feed(_w("Hello,", "world"))
    newly = c.feed(_w("hello", "world."))
    # 확정 텍스트는 "최신 가설의 표기"를 사용
    assert [w.text for w in newly] == ["hello", "world."]


def test_reset_starts_fresh():
    c = LocalAgreementConfirmer()
    c.feed(_w("a", "b"))
    c.feed(_w("a", "b"))
    c.reset()
    assert c.confirmed_count == 0
    assert c.feed(_w("c", "d")) == []
    assert [w.text for w in c.feed(_w("c", "d"))] == ["c", "d"]
```

- [ ] **Step 2: 실패 확인**

```bash
.venv/bin/python -m pytest apps/server/tests/test_local_agreement.py -q
```
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 구현** — `apps/server/ai/local_agreement.py`

```python
# === ANCHOR: LOCAL_AGREEMENT_START ===
"""LocalAgreement-2 word confirmation for streaming whisper re-transcription.

Each whisper pass re-transcribes the same (trimmed) audio buffer, so
consecutive hypotheses are aligned from the buffer start. A word becomes
"confirmed" when two consecutive hypotheses agree on it at the same position
(ufal/whisper_streaming's LocalAgreement-2). Confirmed words are never
retracted — that is what makes the caption text flicker-free.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_STRIP_RE = re.compile(r"[\s.,?!…\"'’·]+")


def _norm(text: str) -> str:
    return _STRIP_RE.sub("", text).lower()


@dataclass(frozen=True)
class Word:
    """One hypothesis word with its end timestamp (seconds from buffer start)."""

    text: str
    end: float


class LocalAgreementConfirmer:
    def __init__(self) -> None:
        self._prev: list[Word] | None = None
        self._confirmed = 0

    @property
    def confirmed_count(self) -> int:
        return self._confirmed

    def feed(self, words: list[Word]) -> list[Word]:
        """Compare with the previous hypothesis; return newly confirmed words."""
        newly: list[Word] = []
        if self._prev is not None:
            limit = min(len(self._prev), len(words))
            i = self._confirmed
            # 확정 경계 이전에서 이미 가설이 갈라졌다면 새 확정 없음(확정분은 유지).
            aligned = all(
                _norm(self._prev[j].text) == _norm(words[j].text)
                for j in range(min(self._confirmed, len(words)))
            ) and len(words) >= self._confirmed
            while (
                aligned
                and i < limit
                and _norm(self._prev[i].text) == _norm(words[i].text)
                and _norm(words[i].text)
            ):
                newly.append(words[i])
                i += 1
            self._confirmed = i
        self._prev = words
        return newly

    def reset(self) -> None:
        """Call after the audio buffer is trimmed — positions are no longer aligned."""
        self._prev = None
        self._confirmed = 0
# === ANCHOR: LOCAL_AGREEMENT_END ===
```

- [ ] **Step 4: 통과 확인**

```bash
.venv/bin/python -m pytest apps/server/tests/test_local_agreement.py -q
```
Expected: 6 passed

- [ ] **Step 5: 커밋**

```bash
git add apps/server/ai/local_agreement.py apps/server/tests/test_local_agreement.py
git commit -m "feat(local-whisper): LocalAgreement-2 word confirmer"
```

---

### Task 3: EN 문장 조립기 `EnSentenceAssembler` (local_agreement.py에 추가)

**Files:**
- Modify: `apps/server/ai/local_agreement.py` (ANCHOR 내부, Confirmer 아래에 추가)
- Test: `apps/server/tests/test_local_agreement.py` (테스트 추가)

**Interfaces:**
- Consumes: `Word` (Task 2)
- Produces: `AssembledUpdate(partial_en: str | None, finals: list[FinalSentence])`; `FinalSentence(text: str, end: float)`; `EnSentenceAssembler(min_final_chars=30, force_final_chars=160)` — `feed(words: list[Word]) -> AssembledUpdate`, `flush() -> list[FinalSentence]`, `has_pending() -> bool`, `idle_flush() -> list[FinalSentence]`(호출 시점 기준 미완결분 전부 final화 — idle 판단은 호출자 책임)

- [ ] **Step 1: 실패 테스트 추가** — `test_local_agreement.py` 끝에 append

```python
from apps.server.ai.local_agreement import EnSentenceAssembler


def test_assembler_partial_grows_until_sentence_end():
    a = EnSentenceAssembler(min_final_chars=10)
    upd = a.feed(_w("The", "cleanup", "team"))
    assert upd.finals == []
    assert upd.partial_en == "The cleanup team"
    upd = a.feed(_w("will", "meet", "today."))
    assert len(upd.finals) == 1
    assert upd.finals[0].text == "The cleanup team will meet today."
    assert upd.finals[0].end == 6.0  # 마지막 단어의 end 타임스탬프
    assert upd.partial_en is None    # 잔여 없음


def test_assembler_short_sentence_merges_until_min_chars():
    a = EnSentenceAssembler(min_final_chars=30)
    upd = a.feed(_w("Yes."))
    assert upd.finals == []          # 너무 짧음 — 다음 문장과 병합
    upd = a.feed(_w("We", "should", "start", "the", "pencil", "test."))
    assert len(upd.finals) == 1
    assert upd.finals[0].text == "Yes. We should start the pencil test."


def test_assembler_decimal_number_is_not_boundary():
    a = EnSentenceAssembler(min_final_chars=5)
    upd = a.feed(_w("cut", "1.5", "needs", "work"))
    assert upd.finals == []


def test_assembler_force_final_without_punctuation():
    a = EnSentenceAssembler(min_final_chars=5, force_final_chars=20)
    upd = a.feed(_w("one", "two", "three", "four", "five", "six"))
    assert len(upd.finals) == 1      # 20자 초과 → 구두점 없어도 강제 final


def test_assembler_flush_emits_remainder():
    a = EnSentenceAssembler()
    a.feed(_w("unfinished", "thought"))
    finals = a.flush()
    assert [f.text for f in finals] == ["unfinished thought"]
    assert a.flush() == []


def test_assembler_idle_flush_same_as_flush_but_reusable():
    a = EnSentenceAssembler()
    a.feed(_w("pause", "here"))
    assert a.has_pending()
    finals = a.idle_flush()
    assert [f.text for f in finals] == ["pause here"]
    assert not a.has_pending()
    # 이후 계속 사용 가능
    upd = a.feed(_w("more", "words."))
    assert upd.partial_en == "more words." or upd.finals
```

- [ ] **Step 2: 실패 확인**

```bash
.venv/bin/python -m pytest apps/server/tests/test_local_agreement.py -q
```
Expected: 신규 테스트들 FAIL — `ImportError: cannot import name 'EnSentenceAssembler'`

- [ ] **Step 3: 구현** — `local_agreement.py`의 `# === ANCHOR: LOCAL_AGREEMENT_END ===` 직전에 추가

```python

# 문장 종결 구두점 — 소수점("1.5")은 경계가 아님 (gemini_live_translate.py:73과 동일 접근)
_SENTENCE_END_RE = re.compile(r"(?<![0-9])[.?!…]|(?<=[0-9])[.?!…](?![0-9])")


def _last_sentence_end(text: str) -> int:
    last = -1
    for match in _SENTENCE_END_RE.finditer(text):
        last = match.end()
    return last


@dataclass(frozen=True)
class FinalSentence:
    """A completed EN sentence and the buffer-relative end time of its last word."""

    text: str
    end: float


@dataclass(frozen=True)
class AssembledUpdate:
    partial_en: str | None
    finals: list[FinalSentence]


class EnSentenceAssembler:
    """Fold confirmed words into caption-sized EN sentences.

    Sentence-end punctuation finalizes once the text reaches ``min_final_chars``
    (shorter sentences merge into the next one — same "감질" rationale as
    gemini_live_translate's DEFAULT_MIN_FINAL_CHARS). ``force_final_chars``
    caps unpunctuated run-ons.
    """

    def __init__(self, min_final_chars: int = 30, force_final_chars: int = 160) -> None:
        self._min_final = min_final_chars
        self._force_final = force_final_chars
        self._words: list[Word] = []

    def has_pending(self) -> bool:
        return bool(self._words)

    def _text(self) -> str:
        return " ".join(w.text for w in self._words).strip()

    def feed(self, words: list[Word]) -> AssembledUpdate:
        if not words:
            return AssembledUpdate(partial_en=None, finals=[])
        self._words.extend(words)
        finals: list[FinalSentence] = []
        text = self._text()
        boundary = _last_sentence_end(text)
        if boundary > 0 and len(text[:boundary].strip()) >= self._min_final:
            finals = self._cut_at_char(boundary)
        elif len(text) >= self._force_final:
            finals = self._cut_at_char(len(text))
        partial = self._text() or None
        return AssembledUpdate(partial_en=None if finals and not partial else partial,
                               finals=finals)

    def _cut_at_char(self, boundary: int) -> list[FinalSentence]:
        """Split the word list at the char boundary (word-granular, >= boundary)."""
        consumed = 0
        cut_index = len(self._words)
        for i, w in enumerate(self._words):
            consumed += len(w.text) + (1 if i else 0)  # 공백 포함 누적 길이
            if consumed >= boundary:
                cut_index = i + 1
                break
        head, tail = self._words[:cut_index], self._words[cut_index:]
        self._words = tail
        text = " ".join(w.text for w in head).strip()
        if not text:
            return []
        return [FinalSentence(text=text, end=head[-1].end)]

    def idle_flush(self) -> list[FinalSentence]:
        """Finalize everything pending (caller decides when 'idle' happened)."""
        if not self._words:
            return []
        return self._cut_at_char(len(self._text()))

    def flush(self) -> list[FinalSentence]:
        """Stream is ending — same as idle_flush."""
        return self.idle_flush()
```

- [ ] **Step 4: 통과 확인**

```bash
.venv/bin/python -m pytest apps/server/tests/test_local_agreement.py -q
```
Expected: 12 passed

- [ ] **Step 5: 커밋**

```bash
git add apps/server/ai/local_agreement.py apps/server/tests/test_local_agreement.py
git commit -m "feat(local-whisper): EN sentence assembler (min/force final + flush)"
```

---

### Task 4: 로컬 MT 래퍼 `local_mt.py`

**Files:**
- Create: `apps/server/ai/local_mt.py`
- Test: `apps/server/tests/test_local_mt.py`

**Interfaces:**
- Consumes: `local_models.ensure_mt_model()` (Task 1)
- Produces: `LocalTranslator(model_dir: Path, intra_threads: int = 4)` — `translate(text: str) -> str` (동기; 호출자가 `asyncio.to_thread`로 감쌈). 생성자에서 lazy import(`ctranslate2`, `sentencepiece`)·모델 로드. `YESON_LOCAL_MT_TARGET_PREFIX` env가 있으면 소스 토큰 앞에 삽입(Opus-MT 다국어 타겟 토큰 규약, 예 `>>kor<<`).

- [ ] **Step 1: 실패 테스트 작성** — `apps/server/tests/test_local_mt.py`

ctranslate2/sentencepiece 실물 없이 검증하기 위해 내부 구성요소를 주입 가능하게 설계한다.

```python
"""LocalTranslator: tokenization → ct2 translate_batch → detokenize plumbing."""
from apps.server.ai.local_mt import LocalTranslator


class FakeSp:
    def __init__(self, decoded="번역결과"):
        self._decoded = decoded
        self.encoded_with: list[str] = []

    def encode(self, text, out_type=str):
        self.encoded_with.append(text)
        return text.split()

    def decode(self, tokens):
        return self._decoded + "|" + " ".join(tokens)


class FakeHyp:
    def __init__(self, tokens):
        self.hypotheses = [tokens]


class FakeCt2:
    def __init__(self):
        self.batches: list[list[list[str]]] = []

    def translate_batch(self, batch, beam_size=2, max_decoding_length=256):
        self.batches.append(batch)
        return [FakeHyp([t.upper() for t in tokens]) for tokens in batch]


def _translator(monkeypatch=None, prefix=""):
    t = LocalTranslator.__new__(LocalTranslator)  # __init__(모델 로드) 우회
    t._sp_src = FakeSp()
    t._sp_tgt = FakeSp()
    t._ct2 = FakeCt2()
    t._target_prefix = prefix
    return t


def test_translate_roundtrip():
    t = _translator()
    out = t.translate("hello world")
    assert out == "번역결과|HELLO WORLD"
    assert t._ct2.batches == [[["hello", "world"]]]


def test_translate_empty_returns_empty():
    t = _translator()
    assert t.translate("   ") == ""
    assert t._ct2.batches == []


def test_target_prefix_prepended_as_token():
    t = _translator(prefix=">>kor<<")
    t.translate("hello")
    assert t._ct2.batches == [[[">>kor<<", "hello"]]]
```

- [ ] **Step 2: 실패 확인**

```bash
.venv/bin/python -m pytest apps/server/tests/test_local_mt.py -q
```
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 구현** — `apps/server/ai/local_mt.py`

```python
# === ANCHOR: LOCAL_MT_START ===
"""CTranslate2 + SentencePiece wrapper for local EN->KO translation.

Loads a converted Opus-MT model dir (model.bin + source.spm/target.spm — see
local_models.MT_REQUIRED_FILES). ``translate`` is synchronous and CPU-bound;
callers run it via ``asyncio.to_thread``. One instance per provider stream.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

TARGET_PREFIX_ENV = "YESON_LOCAL_MT_TARGET_PREFIX"


class LocalTranslator:
    def __init__(self, model_dir: Path, intra_threads: int = 4) -> None:
        import ctranslate2  # lazy: 서버의 다른 provider 경로에서 로드 비용 없음
        import sentencepiece

        self._ct2 = ctranslate2.Translator(
            str(model_dir), device="cpu",
            inter_threads=1, intra_threads=intra_threads,
        )
        self._sp_src = sentencepiece.SentencePieceProcessor(
            model_file=str(model_dir / "source.spm")
        )
        self._sp_tgt = sentencepiece.SentencePieceProcessor(
            model_file=str(model_dir / "target.spm")
        )
        self._target_prefix = os.environ.get(TARGET_PREFIX_ENV, "").strip()
        logger.info("local_whisper: MT translator loaded", extra={"mt_dir": str(model_dir)})

    def translate(self, text: str) -> str:
        text = text.strip()
        if not text:
            return ""
        tokens = self._sp_src.encode(text, out_type=str)
        if self._target_prefix:
            tokens = [self._target_prefix, *tokens]
        results = self._ct2.translate_batch(
            [tokens], beam_size=2, max_decoding_length=256
        )
        return self._sp_tgt.decode(results[0].hypotheses[0]).strip()
# === ANCHOR: LOCAL_MT_END ===
```

- [ ] **Step 4: 통과 확인**

```bash
.venv/bin/python -m pytest apps/server/tests/test_local_mt.py -q
```
Expected: 3 passed

- [ ] **Step 5: 커밋**

```bash
git add apps/server/ai/local_mt.py apps/server/tests/test_local_mt.py
git commit -m "feat(local-whisper): CTranslate2+sentencepiece MT wrapper"
```

---

### Task 5: MT 모델 변환 스크립트 + 실변환·실번역 스모크

**Files:**
- Create: `scripts/convert_local_mt_model.sh`

**Interfaces:**
- Produces: `target/mt-en-ko-ct2/` (model.bin, source.spm, target.spm, …) + `target/mt-en-ko-ct2.tar.gz` + 그 sha256. 이후 Task 8·9에서 `YESON_LOCAL_MT_MODEL_DIR=target/mt-en-ko-ct2`로 사용.

- [ ] **Step 1: 스크립트 작성** — `scripts/convert_local_mt_model.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
# Convert Helsinki-NLP/opus-mt-tc-big-en-ko to CTranslate2 int8 for the
# local_whisper provider. Dev-machine one-shot: the runtime never needs
# transformers/torch. Output: target/mt-en-ko-ct2 (+ tarball + sha256).
cd "$(dirname "$0")/.."

VENV="target/mt-convert-venv"
OUT="target/mt-en-ko-ct2"
MODEL="Helsinki-NLP/opus-mt-tc-big-en-ko"

uv venv --clear --python 3.12 "${VENV}"
VIRTUAL_ENV="${VENV}" uv pip install --python "${VENV}/bin/python" \
    "ctranslate2>=4.0" "transformers>=4.40" sentencepiece torch --torch-backend=cpu

rm -rf "${OUT}"
"${VENV}/bin/ct2-transformers-converter" \
    --model "${MODEL}" \
    --output_dir "${OUT}" \
    --quantization int8 \
    --copy_files source.spm target.spm

# 스모크: 용어·타겟토큰 필요 여부 확인 (>>kor<< 유무 비교 출력)
"${VENV}/bin/python" - "$OUT" <<'PY'
import sys
from pathlib import Path
import ctranslate2, sentencepiece

model_dir = Path(sys.argv[1])
tr = ctranslate2.Translator(str(model_dir), device="cpu")
src = sentencepiece.SentencePieceProcessor(model_file=str(model_dir / "source.spm"))
tgt = sentencepiece.SentencePieceProcessor(model_file=str(model_dir / "target.spm"))

def translate(text, prefix=None):
    tokens = src.encode(text, out_type=str)
    if prefix:
        tokens = [prefix] + tokens
    out = tr.translate_batch([tokens])[0].hypotheses[0]
    return tgt.decode(out)

sample = "The cleanup team will start the pencil test tomorrow."
plain = translate(sample)
prefixed = translate(sample, ">>kor<<")
print("PLAIN    :", plain)
print(">>kor<<  :", prefixed)
korean = lambda s: any("가" <= ch <= "힣" for ch in s)
assert korean(plain) or korean(prefixed), "no Korean output — investigate model/prefix"
print("PREFIX NEEDED:", "no" if korean(plain) else "yes (set YESON_LOCAL_MT_TARGET_PREFIX='>>kor<<')")
PY

tar -C target -czf target/mt-en-ko-ct2.tar.gz mt-en-ko-ct2
shasum -a 256 target/mt-en-ko-ct2.tar.gz
echo "OK: ${OUT} + target/mt-en-ko-ct2.tar.gz"
```

- [ ] **Step 2: 실행 (수 분 소요 — torch cpu 설치 + 모델 다운로드)**

```bash
chmod +x scripts/convert_local_mt_model.sh && bash scripts/convert_local_mt_model.sh
```
Expected: `PLAIN:`/`>>kor<<:` 한국어 출력 라인 + `PREFIX NEEDED: …` + sha256 출력 + `OK:`.
**기록**: PREFIX NEEDED 결과와 sha256 값을 이 계획 문서의 이 자리에 메모로 남길 것 (Task 8·9에서 사용).
**게이트**: 한국어 출력의 품질이 명백히 사용 불가 수준이면 STOP — 스펙 §6에 따라 NLLB-200-distilled-600M로 대체(`ct2-transformers-converter --model facebook/nllb-200-distilled-600M`, `target_prefix="kor_Hang"` — LocalTranslator는 target_prefix env로 이미 대응 가능하나 NLLB는 `--copy_files`가 아닌 HF tokenizer라 spm 파일명이 다름 → 이 경우 계획 수정 필요)를 결정하고 사용자에게 보고.

- [ ] **Step 3: 커밋**

```bash
git add scripts/convert_local_mt_model.sh
git commit -m "feat(local-whisper): Opus-MT en-ko CTranslate2 conversion script"
```

---

### Task 6: provider 본체 `local_whisper_translate.py`

**Files:**
- Create: `apps/server/ai/local_whisper_translate.py`
- Test: `apps/server/tests/test_local_whisper_translate.py`

**Interfaces:**
- Consumes: `LocalAgreementConfirmer`, `Word`, `EnSentenceAssembler`, `FinalSentence`(Tasks 2–3), `LocalTranslator`(Task 4), `local_models`(Task 1), `apply_ko_corrections`(기존), `TranslatedUtterance`(기존)
- Produces: `LocalWhisperTranslateProvider(trace_extra=None, transcribe_fn=None, translator_factory=None)` — `stream(audio, lang_hint) -> AsyncIterator[TranslatedUtterance]`. `transcribe_fn(pcm: np.ndarray) -> list[Word]`와 `translator_factory() -> LocalTranslator`는 테스트 주입 지점(None이면 실물 로드).

- [ ] **Step 1: 실패 테스트 작성** — `apps/server/tests/test_local_whisper_translate.py`

```python
"""Provider orchestration with fake whisper + fake MT (no real models)."""
import asyncio

import pytest

from apps.server.ai.local_agreement import Word
from apps.server.ai.local_whisper_translate import LocalWhisperTranslateProvider


def _pcm_chunk(ms: int = 20) -> bytes:
    # 16kHz s16le mono: 20ms = 320 samples = 640 bytes (sidecar 규약)
    return b"\x00\x00" * (16000 * ms // 1000)


async def _audio(seconds: float):
    for _ in range(int(seconds * 50)):  # 50 x 20ms chunks/sec
        yield _pcm_chunk()


class ScriptedTranscribe:
    """pass 횟수에 따라 미리 정해진 가설을 돌려주는 fake whisper."""

    def __init__(self, script: list[list[Word]]):
        self.script = script
        self.calls = 0

    def __call__(self, pcm):
        hyp = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        return hyp


class FakeTranslator:
    def translate(self, text: str) -> str:
        return f"KO[{text}]"


def _w(*texts: str) -> list[Word]:
    return [Word(text=t, end=float(i + 1)) for i, t in enumerate(texts)]


async def _collect(provider, seconds=6.0):
    out = []
    async for utt in provider.stream(_audio(seconds), "en"):
        out.append(utt)
    return out


@pytest.mark.asyncio
async def test_final_utterance_translated_and_seq_increments():
    script = [
        _w("the", "cleanup", "team", "will"),
        _w("the", "cleanup", "team", "will", "meet", "today."),   # confirms 4
        _w("the", "cleanup", "team", "will", "meet", "today.", "next"),  # confirms rest
        _w("the", "cleanup", "team", "will", "meet", "today.", "next"),
    ]
    provider = LocalWhisperTranslateProvider(
        transcribe_fn=ScriptedTranscribe(script),
        translator_factory=FakeTranslator,
        pass_interval_s=0.05,   # 테스트 가속
        min_new_audio_s=0.0,
        idle_final_s=0.3,
    )
    utts = await _collect(provider, seconds=1.0)
    finals = [u for u in utts if u.is_final]
    assert finals, f"no finals in {utts}"
    assert finals[0].text_ko.startswith("KO[")
    assert finals[0].text_en.startswith("the cleanup team")
    # seq는 1부터, final마다 증가
    assert finals[0].seq == 1
    assert all(u.provider_segment == 1 for u in utts)


@pytest.mark.asyncio
async def test_partials_emitted_before_final():
    script = [
        _w("hello", "world", "x"),
        _w("hello", "world", "y"),      # confirms hello world → partial
        _w("hello", "world", "y"),
    ]
    provider = LocalWhisperTranslateProvider(
        transcribe_fn=ScriptedTranscribe(script),
        translator_factory=FakeTranslator,
        pass_interval_s=0.05, min_new_audio_s=0.0, idle_final_s=10.0,
    )
    utts = await _collect(provider, seconds=0.6)
    partials = [u for u in utts if not u.is_final]
    assert partials and partials[0].text_en == "hello world"
    # 스트림 종료 flush가 남은 텍스트를 final로 방출
    assert utts[-1].is_final


@pytest.mark.asyncio
async def test_provider_segment_increments_per_stream_call():
    provider = LocalWhisperTranslateProvider(
        transcribe_fn=ScriptedTranscribe([_w("a", "b."), _w("a", "b.")]),
        translator_factory=FakeTranslator,
        pass_interval_s=0.05, min_new_audio_s=0.0,
    )
    first = await _collect(provider, seconds=0.3)
    second = await _collect(provider, seconds=0.3)
    assert all(u.provider_segment == 1 for u in first)
    assert all(u.provider_segment == 2 for u in second)


@pytest.mark.asyncio
async def test_mt_failure_falls_back_to_empty_ko():
    class BrokenTranslator:
        def translate(self, text):
            raise RuntimeError("mt exploded")

    provider = LocalWhisperTranslateProvider(
        transcribe_fn=ScriptedTranscribe([_w("a", "b", "c."), _w("a", "b", "c.")]),
        translator_factory=BrokenTranslator,
        pass_interval_s=0.05, min_new_audio_s=0.0,
    )
    utts = await _collect(provider, seconds=0.3)
    finals = [u for u in utts if u.is_final]
    assert finals and finals[0].text_ko == ""   # EN이라도 표시(스펙 §4)
    assert finals[0].text_en
```

- [ ] **Step 2: 실패 확인**

```bash
.venv/bin/python -m pytest apps/server/tests/test_local_whisper_translate.py -q
```
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 구현** — `apps/server/ai/local_whisper_translate.py`

```python
# === ANCHOR: LOCAL_WHISPER_TRANSLATE_START ===
"""Fully-local caption provider: faster-whisper streaming STT + CTranslate2 MT.

Zero cloud calls. EN meeting audio → KO captions at ~3-5s confirmed latency
(the 2026-07-01 measurement: whisper base int8 + LocalAgreement-2 keeps up on
CPU; small runs away — do NOT bump the model size). Audio is re-transcribed
~every second; LocalAgreement-2 confirms the stable prefix; confirmed EN folds
into sentences; each final sentence is translated locally and patched via
``glossary.apply_ko_corrections``. Partial KO is a throttled re-translation of
the in-progress sentence (may be rewritten); final KO never changes.

Emission contract matches gemini_live_translate: seq starts at 1 per
``stream()`` call, ``provider_segment`` increments per call, so
AISequenceNormalizer re-offsets across reconnects.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator, Callable, Mapping
from datetime import datetime, timezone

import numpy as np

from apps.server.ai import local_models
from apps.server.ai.glossary import apply_ko_corrections
from apps.server.ai.local_agreement import (
    EnSentenceAssembler,
    LocalAgreementConfirmer,
    Word,
)
from apps.server.ai.local_mt import LocalTranslator
from apps.server.ai.providers import TranslatedUtterance

logger = logging.getLogger(__name__)

INPUT_SAMPLE_RATE = 16000
WHISPER_MODEL_ENV = "YESON_LOCAL_WHISPER_MODEL"
WHISPER_THREADS_ENV = "YESON_LOCAL_WHISPER_THREADS"
DEFAULT_WHISPER_MODEL = "base"
DEFAULT_WHISPER_THREADS = 4
# 재전사 페이싱: 새 오디오가 이만큼 쌓였을 때만 whisper를 돌린다.
DEFAULT_PASS_INTERVAL_S = 0.25
DEFAULT_MIN_NEW_AUDIO_S = 1.0
# 확정 텍스트가 이 시간 동안 늘지 않으면 미완결 문장을 final로 내린다.
DEFAULT_IDLE_FINAL_S = 2.5
# partial KO 재번역 스로틀.
PARTIAL_MT_INTERVAL_S = 1.0
# 오디오 버퍼 상한 — 초과 시 최신 구간만 남기고 강제 트림(약한 CPU 폭주 방지).
MAX_BUFFER_S = 25.0


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


class LocalWhisperTranslateProvider:
    """STTProvider: local whisper STT + local MT, EN→KO only."""

    def __init__(
        self,
        trace_extra: Mapping[str, object] | None = None,
        transcribe_fn: Callable[[np.ndarray], list[Word]] | None = None,
        translator_factory: Callable[[], object] | None = None,
        pass_interval_s: float = DEFAULT_PASS_INTERVAL_S,
        min_new_audio_s: float = DEFAULT_MIN_NEW_AUDIO_S,
        idle_final_s: float = DEFAULT_IDLE_FINAL_S,
    ) -> None:
        self._trace_extra = dict(trace_extra or {})
        self._transcribe_fn = transcribe_fn
        self._translator_factory = translator_factory
        self._pass_interval_s = pass_interval_s
        self._min_new_audio_s = min_new_audio_s
        self._idle_final_s = idle_final_s
        self._segment_index = 0

    # -- 실물 모델 로딩 (테스트에서는 주입으로 우회) --------------------------

    def _load_real(self) -> tuple[Callable[[np.ndarray], list[Word]], object]:
        """Blocking: ensure models on disk, load whisper + MT. Run in a thread."""
        from faster_whisper import WhisperModel

        mt_dir = local_models.ensure_mt_model()
        threads = _int_env(WHISPER_THREADS_ENV, DEFAULT_WHISPER_THREADS)
        model_name = os.environ.get(WHISPER_MODEL_ENV, DEFAULT_WHISPER_MODEL)
        whisper = WhisperModel(
            model_name,
            device="cpu",
            compute_type="int8",
            cpu_threads=threads,
            download_root=str(local_models.whisper_download_root()),
        )
        translator = LocalTranslator(mt_dir, intra_threads=threads)

        def transcribe(pcm: np.ndarray) -> list[Word]:
            segments, _info = whisper.transcribe(
                pcm,
                language="en",
                word_timestamps=True,
                vad_filter=True,
                condition_on_previous_text=False,
            )
            words: list[Word] = []
            for seg in segments:
                for w in seg.words or []:
                    words.append(Word(text=w.word.strip(), end=float(w.end)))
            return words

        return transcribe, translator

    # -- 스트림 본체 ----------------------------------------------------------

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        lang_hint: str,
    ) -> AsyncIterator[TranslatedUtterance]:
        self._segment_index += 1
        segment = self._segment_index
        trace = {**self._trace_extra, "local_whisper_segment": segment}

        if self._transcribe_fn is not None and self._translator_factory is not None:
            transcribe, translator = self._transcribe_fn, self._translator_factory()
        else:
            logger.info("local_whisper: loading models", extra=trace)
            transcribe, translator = await asyncio.to_thread(self._load_real)
            logger.info("local_whisper: models ready", extra=trace)

        buffer = np.empty(0, dtype=np.float32)
        buffer_lock = asyncio.Lock()
        audio_done = asyncio.Event()

        async def read_audio() -> None:
            nonlocal buffer
            try:
                async for chunk in audio:
                    pcm = (
                        np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0
                    )
                    async with buffer_lock:
                        buffer = np.concatenate([buffer, pcm])
            finally:
                audio_done.set()

        reader = asyncio.create_task(read_audio())
        confirmer = LocalAgreementConfirmer()
        assembler = EnSentenceAssembler()

        seq = 1
        started_at: datetime | None = None
        last_confirm_mono = time.monotonic()
        last_partial_mt = 0.0
        transcribed_len = 0  # 마지막 pass 시점의 버퍼 길이(샘플)
        first_caption = False
        stream_started = time.monotonic()

        def _utt(en: str, ko: str, *, is_final: bool) -> TranslatedUtterance:
            nonlocal started_at
            return TranslatedUtterance(
                seq=seq,
                text_en=en.strip(),
                text_ko=apply_ko_corrections(ko.strip()) if ko else "",
                started_at=started_at or datetime.now(timezone.utc),
                ended_at=datetime.now(timezone.utc),
                is_final=is_final,
                provider_segment=segment,
            )

        async def _translate(text: str) -> str:
            try:
                return await asyncio.to_thread(translator.translate, text)
            except Exception:
                logger.exception("local_whisper: MT failed", extra=trace)
                return ""

        try:
            while True:
                await asyncio.sleep(self._pass_interval_s)
                async with buffer_lock:
                    snapshot = buffer.copy()
                new_audio_s = (len(snapshot) - transcribed_len) / INPUT_SAMPLE_RATE
                ending = audio_done.is_set()
                if new_audio_s < self._min_new_audio_s and not ending:
                    continue
                if len(snapshot) == transcribed_len and ending:
                    break
                transcribed_len = len(snapshot)

                if len(snapshot) / INPUT_SAMPLE_RATE > MAX_BUFFER_S:
                    # 폭주 방지: 최신 구간만 유지, 확정 상태 리셋(자막 공백 감수)
                    keep = int(MAX_BUFFER_S * 0.6 * INPUT_SAMPLE_RATE)
                    logger.warning(
                        "local_whisper: buffer overflow — force trim",
                        extra={**trace, "buffer_s": len(snapshot) / INPUT_SAMPLE_RATE},
                    )
                    async with buffer_lock:
                        buffer = buffer[-keep:]
                    transcribed_len = 0
                    confirmer.reset()
                    continue

                words = await asyncio.to_thread(transcribe, snapshot)
                newly = confirmer.feed(words)
                update = assembler.feed(newly)
                now = time.monotonic()
                if newly:
                    last_confirm_mono = now
                    if started_at is None:
                        started_at = datetime.now(timezone.utc)

                finals = list(update.finals)
                # idle: 확정 텍스트가 한동안 늘지 않으면 미완결분을 final로
                if (
                    not finals
                    and assembler.has_pending()
                    and now - last_confirm_mono >= self._idle_final_s
                ):
                    finals = assembler.idle_flush()

                trim_to: float | None = None
                for final in finals:
                    ko = await _translate(final.text)
                    yield _utt(final.text, ko, is_final=True)
                    first_caption = first_caption or _log_first(
                        trace, stream_started, first_caption
                    )
                    seq += 1
                    started_at = None
                    trim_to = final.end
                if trim_to is not None:
                    # 확정 문장 끝까지의 오디오를 버림 → 재전사 비용/버퍼 상한 유지
                    cut = int(trim_to * INPUT_SAMPLE_RATE)
                    async with buffer_lock:
                        buffer = buffer[cut:]
                    transcribed_len = max(0, transcribed_len - cut)
                    confirmer.reset()

                if update.partial_en and not finals:
                    ko = ""
                    if now - last_partial_mt >= PARTIAL_MT_INTERVAL_S:
                        last_partial_mt = now
                        ko = await _translate(update.partial_en)
                    yield _utt(update.partial_en, ko, is_final=False)
                    first_caption = first_caption or _log_first(
                        trace, stream_started, first_caption
                    )

                if ending and not assembler.has_pending() and not newly:
                    break
        finally:
            reader.cancel()
            try:
                await reader
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

        for final in assembler.flush():
            ko = await _translate(final.text)
            yield _utt(final.text, ko, is_final=True)
            seq += 1
        logger.info("local_whisper: stream ended", extra=trace)


def _log_first(trace: dict, stream_started: float, already: bool) -> bool:
    if not already:
        logger.info(
            "local_whisper: first caption",
            extra={
                **trace,
                "first_caption_ms": round((time.monotonic() - stream_started) * 1000),
            },
        )
    return True
# === ANCHOR: LOCAL_WHISPER_TRANSLATE_END ===
```

- [ ] **Step 4: 통과 확인**

```bash
.venv/bin/python -m pytest apps/server/tests/test_local_whisper_translate.py -q
```
Expected: 4 passed. (타이밍 의존 테스트가 flaky하면 `pass_interval_s`/`idle_final_s` 파라미터를 조정해 결정적으로 만들 것 — sleep 기반 검증 금지, 파라미터 주입으로 해결.)

- [ ] **Step 5: 전체 서버 테스트 회귀 확인**

```bash
.venv/bin/python -m pytest -q 2>&1 | tail -3
```
Expected: 기존 306+ 전부 passed (신규 포함).

- [ ] **Step 6: 커밋**

```bash
git add apps/server/ai/local_whisper_translate.py apps/server/tests/test_local_whisper_translate.py
git commit -m "feat(local-whisper): provider — streaming whisper + local MT orchestration"
```

---

### Task 7: 등록 — 서버 팩토리 + 콘솔 드롭다운

**Files:**
- Modify: `apps/server/ws/sidecar.py:28-31` (import), `apps/server/ws/sidecar.py:120-136` (`create_ai_provider`)
- Modify: `apps/server_desktop/src/setup/ServerConfigPanel.tsx:23` (`PROVIDERS`)
- Test: `apps/server/tests/test_local_whisper_translate.py` (팩토리 테스트 추가)

**Interfaces:**
- Consumes: `LocalWhisperTranslateProvider` (Task 6)
- Produces: `YESON_AI_PROVIDER=local_whisper`(별칭 `local`, `whisper_local`)로 provider 생성

- [ ] **Step 1: 실패 테스트 추가** — `test_local_whisper_translate.py` 끝에 append

```python
def test_create_ai_provider_local_whisper(monkeypatch):
    from apps.server.ws.sidecar import create_ai_provider

    monkeypatch.setenv("YESON_AI_PROVIDER", "local_whisper")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)  # API 키 없어도 생성됨
    provider = create_ai_provider()
    assert isinstance(provider, LocalWhisperTranslateProvider)


def test_create_ai_provider_local_alias(monkeypatch):
    from apps.server.ws.sidecar import create_ai_provider

    monkeypatch.setenv("YESON_AI_PROVIDER", "whisper_local")
    provider = create_ai_provider()
    assert isinstance(provider, LocalWhisperTranslateProvider)
```

- [ ] **Step 2: 실패 확인**

```bash
.venv/bin/python -m pytest apps/server/tests/test_local_whisper_translate.py -q
```
Expected: 신규 2건 FAIL (기본 Gemini 분기로 빠져 None 또는 GeminiLiveProvider 반환)

- [ ] **Step 3: sidecar.py 수정**

import 블록(`sidecar.py:30` `GoogleSttTranslateProvider` 다음 줄)에 추가:

```python
from apps.server.ai.local_whisper_translate import LocalWhisperTranslateProvider
```

`create_ai_provider()`의 `gemini_live_translate` 분기(`:130-133`) 다음, 기본 Gemini 분기(`:134`) 앞에 추가:

```python
    if provider_name in {"local_whisper", "local", "whisper_local"}:
        # 완전 로컬 — API 키 게이트 없음. 모델 파일은 stream() 첫 호출에서
        # 보장(다운로드 실패 시 reconnect 루프가 백오프 재시도 + 콘솔 로그).
        return LocalWhisperTranslateProvider(trace_extra=trace_extra)
```

- [ ] **Step 4: 통과 확인**

```bash
.venv/bin/python -m pytest apps/server/tests/test_local_whisper_translate.py apps/server/tests/test_ws_sidecar_binary.py -q
```
Expected: all passed

- [ ] **Step 5: 콘솔 드롭다운 추가** — `ServerConfigPanel.tsx:23`

```ts
const PROVIDERS = ["gemini_live", "gemini_live_translate", "google_stt_translate", "local_whisper"] as const;
```

- [ ] **Step 6: 콘솔 타입체크**

```bash
cd apps/server_desktop && pnpm exec tsc --noEmit && cd ../..
```
Expected: 에러 없음

- [ ] **Step 7: 커밋**

```bash
git add apps/server/ws/sidecar.py apps/server_desktop/src/setup/ServerConfigPanel.tsx apps/server/tests/test_local_whisper_translate.py
git commit -m "feat(local-whisper): register provider + console dropdown"
```

---

### Task 8: 실모델 통합 검증 + frozen bundle 재동결

**Files:**
- Create: `scripts/probe_local_whisper.py` (검증용, 커밋함 — 재실측 재료)
- Modify: `apps/server_desktop/scripts/build-server.sh:55-72` (PyInstaller 플래그)

**Interfaces:**
- Consumes: Task 5의 `target/mt-en-ko-ct2` + Task 6 provider

- [ ] **Step 1: 프로브 스크립트 작성** — `scripts/probe_local_whisper.py`

```python
"""Real-model streaming probe for the local_whisper provider.

Feeds a WAV file (16kHz mono s16le) in real time and prints each utterance
with its latency vs. the audio position. Usage:
    YESON_LOCAL_MT_MODEL_DIR=target/mt-en-ko-ct2 \
    STORAGE_ROOT=target/probe-storage \
    .venv/bin/python scripts/probe_local_whisper.py <wav_path>
"""
import asyncio
import sys
import time
import wave

from apps.server.ai.local_whisper_translate import LocalWhisperTranslateProvider

CHUNK_MS = 20


async def main(path: str) -> None:
    with wave.open(path, "rb") as wav:
        assert wav.getframerate() == 16000 and wav.getnchannels() == 1, (
            "need 16kHz mono WAV"
        )
        pcm = wav.readframes(wav.getnframes())

    chunk_bytes = 16000 * 2 * CHUNK_MS // 1000
    t0 = time.monotonic()

    async def audio():
        for i in range(0, len(pcm), chunk_bytes):
            target = t0 + (i / (16000 * 2))
            await asyncio.sleep(max(0.0, target - time.monotonic()))
            yield pcm[i : i + chunk_bytes]

    provider = LocalWhisperTranslateProvider()
    async for utt in provider.stream(audio(), "en"):
        wall = time.monotonic() - t0
        kind = "FINAL" if utt.is_final else "part "
        print(f"[{wall:6.1f}s] {kind} seq={utt.seq} EN={utt.text_en!r} KO={utt.text_ko!r}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
```

- [ ] **Step 2: 실행 (7/1 실측에 쓴 53초 영어 WAV 또는 임의 영어 회의 WAV 사용)**

```bash
YESON_LOCAL_MT_MODEL_DIR=target/mt-en-ko-ct2 STORAGE_ROOT=target/probe-storage \
  .venv/bin/python scripts/probe_local_whisper.py <wav_path>
```
Expected/게이트:
- FINAL 자막이 발화 시점 대비 대략 3~6초 내에 나온다 (7/1 실측 2.93초 + MT 오버헤드)
- KO 텍스트가 자연스러운 한국어이고, "cleanup"류 용어가 glossary 보정을 받는다
- 53초 오디오 처리 중 backlog 폭주(지연이 계속 증가)가 없다
- 미달 시 STOP: 원인(whisper 페이싱/트림 로직/MT 속도)을 파악해 수정 후 재실행

- [ ] **Step 3: build-server.sh 플래그 추가** — `--collect-submodules lxml \`(`build-server.sh:67`) 다음에 추가:

```bash
    --collect-all faster_whisper \
    --collect-all ctranslate2 \
    --collect-all onnxruntime \
    --collect-all av \
    --hidden-import sentencepiece \
```

- [ ] **Step 4: 재동결 + 스모크**

```bash
bash apps/server_desktop/scripts/build-server.sh
```
Expected: 빌드 성공 + `smoke-server-bundle.sh` 통과 + 번들 크기 출력(증가분 기록 — ~150MB 이내 예상). 
frozen provider 부팅 확인:

```bash
YESON_AI_PROVIDER=local_whisper YESON_LOCAL_MT_MODEL_DIR="$(pwd)/target/mt-en-ko-ct2" \
  STORAGE_ROOT=target/probe-storage \
  ./apps/server_desktop/src-tauri/binaries/yeson-server-x86_64-apple-darwin/yeson-server --help >/dev/null 2>&1 \
  || ./apps/server_desktop/src-tauri/binaries/yeson-server-x86_64-apple-darwin/yeson-server & sleep 8; kill %1
```
(정확한 부팅 확인 방법은 smoke-server-bundle.sh의 기존 패턴을 따를 것 — 핵심은 frozen 번들에서 `import faster_whisper/ctranslate2/sentencepiece`가 성공하고 서버가 뜨는 것. 실패 시 PyInstaller hidden-import 누락을 로그에서 찾아 플래그 보강.)

- [ ] **Step 5: 커밋**

```bash
git add scripts/probe_local_whisper.py apps/server_desktop/scripts/build-server.sh
git commit -m "feat(local-whisper): real-model probe + frozen bundle flags"
```

---

### Task 9: 릴리스 자산 + E2E + 문서/메모리 갱신

**Files:**
- Modify: `docs/caption-latency-research-2026-07-01.md` (부록 3 추가)
- Modify: ROADMAP/PRD 체크박스 (해당 항목 있으면 — `feedback_docs_after_slice` 규칙)

- [ ] **Step 1: MT 모델 tarball을 GitHub Release 자산으로 업로드**

```bash
gh release create models-mt-en-ko-v1 target/mt-en-ko-ct2.tar.gz \
  --title "local_whisper MT model (opus-mt-tc-big-en-ko ct2 int8)" \
  --notes "CTranslate2 int8 conversion of Helsinki-NLP/opus-mt-tc-big-en-ko (CC-BY-4.0). Used by the local_whisper caption provider." \
  --prerelease
```
업로드 후 자산 URL과 Task 5에서 기록한 sha256을 확인.

- [ ] **Step 2: 기본 URL 배선**

`apps/server/ai/local_models.py`의 `MT_MODEL_URL_ENV` 읽는 부분을 기본값 포함으로 수정:

```python
DEFAULT_MT_MODEL_URL = (
    "https://github.com/<owner>/<repo>/releases/download/models-mt-en-ko-v1/mt-en-ko-ct2.tar.gz"
)
DEFAULT_MT_MODEL_SHA256 = "<Task 5에서 기록한 sha256>"
```
`ensure_mt_model()`에서 `url = os.environ.get(MT_MODEL_URL_ENV) or DEFAULT_MT_MODEL_URL`, `expected_sha = (os.environ.get(MT_MODEL_SHA256_ENV) or DEFAULT_MT_MODEL_SHA256).strip().lower()`로 변경. (`<owner>/<repo>`는 `git remote get-url origin`으로 확인해 실값 기입.)
`test_local_models.py`의 `test_ensure_mt_model_no_url_raises`는 "URL도 기본값도 없을 때"가 사라지므로 **삭제하고**, 대신 env가 기본값을 오버라이드하는지 확인하는 테스트로 교체:

```python
def test_ensure_mt_model_env_overrides_default_url(monkeypatch, tmp_path):
    tar_path, digest = _make_mt_tarball(tmp_path)
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.delenv("YESON_LOCAL_MT_MODEL_DIR", raising=False)
    monkeypatch.setenv("YESON_LOCAL_MT_MODEL_URL", tar_path.as_uri())
    monkeypatch.setenv("YESON_LOCAL_MT_MODEL_SHA256", digest)
    assert local_models.ensure_mt_model().is_dir()
```

```bash
.venv/bin/python -m pytest apps/server/tests/test_local_models.py -q
```
Expected: all passed

- [ ] **Step 3: 자동 다운로드 경로 실검증** (캐시 비우고 릴리스에서 실제로 받아지는지)

```bash
rm -rf target/probe-storage/models/mt-en-ko
unset YESON_LOCAL_MT_MODEL_DIR
STORAGE_ROOT=target/probe-storage .venv/bin/python -c "
from apps.server.ai.local_models import ensure_mt_model; print(ensure_mt_model())"
```
Expected: 다운로드 후 경로 출력

- [ ] **Step 4: 데스크톱 E2E (수동, tauri:dev)**

```bash
cd apps/server_desktop && pnpm tauri:dev
```
체크리스트:
- 콘솔 config에서 provider `local_whisper` 선택 → 저장 → 서버 재시작
- 클라이언트로 실오디오(영어) 재생 → 뷰어에 KO 자막 표시
- 회의 종료 → 회의록/보고서 정상 생성
- provider를 `gemini_live`로 되돌려 회귀 없음 확인
- 회의 진행 중 네트워크 탭/로그에 외부 호출 0건(모델 다운로드 이후) 확인

- [ ] **Step 5: 문서 갱신**

`docs/caption-latency-research-2026-07-01.md` 말미에 부록 3 추가: local_whisper 구현·실측 결과(지연/품질/번들 크기), 경로 C가 "구현됨"으로 승격되었음을 §5 표에 반영. ROADMAP/PRD에 해당 체크박스가 있으면 갱신.

- [ ] **Step 6: 최종 커밋 + 전체 회귀**

```bash
.venv/bin/python -m pytest -q 2>&1 | tail -3
cd apps/server_desktop && pnpm exec tsc --noEmit && cd ../..
git add -A && git commit -m "feat(local-whisper): default model URL + docs

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-Review 결과 (작성 시 수행)

- 스펙 커버리지: §3.1 provider(Task 6), §3.2 방출 정책(Tasks 3·6), §3.3 모델 관리(Tasks 1·5·9), §3.4 등록 5지점(Tasks 1·7·8), §4 오류 처리(Tasks 1·6 — None 폴백은 기존 규약 그대로, MT 실패 `text_ko=""` 테스트 포함), §5 테스트(각 Task + Task 8 통합 + Task 9 E2E), §6 리스크(Task 5 게이트 = Opus-MT 실확인) — 전부 매핑됨.
- 타입 일관성: `Word(text,end)`·`FinalSentence(text,end)`·`AssembledUpdate(partial_en,finals)`·`LocalTranslator.translate(str)->str`·provider 주입 파라미터(`transcribe_fn`,`translator_factory`) 이름이 Task 간 일치.
- 미확정으로 남긴 것(의도적): Task 5의 PREFIX NEEDED 결과·sha256(실행 시 기록), Task 9의 `<owner>/<repo>`(실행 시 조회). 실행 중 채워지는 값이며 placeholder 아님.
