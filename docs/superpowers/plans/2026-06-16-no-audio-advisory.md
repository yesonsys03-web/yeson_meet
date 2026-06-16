# No-Audio Advisory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fifth capture state `no_audio` that escalates the existing 🟡 silent chip's label/tooltip to actionable output-device guidance after 30 s with no loud audio — covering both the "went quiet" case and the Windows "stuck on ⚪ connecting from the start" blind spot.

**Architecture:** Pure sidecar state-machine change (`capture_status.py`) adds the `no_audio` tier, keyed off a 30 s `STALE_THRESHOLD_S` measured from the last loud chunk or — if audio was never loud — from connect time (`connected_at`). The new marker `CAPTURE_STATUS no_audio` flows through the existing Rust app-log forwarder unchanged. The desktop adds `no_audio` to the `CaptureState` union and maps it in the chip (silent colors, escalated text) and the level meter (explicit empty bar). No Rust, helper, CI, or config change.

**Tech Stack:** Python (sidecar, `pytest`), TypeScript/React (desktop, `vitest` + `tsc`).

**Spec:** `docs/superpowers/specs/2026-06-16-no-audio-advisory-design.md`

---

## File Map

- `apps/client_sidecar/transport/capture_status.py` — add `NO_AUDIO`, `STALE_THRESHOLD_S`, `connected_at` param + reporter tracking, new tier in `compute_state`. All edits stay inside the `CAPTURE_STATUS` anchor (lines 1–149).
- `apps/client_sidecar/tests/test_capture_status.py` — new pure + reporter tests.
- `apps/desktop/src/console/captureStatus.ts` — `"no_audio"` in the union + `KNOWN`.
- `apps/desktop/src/console/captureStatus.test.ts` — parse assertion.
- `apps/desktop/src/console/CaptureStatusChip.tsx` — `no_audio` entry in `PRESENTATION` (silent colors) + `TITLE` (actionable).
- `apps/desktop/src/console/CaptureLevelMeter.tsx` — render `no_audio` as an explicit empty bar (like `silent`).

`LiveSubtitlePreview.tsx` consumes `CaptureState` but only passes it through (no switch) — no change.

---

## Task 1: Sidecar `no_audio` state machine

**Files:**
- Modify: `apps/client_sidecar/transport/capture_status.py`
- Test: `apps/client_sidecar/tests/test_capture_status.py`

- [ ] **Step 1: Write the failing tests**

Append to `apps/client_sidecar/tests/test_capture_status.py`. First add `NO_AUDIO` to the existing import block (it currently imports `ACTIVE, CONNECTING, SILENT, TRANSPORT_DOWN, CaptureStatusReporter, compute_state, level_marker, run_watchdog`) and add a `STALE` constant next to `T`:

