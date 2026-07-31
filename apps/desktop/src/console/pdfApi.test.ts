import { describe, expect, it, vi } from "vitest";

// apiBase() → loadValues() throws on the real default ("" serverWsBase) unless
// a valid ws:// value is mocked in — same pattern as videoApi.test.ts.
vi.mock("../setup/setupValues", async (importOriginal) => {
  const real = await importOriginal<typeof import("../setup/setupValues")>();
  return {
    ...real,
    loadValues: () => ({ ...real.DEFAULT_VALUES, serverWsBase: "ws://localhost:8000" }),
  };
});

import { isActivePdfStatus, pdfPageUrl } from "./pdfApi";

describe("pdfApi helpers", () => {
  it("active statuses are the four in-flight ones", () => {
    for (const s of ["queued", "extracting", "translating", "overlaying"]) {
      expect(isActivePdfStatus(s)).toBe(true);
    }
    for (const s of ["done", "error", "cancelled"]) {
      expect(isActivePdfStatus(s)).toBe(false);
    }
  });

  it("pdfPageUrl encodes variant", () => {
    expect(pdfPageUrl("abc", 3, "translated")).toContain(
      "/api/v1/pdf-jobs/abc/page/3?variant=translated");
  });
});
