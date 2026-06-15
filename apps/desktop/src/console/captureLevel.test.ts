import { describe, expect, it } from "vitest";

import { SEGMENTS, dbfsToSegments, segmentColorRole, segmentEdgeDbfs } from "./captureLevel";

describe("dbfsToSegments", () => {
  it("is empty at or below the floor", () => {
    expect(dbfsToSegments(-54)).toBe(0);
    expect(dbfsToSegments(-80)).toBe(0);
  });

  it("is full at or above the ceiling", () => {
    expect(dbfsToSegments(-6)).toBe(SEGMENTS);
    expect(dbfsToSegments(0)).toBe(SEGMENTS);
  });

  it("maps the mid-range proportionally", () => {
    // (-30 - -54) / (-6 - -54) = 24/48 = 0.5 → 3 of 6
    expect(dbfsToSegments(-30)).toBe(3);
  });

  it("treats non-finite input as empty", () => {
    expect(dbfsToSegments(Number.NEGATIVE_INFINITY)).toBe(0);
    expect(dbfsToSegments(Number.NaN)).toBe(0);
  });
});

describe("segmentEdgeDbfs", () => {
  it("top segment edge is the ceiling", () => {
    expect(segmentEdgeDbfs(SEGMENTS - 1)).toBeCloseTo(-6);
  });

  it("first segment edge is one step above the floor", () => {
    // -54 + (1/6)*48 = -46
    expect(segmentEdgeDbfs(0)).toBeCloseTo(-46);
  });
});

describe("segmentColorRole", () => {
  it("grades the top segment red, second-from-top yellow, the rest green", () => {
    expect(segmentColorRole(5)).toBe("red");    // edge -6
    expect(segmentColorRole(4)).toBe("yellow");  // edge -14
    for (const i of [0, 1, 2, 3]) expect(segmentColorRole(i)).toBe("green");
  });
});