```python
# add NO_AUDIO to the existing `from ... import (...)` block
# add this constant near T = 10.0 / LOUD / QUIET:
STALE = 30.0  # no-audio escalation threshold

def test_no_audio_when_silent_from_start_after_stale():
    # Windows zero-packets from the start: no chunk ever, connected 30s ago → no_audio
    assert compute_state(
        ws_connected=True, ever_connected=True,
        last_chunk_at=None, last_loud_at=None, now=130.0,
        threshold=T, connected_at=100.0, stale_threshold=STALE,
    ) == NO_AUDIO


def test_connecting_before_stale_with_no_chunk():
    # same case, only 29s in → still connecting (not yet escalated)
    assert compute_state(
        ws_connected=True, ever_connected=True,
        last_chunk_at=None, last_loud_at=None, now=129.0,
        threshold=T, connected_at=100.0, stale_threshold=STALE,
    ) == CONNECTING


def test_no_audio_after_stale_since_last_loud():
    # was active, then quiet past the stale threshold → no_audio (escalates from silent)
    assert compute_state(
        ws_connected=True, ever_connected=True,
        last_chunk_at=130.0, last_loud_at=100.0, now=130.0,
        threshold=T, connected_at=100.0, stale_threshold=STALE,
    ) == NO_AUDIO


def test_silent_between_thresholds_since_last_loud():
    # 10s..30s since last loud → still silent, not yet no_audio
    assert compute_state(
        ws_connected=True, ever_connected=True,
        last_chunk_at=120.0, last_loud_at=100.0, now=120.0,
        threshold=T, connected_at=100.0, stale_threshold=STALE,
    ) == SILENT


def test_transport_down_takes_priority_over_no_audio():
    assert compute_state(
        ws_connected=False, ever_connected=True,
        last_chunk_at=None, last_loud_at=None, now=200.0,
        threshold=T, connected_at=100.0, stale_threshold=STALE,
    ) == TRANSPORT_DOWN


def test_reporter_escalates_connecting_to_no_audio():
    r = CaptureStatusReporter(threshold=T, stale_threshold=STALE)
    r.set_connected(True)
    assert r.poll(now=100.0) == CONNECTING   # connected, no chunk yet; connected_at=100
    assert r.poll(now=129.0) is None          # still connecting (29s)
    assert r.poll(now=130.0) == NO_AUDIO       # 30s with no audio → escalate


def test_reporter_recovers_from_no_audio_on_loud_chunk():
    r = CaptureStatusReporter(threshold=T, stale_threshold=STALE)
    r.set_connected(True)
    assert r.poll(now=100.0) == CONNECTING
    assert r.poll(now=130.0) == NO_AUDIO
    r.note_chunk(now=131.0, dbfs=LOUD)
    assert r.poll(now=131.1) == ACTIVE          # instant recovery


def test_reporter_active_silent_no_audio_progression():
    r = CaptureStatusReporter(threshold=T, stale_threshold=STALE)
    r.set_connected(True)
    r.note_chunk(now=100.0, dbfs=LOUD)
    assert r.poll(now=100.5) == ACTIVE
    assert r.poll(now=111.0) == SILENT          # 11s since last loud
    assert r.poll(now=131.0) == NO_AUDIO         # 31s since last loud
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest apps/client_sidecar/tests/test_capture_status.py -q`
Expected: FAIL — `ImportError: cannot import name 'NO_AUDIO'` (the import line fails first).

- [ ] **Step 3: Add the constant and state token**

In `apps/client_sidecar/transport/capture_status.py`, add the constant under the existing `SILENCE_THRESHOLD_S` block (around line 18):

```python
# Long-silence escalation: no loud audio for this many seconds (well past the
# informational SILENCE_THRESHOLD_S) → escalate the silent chip to an actionable
# "check your output device" advisory. Still informational, auto-clears on sound.
STALE_THRESHOLD_S = 30.0
```

And add the state token next to the others (around line 31, after `TRANSPORT_DOWN = "transport_down"`):

```python
NO_AUDIO = "no_audio"
```

- [ ] **Step 4: Add the `no_audio` tier to `compute_state`**

Replace the entire `compute_state` function (currently lines 36–59) with:

```python
def compute_state(
    *,
    ws_connected: bool,
    ever_connected: bool,
    last_chunk_at: float | None,
    last_loud_at: float | None,
    now: float,
    threshold: float = SILENCE_THRESHOLD_S,
    connected_at: float | None = None,
    stale_threshold: float = STALE_THRESHOLD_S,
) -> str:
    """Pure: derive the capture state from connection + loudness facts.

    Silence is judged on the last *loud* chunk (RMS >= threshold), not mere chunk
    presence, so it fires on Mac (silent packets keep flowing) as well as Windows
    (no packets in silence). `last_chunk_at` only distinguishes "connecting"
    (no chunk yet) from a flowing-but-quiet stream.

    `no_audio` escalates silence after `stale_threshold`s with no loud audio,
    measured from the last loud chunk or — if audio was never loud — from
    `connected_at`. That second baseline covers the Windows "no packets from the
    start" case, where `last_chunk_at` stays None and the state would otherwise be
    stuck on "connecting" forever.

    Priority: transport_down > no_audio > connecting > silent > active.
    """
    if not ws_connected:
        return TRANSPORT_DOWN if ever_connected else CONNECTING
    reference = last_loud_at if last_loud_at is not None else connected_at
    if reference is not None and now - reference >= stale_threshold:
        return NO_AUDIO
    if last_chunk_at is None:
        return CONNECTING
    if last_loud_at is None or now - last_loud_at >= threshold:
        return SILENT
    return ACTIVE
```

- [ ] **Step 5: Track `connected_at` and pass the new args in the reporter**

In `CaptureStatusReporter.__init__` (currently lines 66–78), add the `stale_threshold` parameter and the `_connected_at` field. Replace the `__init__` signature and body with:

