# RMS-Based Silence Detection (unified) — Implementation Plan (Phase 3 slice 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the capture-status chip's 🟡 silent state fire correctly on both platforms by judging silence on chunk *loudness* (RMS dBFS) instead of chunk *presence*.

**Architecture:** The capture-status decider gains a second timestamp, `last_loud_at` (last chunk with dBFS ≥ threshold). Silence = no loud chunk for ≥10 s; `last_chunk_at` (any chunk) only exits "connecting". The sidecar computes per-chunk dBFS in `stream_audio` via a new `pcm16_dbfs` helper (reusing `rms.py`). This unifies Windows (no packets in silence) and Mac (silent packets below threshold). The desktop chip/markers are unchanged.

**Tech Stack:** Python sidecar (`numpy`, `asyncio`), pytest. The decider stays a pure clock-injected function unit-tested on macOS; only the live capture loop needs E2E.

**Spec:** `docs/superpowers/specs/2026-06-10-rms-silence-detection-design.md` (read §0 motivation, §2 mechanism, §3 RMS).

---

## Environment Legend

- **[MAC]** — runs on the macOS dev machine (pytest, ruff, live sidecar+helper smoke).
- **[WIN]** — runs on real Windows (regression: the 4 chip states still work).
- **[BOTH]** — runs on either.

Everything except the Windows regression E2E (Task 6) is **[MAC]**. The key new behavior — "quiet chunks keep arriving but the chip still goes silent" — is provable in a pure pytest (Task 2) AND observable live on this Mac (Task 5).

---

## File Structure

```
apps/client_sidecar/
  audio/rms.py                    # MODIFY add pcm16_dbfs(chunk: bytes) -> float
  audio/tests/                    # (rms tested in client_sidecar/tests)
  transport/capture_status.py     # MODIFY compute_state(+last_loud_at), Reporter(+rms_threshold, note_chunk(now,dbfs)), RMS_SILENCE_DBFS
  transport/audio_ws.py           # MODIFY note_chunk(now) -> note_chunk(now, pcm16_dbfs(chunk))
  main.py                         # MODIFY pass rms_threshold_dbfs from config.audio.RMS_DBFS_THRESHOLD
  tests/test_capture_status.py    # MODIFY new signatures + RMS/Mac-silence cases
  tests/test_rms.py               # NEW (or extend) pcm16_dbfs tests
```

Desktop (`captureStatus.ts`, `CaptureStatusChip.tsx`) is **unchanged** — same `CAPTURE_STATUS` markers and 4 states. Task 5 only re-runs the desktop suite to confirm no regression.

**pytest path quirk:** the root pyproject `testpaths` points only at the server; run sidecar tests with the explicit path (e.g. `uv run pytest apps/client_sidecar/tests -q`). Never bare `pytest`.

**Commit convention:** every commit message ends with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`. Branch is `topyeson` (already checked out). After `git commit` a VibeLign guard report may print; the commit still succeeds — proceed.

---

## Task 1: `pcm16_dbfs` helper in `rms.py` [MAC TDD]

`rms.py::rms_dbfs` takes a float32 numpy array, but the transport layer has 16-bit-LE PCM **bytes**. Add a tiny converter so `audio_ws` can get a dBFS from a raw chunk.

**Files:**
- Modify: `apps/client_sidecar/audio/rms.py`
- Create: `apps/client_sidecar/tests/test_rms.py`

- [ ] **Step 1: Write the failing test**

Create `apps/client_sidecar/tests/test_rms.py`:

```python
"""pcm16_dbfs: RMS dBFS of a 16-bit LE PCM chunk."""
from apps.client_sidecar.audio.rms import pcm16_dbfs


def test_silence_bytes_are_very_low():
    # 640 bytes of zeros (320 silent s16 samples) → far below any real threshold
    assert pcm16_dbfs(b"\x00\x00" * 320) < -100.0


def test_full_scale_is_near_zero_dbfs():
    # int16 +full scale = 0x7FFF, little-endian bytes b"\xff\x7f"
    dbfs = pcm16_dbfs(b"\xff\x7f" * 320)
    assert -1.0 < dbfs <= 0.0


