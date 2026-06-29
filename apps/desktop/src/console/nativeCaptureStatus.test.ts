// === ANCHOR: NATIVECAPTURESTATUS_TEST_START ===
import { describe, expect, it } from "vitest";

import type { AppLogEntry } from "../diagnostics/appLog";
import { latestNativeStatus, parseNativeStatusReason } from "./nativeCaptureStatus";

// === ANCHOR: NATIVECAPTURESTATUS_TEST_ENTRY_START ===
function entry(id: number, message: string): AppLogEntry {
  return { id, ts: "", level: "info", source: "sidecar:stdout", message };
}
// === ANCHOR: NATIVECAPTURESTATUS_TEST_ENTRY_END ===

describe("parseNativeStatusReason", () => {
  it("extracts the reason token from a NATIVE_STATUS line", () => {
    expect(parseNativeStatusReason("NATIVE_STATUS permission_denied")).toBe("permission_denied");
  });

  it("ignores trailing content after the reason", () => {
    expect(parseNativeStatusReason("NATIVE_STATUS start_failed detail=x")).toBe("start_failed");
  });

  it("returns null for unrelated log lines", () => {
    expect(parseNativeStatusReason("sidecar audio mode → source=NativePipeSource")).toBeNull();
    expect(parseNativeStatusReason("NATIVE_STATUS")).toBeNull();
  });
});

describe("latestNativeStatus", () => {
  it("returns the most recent native failure", () => {
    const entries = [
      entry(1, "starting"),
      entry(2, "NATIVE_STATUS permission_denied"),
      entry(3, "some other log"),
      entry(4, "NATIVE_STATUS start_failed"),
    ];
    expect(latestNativeStatus(entries)).toEqual({ reason: "start_failed", id: 4 });
  });

  it("returns null when there is no native status", () => {
    expect(latestNativeStatus([entry(1, "hello"), entry(2, "world")])).toBeNull();
  });
});
// === ANCHOR: NATIVECAPTURESTATUS_TEST_END ===
