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
});
// === ANCHOR: USE_PACED_SUBTITLE_TEST_END ===