def test_empty_chunk_is_floor():
    # rms_dbfs returns -120.0 for empty input; pcm16_dbfs inherits that
    assert pcm16_dbfs(b"") == -120.0


def test_quiet_below_threshold_loud_above():
    # a small-amplitude tone is below -45; a large one is above
    quiet = pcm16_dbfs((256).to_bytes(2, "little", signed=True) * 320)  # ~ -42? verify below
    loud = pcm16_dbfs((16384).to_bytes(2, "little", signed=True) * 320)  # 0.5 FS ≈ -6 dBFS
    assert quiet < loud
    assert loud > -45.0
```

- [ ] **Step 2: Run the test to verify it FAILS**

Run: `uv run pytest apps/client_sidecar/tests/test_rms.py -q`
Expected: FAIL — `cannot import name 'pcm16_dbfs'`.

- [ ] **Step 3: Implement the helper**

In `apps/client_sidecar/audio/rms.py`, add inside the `# === ANCHOR: RMS_* ===` region (after `rms_dbfs`, before `RmsLogger`):

```python
# === ANCHOR: RMS_PCM16_DBFS_START ===
def pcm16_dbfs(chunk: bytes) -> float:
    """RMS dBFS of a 16-bit little-endian mono PCM chunk (what the sidecar sends).

    Converts to normalized float32 then defers to ``rms_dbfs``. Empty/odd input
    is treated as silence-floor by ``rms_dbfs`` (size 0 → -120.0)."""
    samples = np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0
    return rms_dbfs(samples)
# === ANCHOR: RMS_PCM16_DBFS_END ===
```

(`np` is already imported at the top of `rms.py`.)

- [ ] **Step 4: Run the test to verify it PASSES**

Run: `uv run pytest apps/client_sidecar/tests/test_rms.py -q`
Expected: 4 tests pass. If `test_quiet_below_threshold_loud_above` is brittle on the exact `quiet` value, keep only the two assertions shown (they hold: 256/32768 ≈ 0.0078 → ≈ -42 dBFS is NOT guaranteed below -45, so the test only asserts `quiet < loud` and `loud > -45`, both robust).

- [ ] **Step 5: Lint**

Run: `uvx ruff check apps/client_sidecar/audio/rms.py apps/client_sidecar/tests/test_rms.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add apps/client_sidecar/audio/rms.py apps/client_sidecar/tests/test_rms.py
git commit -m "feat(sidecar): pcm16_dbfs helper for RMS dBFS of s16le chunks"
```

---

## Task 2: RMS loudness in the capture-status decider + reporter [MAC TDD]

Add `last_loud_at` to the pure decider and the reporter. This is the heart of the slice. The existing `test_capture_status.py` calls the OLD signatures, so it is rewritten in full here.

**Files:**
- Modify: `apps/client_sidecar/transport/capture_status.py`
- Modify: `apps/client_sidecar/tests/test_capture_status.py` (full rewrite)

- [ ] **Step 1: Rewrite the test file with the new signatures + RMS cases**

Replace the entire contents of `apps/client_sidecar/tests/test_capture_status.py` with:

