// === ANCHOR: MEETINGTITLE_TEST_START ===
import { describe, expect, it } from "vitest";

import { formatMeetingTitle } from "./meetingTitle";

describe("formatMeetingTitle", () => {
  it("formats date and time with zero-padding", () => {
    // Month is 0-based: 5 === June.
    expect(formatMeetingTitle(new Date(2026, 5, 17, 9, 5))).toBe("2026-06-17 09:05 회의");
  });

  it("pads two-digit hours and minutes", () => {
    expect(formatMeetingTitle(new Date(2026, 11, 1, 14, 30))).toBe("2026-12-01 14:30 회의");
  });
});
// === ANCHOR: MEETINGTITLE_TEST_END ===