```python
    def __init__(
        self,
        threshold: float = SILENCE_THRESHOLD_S,
        rms_threshold_dbfs: float = RMS_SILENCE_DBFS,
        stale_threshold: float = STALE_THRESHOLD_S,
    ) -> None:
        self._threshold = threshold
        self._rms_threshold = rms_threshold_dbfs
        self._stale_threshold = stale_threshold
        self._ws_connected = False
        self._ever_connected = False
        self._connected_at: float | None = None
        self._last_chunk_at: float | None = None
        self._last_loud_at: float | None = None
        self._levels: deque[tuple[float, float]] = deque(maxlen=200)  # 200 @ ≤50 Hz ≈ 4 s; LEVEL_STALE_S=1.5 s needs ≤75 entries
        self._emitted: str | None = None
```

Then replace the `poll` method (currently lines 91–104) with one that lazily stamps `connected_at` on the first poll after connecting and forwards the new args:

```python
    def poll(self, now: float) -> str | None:
        """Return the new state iff it changed since the last emit, else None."""
        if self._ws_connected and self._connected_at is None:
            self._connected_at = now
        state = compute_state(
            ws_connected=self._ws_connected,
            ever_connected=self._ever_connected,
            last_chunk_at=self._last_chunk_at,
            last_loud_at=self._last_loud_at,
            now=now,
            threshold=self._threshold,
            connected_at=self._connected_at,
            stale_threshold=self._stale_threshold,
        )
        if state == self._emitted:
            return None
        self._emitted = state
        return state
```

(Leave `set_connected`, `note_chunk`, `level`, `level_marker`, and `run_watchdog` unchanged. `main.py` constructs the reporter with keyword args and the new `stale_threshold` defaults, so no `main.py` change is needed.)

- [ ] **Step 6: Run the capture-status tests to verify they pass**

Run: `uv run pytest apps/client_sidecar/tests/test_capture_status.py -q`
Expected: PASS — all new tests plus the pre-existing ones (the pre-existing `compute_state` calls omit `connected_at`/`stale_threshold`, which default to `None`/`30.0`, so `no_audio` never triggers in them).

- [ ] **Step 7: Run the full sidecar suite (no regression)**

Run: `uv run pytest apps/client_sidecar/tests -q`
Expected: PASS (all green; previous baseline was 63 passed).

- [ ] **Step 8: Commit**

```bash
git add apps/client_sidecar/transport/capture_status.py apps/client_sidecar/tests/test_capture_status.py
git commit -m "feat(sidecar): no_audio state escalates long silence to an advisory

Adds a 30s no_audio tier above the 10s silent state, measured from the
last loud chunk or connect time, so a long no-audio stretch (incl. the
Windows zero-packets-from-start case) is flagged. Pure + reporter tests.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Desktop `no_audio` chip + meter

**Files:**
- Modify: `apps/desktop/src/console/captureStatus.ts`
- Modify: `apps/desktop/src/console/CaptureStatusChip.tsx`
- Modify: `apps/desktop/src/console/CaptureLevelMeter.tsx`
- Test: `apps/desktop/src/console/captureStatus.test.ts`

- [ ] **Step 1: Write the failing parse test**

In `apps/desktop/src/console/captureStatus.test.ts`, add a line to the `"extracts a known state token"` test (after the `connecting` assertion, line 15):

```typescript
    expect(parseCaptureStatus("CAPTURE_STATUS no_audio")).toBe("no_audio");
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pnpm --filter @yeson-meet/desktop test`
Expected: FAIL — `parseCaptureStatus("CAPTURE_STATUS no_audio")` returns `null` (token not in `KNOWN`), so `toBe("no_audio")` fails.

- [ ] **Step 3: Add `no_audio` to the union and KNOWN**

In `apps/desktop/src/console/captureStatus.ts`, replace the `CaptureState` type (line 11) and `KNOWN` (line 13):

```typescript
export type CaptureState = "connecting" | "active" | "silent" | "no_audio" | "transport_down";

const KNOWN: readonly CaptureState[] = ["connecting", "active", "silent", "no_audio", "transport_down"];
```

- [ ] **Step 4: Run the parse test to verify it passes**

Run: `pnpm --filter @yeson-meet/desktop test`
Expected: PASS for `parseCaptureStatus`. (`tsc` will still fail — fixed next — because `CaptureStatusChip`'s `Record<CaptureState, ...>` maps are now missing the `no_audio` key.)

- [ ] **Step 5: Map `no_audio` in the chip**

In `apps/desktop/src/console/CaptureStatusChip.tsx`, add a `no_audio` entry to `PRESENTATION` (after the `silent` line, using the same 🟡 colors) and to `TITLE` (after the `silent` line):

```typescript
// in PRESENTATION:
  no_audio: { label: "오디오 없음", color: "#fde047", bg: "#2a2408", border: "#a16207" },