```python
"""Pure capture-status decider + reporter coalescing (RMS-loudness silence)."""
from apps.client_sidecar.transport.capture_status import (
    ACTIVE,
    CONNECTING,
    SILENT,
    TRANSPORT_DOWN,
    CaptureStatusReporter,
    compute_state,
)

T = 10.0      # silence time threshold
LOUD = -10.0  # dBFS above the -45 default → counts as audio
QUIET = -80.0 # dBFS below threshold → silence


def test_connecting_before_first_connect():
    assert compute_state(
        ws_connected=False, ever_connected=False,
        last_chunk_at=None, last_loud_at=None, now=5.0, threshold=T,
    ) == CONNECTING


def test_connecting_after_connect_before_first_chunk():
    assert compute_state(
        ws_connected=True, ever_connected=True,
        last_chunk_at=None, last_loud_at=None, now=5.0, threshold=T,
    ) == CONNECTING


def test_active_on_recent_loud_chunk():
    assert compute_state(
        ws_connected=True, ever_connected=True,
        last_chunk_at=105.0, last_loud_at=100.0, now=105.0, threshold=T,
    ) == ACTIVE


def test_silent_after_threshold_since_last_loud():
    # gap from last LOUD chunk hits threshold → silent (even though a chunk arrived at 105)
    assert compute_state(
        ws_connected=True, ever_connected=True,
        last_chunk_at=105.0, last_loud_at=100.0, now=110.0, threshold=T,
    ) == SILENT


def test_silent_when_chunks_flow_but_never_loud():
    # THE Mac case: chunks present (last_chunk_at recent) but no loud chunk ever → silent
    assert compute_state(
        ws_connected=True, ever_connected=True,
        last_chunk_at=110.0, last_loud_at=None, now=110.0, threshold=T,
    ) == SILENT


def test_transport_down_after_disconnect():
    assert compute_state(
        ws_connected=False, ever_connected=True,
        last_chunk_at=100.0, last_loud_at=100.0, now=101.0, threshold=T,
    ) == TRANSPORT_DOWN


def test_transport_down_takes_priority_over_silence():
    assert compute_state(
        ws_connected=False, ever_connected=True,
        last_chunk_at=100.0, last_loud_at=100.0, now=100.5, threshold=T,
    ) == TRANSPORT_DOWN


def test_reporter_emits_only_on_transition():
    r = CaptureStatusReporter(threshold=T)
    r.set_connected(True)
    r.note_chunk(now=100.0, dbfs=LOUD)
    assert r.poll(now=101.0) == ACTIVE
    assert r.poll(now=102.0) is None
    assert r.poll(now=111.0) == SILENT   # 11s since last loud
    assert r.poll(now=112.0) is None


def test_reporter_instant_recovery_from_silent():
    r = CaptureStatusReporter(threshold=T)
    r.set_connected(True)
    r.note_chunk(now=100.0, dbfs=LOUD)
    assert r.poll(now=111.0) == SILENT
    r.note_chunk(now=111.5, dbfs=LOUD)         # one loud chunk
    assert r.poll(now=111.6) == ACTIVE          # instant recovery


def test_reporter_mac_silence_despite_flowing_chunks():
    # Regression guard for the whole slice: on Mac, chunks keep arriving during
    # silence (last_chunk_at advances) but they are all quiet → still goes silent.
    r = CaptureStatusReporter(threshold=T)
    r.set_connected(True)
    r.note_chunk(now=100.0, dbfs=LOUD)
    assert r.poll(now=100.5) == ACTIVE
    for t in (101.0, 103.0, 105.0, 107.0, 109.0):
        r.note_chunk(now=t, dbfs=QUIET)        # quiet chunks STILL arrive (the Mac trap)
    assert r.poll(now=109.5) == ACTIVE          # 9.5s since last loud — still active
    r.note_chunk(now=110.5, dbfs=QUIET)
    assert r.poll(now=110.5) == SILENT          # 10.5s since last loud → silent


def test_reporter_transport_down_then_reconnect():
    r = CaptureStatusReporter(threshold=T)
    r.set_connected(True)
    r.note_chunk(now=100.0, dbfs=LOUD)
    assert r.poll(now=100.1) == ACTIVE
    r.set_connected(False)
    assert r.poll(now=100.2) == TRANSPORT_DOWN
    r.set_connected(True)
    assert r.poll(now=100.3) == ACTIVE


def test_reporter_starts_connecting():
    r = CaptureStatusReporter(threshold=T)
    assert r.poll(now=0.5) == CONNECTING
```

- [ ] **Step 2: Run the tests to verify they FAIL**

Run: `uv run pytest apps/client_sidecar/tests/test_capture_status.py -q`
Expected: FAIL — `compute_state()` got an unexpected/ missing keyword `last_loud_at`, and `note_chunk()` missing `dbfs`.

- [ ] **Step 3: Update `compute_state` and the reporter**

In `apps/client_sidecar/transport/capture_status.py`:

(a) Add the RMS default constant next to `SILENCE_THRESHOLD_S`:

```python
# Chunk RMS at/above this dBFS counts as "audio present". Matches
# config.audio.RMS_DBFS_THRESHOLD's default; main.py injects the configured value.
RMS_SILENCE_DBFS = -45.0
```

(b) Replace `compute_state` with the `last_loud_at` version:

