// === ANCHOR: CAPTURESTATUS_TEST_START ===
import { describe, expect, it } from "vitest";

import type { AppLogEntry } from "../diagnostics/appLog";
import { latestCaptureStatus, parseCaptureStatus } from "./captureStatus";

// === ANCHOR: CAPTURESTATUS_TEST_ENTRY_START ===
function entry(id: number, message: string): AppLogEntry {
  return { id, ts: "", level: "info", source: "sidecar:stdout", message };
}
// === ANCHOR: CAPTURESTATUS_TEST_ENTRY_END ===

describe("parseCaptureStatus", () => {
  it("extracts a known state token", () => {
    expect(parseCaptureStatus("CAPTURE_STATUS active")).toBe("active");
    expect(parseCaptureStatus("CAPTURE_STATUS silent")).toBe("silent");
    expect(parseCaptureStatus("CAPTURE_STATUS transport_down")).toBe("transport_down");
    expect(parseCaptureStatus("CAPTURE_STATUS connecting")).toBe("connecting");
    expect(parseCaptureStatus("CAPTURE_STATUS no_audio")).toBe("no_audio");
  });

  it("returns null for non-markers and unknown states", () => {
    expect(parseCaptureStatus("hello world")).toBeNull();
    expect(parseCaptureStatus("CAPTURE_STATUS")).toBeNull();
    expect(parseCaptureStatus("CAPTURE_STATUS bogus")).toBeNull();
  });
});

describe("latestCaptureStatus", () => {
  it("returns the most recent capture status in the log", () => {
    const entries = [
      entry(1, "sidecar audio mode"),
      entry(2, "CAPTURE_STATUS connecting"),
      entry(3, "CAPTURE_STATUS active"),
      entry(4, "CAPTURE_STATUS silent"),
    ];
    expect(latestCaptureStatus(entries)).toBe("silent");
  });

  it("returns null when there is no capture status", () => {
    expect(latestCaptureStatus([entry(1, "nothing here")])).toBeNull();
  });
});
// === ANCHOR: CAPTURESTATUS_TEST_END ===