```

```typescript
// in TITLE:
  no_audio: "30초 넘게 오디오가 안 들어왔어요. 회의 오디오가 PC 기본 출력장치로 가는지 확인하세요 (헤드폰·외부 출력으로 빠지면 캡처가 조용합니다).",
```

- [ ] **Step 6: Render `no_audio` as an explicit empty bar in the meter**

In `apps/desktop/src/console/CaptureLevelMeter.tsx`, update the doc comment (lines 15–19) and the `filled` line (line 22) so `no_audio` renders an explicit empty bar like `silent`. Replace the comment block and the two lines starting at line 21:

```typescript
/**
 * Live loudness meter shown beside the capture-status chip.
 * Renders only in active/silent/no_audio states (the chip carries
 * connecting/transport_down); silent and no_audio always show an empty bar,
 * which also covers Windows silence (no chunks → dbfs null).
 */
```

```typescript
  if (state === "connecting" || state === "transport_down") return null;
  const filled = state === "silent" || state === "no_audio" ? 0 : dbfsToSegments(dbfs ?? -120);
```

- [ ] **Step 7: Run vitest and tsc to verify everything passes**

Run: `pnpm --filter @yeson-meet/desktop test`
Expected: PASS (previous baseline 19 passed, now with the added assertion).

Run: `pnpm --filter @yeson-meet/desktop exec tsc --noEmit`
Expected: PASS — no errors (the `Record<CaptureState, ...>` maps now include `no_audio`; `LiveSubtitlePreview` only passes the value through, so no exhaustiveness error there).

- [ ] **Step 8: Commit**

```bash
git add apps/desktop/src/console/captureStatus.ts apps/desktop/src/console/captureStatus.test.ts apps/desktop/src/console/CaptureStatusChip.tsx apps/desktop/src/console/CaptureLevelMeter.tsx
git commit -m "feat(desktop): no_audio capture chip escalates with output-device hint

Maps the new no_audio state to the silent chip's colors with an
actionable tooltip, and renders the level meter as an explicit empty
bar in no_audio.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Docs sync (ROADMAP)

**Files:**
- Modify: `docs/ROADMAP.md`

Per the project rule "update ROADMAP in the same step as finishing a slice" ([[feedback_docs_after_slice]]).

- [ ] **Step 1: Add a dated entry**

In `docs/ROADMAP.md`, add a bullet to the dated status log near the other 2026-06 native-audio entries (after the 2026-06-16 capture-UX-track entry):

```markdown
> - **(2026-06-16) no_audio advisory (코드 완료·Windows E2E 대기)**: 캡처 상태머신에 5번째 상태 `no_audio` 추가 — 30초 넘게 큰 소리가 없으면(처음부터든 도중이든) 기존 🟡 무음 칩을 "출력장치 확인" 행동 유도 툴팁으로 격상. 06-08 "⚪ 연결중에 갇혀 이유 모름"(Windows 무음=패킷0) 블라인드를 닫음. 에러 아님·소리 나면 즉시 복귀. `connected_at` 기준점으로 "한 번도 소리 없음"까지 커버. Rust/헬퍼/CI 무변경(CAPTURE_STATUS는 app-log 경유). 검증: 사이드카 pytest + 데스크톱 vitest/tsc. 설계·계획: `docs/superpowers/specs|plans/2026-06-16-no-audio-advisory*.md`.
```

- [ ] **Step 2: Commit**

```bash
git add docs/ROADMAP.md
git commit -m "docs(native-audio): log no_audio advisory in ROADMAP

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Verification Summary

After all tasks:
- `uv run pytest apps/client_sidecar/tests -q` — all green
- `pnpm --filter @yeson-meet/desktop test` — all green
- `pnpm --filter @yeson-meet/desktop exec tsc --noEmit` — no errors
- `cd apps/desktop/src-tauri && cargo check` — clean (no Rust change, keep the gate green)

**Deferred to Windows live E2E** (next CI build): start a meeting with audio routed to a non-default output device (e.g. headphones while default = speakers), confirm the chip escalates ⚪/🟡 → 🟡 "오디오 없음" within ~30 s with the output-device tooltip, then clears to 🟢 when audio returns to the default device. Confirm a normal speech pause (< 30 s) never escalates past the informational silent chip.