```python
def compute_state(
    *,
    ws_connected: bool,
    ever_connected: bool,
    last_chunk_at: float | None,
    last_loud_at: float | None,
    now: float,
    threshold: float = SILENCE_THRESHOLD_S,
) -> str:
    """Pure: derive the capture state from connection + loudness facts.

    Silence is judged on the last *loud* chunk (RMS >= threshold), not mere chunk
    presence, so it fires on Mac (silent packets keep flowing) as well as Windows
    (no packets in silence). `last_chunk_at` only distinguishes "connecting"
    (no chunk yet) from a flowing-but-quiet stream.
    Priority: transport_down > connecting > silent > active.
    """
    if not ws_connected:
        return TRANSPORT_DOWN if ever_connected else CONNECTING
    if last_chunk_at is None:
        return CONNECTING
    if last_loud_at is None or now - last_loud_at >= threshold:
        return SILENT
    return ACTIVE
```

(c) Replace `CaptureStatusReporter` with the RMS-aware version:

```python
class CaptureStatusReporter:
    """Mutable capture facts + transition-coalescing. Updated by the WS loop,
    polled by the watchdog. Tracks the last *loud* chunk for silence."""

    def __init__(
        self,
        threshold: float = SILENCE_THRESHOLD_S,
        rms_threshold_dbfs: float = RMS_SILENCE_DBFS,
    ) -> None:
        self._threshold = threshold
        self._rms_threshold = rms_threshold_dbfs
        self._ws_connected = False
        self._ever_connected = False
        self._last_chunk_at: float | None = None
        self._last_loud_at: float | None = None
        self._emitted: str | None = None

    def set_connected(self, ok: bool) -> None:
        self._ws_connected = ok
        if ok:
            self._ever_connected = True

    def note_chunk(self, now: float, dbfs: float) -> None:
        self._last_chunk_at = now
        if dbfs >= self._rms_threshold:
            self._last_loud_at = now

    def poll(self, now: float) -> str | None:
        """Return the new state iff it changed since the last emit, else None."""
        state = compute_state(
            ws_connected=self._ws_connected,
            ever_connected=self._ever_connected,
            last_chunk_at=self._last_chunk_at,
            last_loud_at=self._last_loud_at,
            now=now,
            threshold=self._threshold,
        )
        if state == self._emitted:
            return None
        self._emitted = state
        return state
```

Leave `run_watchdog`, the constants `CONNECTING/ACTIVE/SILENT/TRANSPORT_DOWN`, `MARKER`, and `SILENCE_THRESHOLD_S` unchanged.

- [ ] **Step 4: Run the tests to verify they PASS**

Run: `uv run pytest apps/client_sidecar/tests/test_capture_status.py -q`
Expected: 12 tests pass (including `test_reporter_mac_silence_despite_flowing_chunks` and `test_silent_when_chunks_flow_but_never_loud`).

- [ ] **Step 5: Lint**

Run: `uvx ruff check apps/client_sidecar/transport/capture_status.py apps/client_sidecar/tests/test_capture_status.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add apps/client_sidecar/transport/capture_status.py apps/client_sidecar/tests/test_capture_status.py
git commit -m "feat(sidecar): judge capture silence on chunk loudness (RMS), unified cross-platform"
```

---

## Task 3: feed per-chunk dBFS from `stream_audio` [MAC]

Change the reporter call in the WS send loop to pass the chunk's loudness.

**Files:**
- Modify: `apps/client_sidecar/transport/audio_ws.py`

- [ ] **Step 1: Import `pcm16_dbfs`**

In `apps/client_sidecar/transport/audio_ws.py`, add to the imports (next to the existing `from apps.client_sidecar.config.audio import (...)`):

```python
from apps.client_sidecar.audio.rms import pcm16_dbfs
```

- [ ] **Step 2: Pass dBFS to `note_chunk`**

In `stream_audio`, replace the per-chunk reporter line:

```python
                        if reporter is not None:
                            reporter.note_chunk(time.monotonic())
```

with:

```python
                        if reporter is not None:
                            reporter.note_chunk(time.monotonic(), pcm16_dbfs(chunk))
```

(`chunk` is the `bytes` being sent; `time` is already imported.)

