import { describe, expect, it } from "vitest";
import { activeSegmentIndex, overlayStyleFor } from "./videoReviewLogic";

const segs = [
  { start_ms: 0, end_ms: 1000 },
  { start_ms: 1500, end_ms: 3000 },
];

describe("activeSegmentIndex", () => {
  it("returns segment covering current time", () => {
    expect(activeSegmentIndex(segs, 500)).toBe(0);
    expect(activeSegmentIndex(segs, 2000)).toBe(1);
  });
  it("returns -1 in gaps and past end", () => {
    expect(activeSegmentIndex(segs, 1200)).toBe(-1);
    expect(activeSegmentIndex(segs, 99999)).toBe(-1);
  });
});

describe("overlayStyleFor", () => {
  it("bottom position anchors bottom with marginV", () => {
    const s = overlayStyleFor({ position: "bottom", margin_v: 40, font_size: 18 });
    expect(s.bottom).toBe(40);
    expect(s.top).toBeUndefined();
    expect(s.fontSize).toBe(18);
  });
  it("top position anchors top", () => {
    const s = overlayStyleFor({ position: "top", margin_v: 20, font_size: 24 });
    expect(s.top).toBe(20);
    expect(s.bottom).toBeUndefined();
  });
});
