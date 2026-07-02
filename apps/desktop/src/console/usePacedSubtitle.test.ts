// === ANCHOR: USE_PACED_SUBTITLE_TEST_START ===
import { describe, expect, it } from "vitest";

import type { UtteranceTranscribed } from "./types";
import { CATCHUP_FLOOR_MS, MIN_FLOOR_MS, displayMsFor } from "./usePacedSubtitle";

function utterance(text: string): UtteranceTranscribed {
  return {
    type: "utterance.transcribed",
    session_id: "s",
    occurred_at: "",
    seq: 1,
    speaker: null,
    text_en: "",
    text_ko: text,
    started_at: "",
    ended_at: "",
    is_final: true,
  };
}

describe("displayMsFor", () => {
  const short = utterance(""); // read time clamps to the comfortable read floor

  it("uses the comfortable read floor when caught up (backlog 0/1)", () => {
    // No backlog → full read time, never below the comfortable floor.
    expect(displayMsFor(short, 0)).toBeGreaterThanOrEqual(MIN_FLOOR_MS);
    // One queued item is still "caught up" — unchanged from backlog 0.
    expect(displayMsFor(short, 1)).toBe(displayMsFor(short, 0));
  });

  it("drains far below the comfortable floor under a real backlog", () => {
    // The regression: a bursty backlog used to hold every line >= MIN_FLOOR_MS
    // (1.2s), so the display lagged seconds behind. Under backlog it must now
    // drop toward the catch-up floor so the queue clears and latency falls.
    const behind = displayMsFor(short, 6);
    expect(behind).toBeLessThan(MIN_FLOOR_MS);
    expect(behind).toBe(CATCHUP_FLOOR_MS);
  });

  it("shrinks monotonically as the backlog grows", () => {
    const a = displayMsFor(short, 2);
    const b = displayMsFor(short, 4);
    const c = displayMsFor(short, 8);
    expect(a).toBeGreaterThanOrEqual(b);
    expect(b).toBeGreaterThanOrEqual(c);
  });

  it("never drops below the catch-up floor (stays readable)", () => {
    for (const backlog of [0, 1, 5, 20, 100]) {
      expect(displayMsFor(short, backlog)).toBeGreaterThanOrEqual(CATCHUP_FLOOR_MS);
    }
  });

  it("gives a long utterance more time, still bounded by the read ceiling", () => {
    const long = utterance("가".repeat(200));
    // Long text reads longer than short text when caught up.
    expect(displayMsFor(long, 0)).toBeGreaterThan(displayMsFor(short, 0));
  });

  function spokenUtterance(text: string, seconds: number): UtteranceTranscribed {
    return {
      ...utterance(text),
      started_at: "2026-07-01T00:00:00.000Z",
      ended_at: `2026-07-01T00:00:${String(seconds).padStart(2, "0")}.000Z`,
    };
  }

  it("paces a segment to its spoken duration, not just its text length", () => {
    // The fix: a ~10s segment carries ~10s of speech but modest text. The old
    // char-only heuristic capped it at 5.5s → it got rushed off screen before
    // the next segment arrived ("읽기 전에 바뀜"). Now it holds ~its spoken length.
    const seg = spokenUtterance("짧은 자막", 10);
    expect(displayMsFor(seg, 0)).toBe(10_000);
  });

  it("bounds spoken duration by the read ceiling", () => {
    const seg = spokenUtterance("x", 30);
    expect(displayMsFor(seg, 0)).toBe(13_000); // MAX_READ_MS
  });

  it("releases a spoken-paced line after read time once the next line waits", () => {
    // "다음 자막이 살짝 늦게 나온다"(2026-07-02): 대기 중인 다음 자막이 있으면
    // 발화길이(10s)만큼 붙잡지 않고 글자수 기반 읽기시간만 보장하고 넘어간다.
    const seg = spokenUtterance("짧은 자막", 10);
    expect(displayMsFor(seg, 1)).toBe(1_400 + "짧은 자막".length * 70);
    expect(displayMsFor(seg, 1)).toBeLessThan(displayMsFor(seg, 0));
  });

  it("falls back to text-based read time when timestamps are missing/unparseable", () => {
    // Guards the empty-string timestamps in fixtures and any bad server payload.
    expect(displayMsFor(short, 0)).toBe(1_400); // BASE_READ_MS, unchanged
  });
});
// === ANCHOR: USE_PACED_SUBTITLE_TEST_END ===