- [ ] **Step 3: Verify import + lint**

Run: `uv run python -c "import apps.client_sidecar.transport.audio_ws"`
Expected: exit 0, no output.

Run: `uvx ruff check apps/client_sidecar/transport/audio_ws.py`
Expected: no errors.

- [ ] **Step 4: Run the full sidecar suite (no regression)**

Run: `uv run pytest apps/client_sidecar/tests -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add apps/client_sidecar/transport/audio_ws.py
git commit -m "feat(sidecar): report per-chunk dBFS to the capture-status reporter"
```

---

## Task 4: inject the configured RMS threshold in `audio_main` [MAC]

Use the project's existing `RMS_DBFS_THRESHOLD` (env-backed, default -45, bundled app sets -60) so the silence threshold matches the rest of the sidecar.

**Files:**
- Modify: `apps/client_sidecar/main.py` (`audio_main`)

- [ ] **Step 1: Import the config threshold and pass it to the reporter**

In `apps/client_sidecar/main.py::audio_main`, add the import alongside the other in-function imports:

```python
    from apps.client_sidecar.config.audio import RMS_DBFS_THRESHOLD
```

Then change the reporter construction from:

```python
    reporter = CaptureStatusReporter()
```

to:

```python
    reporter = CaptureStatusReporter(rms_threshold_dbfs=RMS_DBFS_THRESHOLD)
```

- [ ] **Step 2: Verify import + lint**

Run: `uv run python -c "import apps.client_sidecar.main"`
Expected: exit 0.

Run: `uvx ruff check apps/client_sidecar/main.py`
Expected: no errors.

- [ ] **Step 3: Run the full sidecar suite**

Run: `uv run pytest apps/client_sidecar/tests -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add apps/client_sidecar/main.py
git commit -m "feat(sidecar): use configured RMS_DBFS_THRESHOLD for capture-status silence"
```

---

## Task 5: confirm desktop unchanged + live Mac smoke [MAC]

No desktop code changes; just confirm no regression, then observe the fix live on this Mac (the whole point: Mac silence now reaches 🟡).

**Files:** none (verification only).

- [ ] **Step 1: Desktop suite still green (no marker/state change)**

Run: `cd apps/desktop && pnpm vitest run && pnpm tsc --noEmit`
Expected: all vitest pass, tsc clean. (`captureStatus.ts` / chip untouched.)

- [ ] **Step 2: Live Mac smoke — silence now reaches the reporter's SILENT**

This proves the fix without the desktop. Run the helper into a short python harness that feeds chunks to the reporter and prints transitions, OR run the real sidecar against a server. The lightweight harness:

Run this from repo root:
```bash
uv run python - <<'PY'
import time
from apps.client_sidecar.transport.capture_status import CaptureStatusReporter
from apps.client_sidecar.audio.rms import pcm16_dbfs

r = CaptureStatusReporter(threshold=2.0)  # short threshold for a quick demo
r.set_connected(True)
loud = (16384).to_bytes(2, "little", signed=True) * 320   # ~ -6 dBFS
quiet = b"\x00\x00" * 320                                   # silence
# 1s of loud chunks
for _ in range(50):
    r.note_chunk(time.monotonic(), pcm16_dbfs(loud))
print("after loud:", r.poll(time.monotonic()))             # ACTIVE
# now silence: quiet chunks KEEP arriving (the Mac trap), advance time past threshold
t0 = time.monotonic()
state = None
while time.monotonic() - t0 < 3.0:
    r.note_chunk(time.monotonic(), pcm16_dbfs(quiet))
    s = r.poll(time.monotonic())
    if s:
        state = s
        print("transition:", s, "at +%.1fs" % (time.monotonic() - t0))
    time.sleep(0.02)
assert state == "silent", f"expected silent, got {state}"
print("OK: quiet-but-flowing chunks reached SILENT")
PY
```
Expected: prints `after loud: active`, then a `transition: silent at +~2.0s`, then `OK: ...`. This is the Mac behavior slice 1 could not produce (chunks flowing → never silent).

- [ ] **Step 3: (optional) full real pipeline**

If a server + session is available, run the real sidecar (`YESON_AUDIO_PROVIDER=native`, native helper) on this Mac, start a meeting, play audio (🟢 정상), then pause ~10 s and confirm the desktop chip turns 🟡 무음 — the behavior that was broken on Mac. No commit (verification only).

- [ ] **Step 4: Record the smoke result**

Note the harness output (and live result if run) in the spec §7 checklist line.

---

## Task 6: Windows regression E2E [WIN]

Confirm the unified RMS logic didn't break Windows (where silence = no packets).

- [ ] **Step 1: install a fresh Windows CI build including this slice; start a meeting.**

- [ ] **Step 2:** Play audio → chip **🟢 정상**. Pause ~10 s → **🟡 무음**. Resume → **🟢** within ~1 s. (Same 4-state behavior as slice 1; the change is internal.)

- [ ] **Step 3:** Stop server → **🔴 전송 끊김**; restart → recovers. Disable default output → **NativeCaptureBanner** (not chip).

- [ ] **Step 4:** Record pass/fail in the spec §7 checklist.

---

## Task 7: Docs sync [MAC]

**Files:**
- Modify: `docs/NATIVE_DESKTOP_HELPER_PLAN.md` (Phase 3 section)
- Modify: `docs/ROADMAP.md` (native track)

- [ ] **Step 1:** In `docs/NATIVE_DESKTOP_HELPER_PLAN.md` Phase 3, add a dated `> 📌 (2026-06-10)` callout: RMS-based silence (slice 2) — silence judged on chunk loudness (`last_loud_at`, threshold `RMS_DBFS_THRESHOLD`), unified across platforms so Mac's continuous-silent-chunks now reach 🟡; desktop unchanged; level meter still deferred. Reference the empirical Mac measurement (158KB/6s silence).

- [ ] **Step 2:** In `docs/ROADMAP.md`, add a 2026-06-10 line recording Phase 3 slice 2 status.

- [ ] **Step 3: Commit**

```bash
git add docs/NATIVE_DESKTOP_HELPER_PLAN.md docs/ROADMAP.md
git commit -m "docs(native-audio): record RMS-based silence detection (Phase 3 slice 2)"
```

---

## Self-Review (author checklist)

**Spec coverage:**
- §1 unify on RMS / silence-only / reuse RMS_DBFS_THRESHOLD → Tasks 2,4. ✓
- §2 two-timestamp mechanism (last_loud_at silent, last_chunk_at connecting), asymmetric hysteresis, priority → Task 2 (compute_state + tests incl. instant recovery, transport priority). ✓
- §3 RMS computed in stream_audio via rms.py, scalar to reporter, no smoothing → Tasks 1,3. ✓
- §4 files (capture_status, rms, audio_ws, main, tests; desktop unchanged) → Tasks 1–5. ✓
- §5 tests incl. the key "quiet chunks flowing → silent" → Task 2 (`test_reporter_mac_silence_despite_flowing_chunks`) + Task 5 live smoke. ✓
- §6 non-scope (no meter, no gating) honored; known Windows connecting-from-start edge unchanged (compute_state keeps `last_chunk_at is None → connecting`). ✓
- §7 deliverables → Tasks map 1:1. ✓

**Placeholder scan:** no TBD/TODO; full code in every step; commands have expected output. ✓

**Type/name consistency:** `compute_state(*, ws_connected, ever_connected, last_chunk_at, last_loud_at, now, threshold)`; `CaptureStatusReporter(threshold, rms_threshold_dbfs)` + `note_chunk(now, dbfs)`; `pcm16_dbfs(chunk: bytes) -> float`; `RMS_SILENCE_DBFS` (module default) vs `config.audio.RMS_DBFS_THRESHOLD` (injected) — names consistent across Tasks 1–4. ✓

**Mac-testability:** decider + reporter + pcm16_dbfs are pytest; the live harness (Task 5 Step 2) demonstrates the fix on Mac without Windows. Only the 4-state chip regression (Task 6) is Windows.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-06-10-rms-silence-detection.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task (Tasks 1–4 sidecar, then verify), review between. Tasks 1–5 + 7 run on macOS; Task 6 is Windows.

**2. Inline Execution** — execute Tasks 1–5 + 7 here (all pytest/vitest on macOS), hand Task 6 to Windows.

**Which approach?**
