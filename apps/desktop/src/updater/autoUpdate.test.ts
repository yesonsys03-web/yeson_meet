// === ANCHOR: AUTO_UPDATE_TEST_START ===
import { describe, expect, it } from "vitest";

import { initialUpdateStatus, isMacOS, updateReducer, type UpdateStatus } from "./autoUpdate";

describe("updateReducer", () => {
  it("moves idle → checking on check-start", () => {
    expect(updateReducer(initialUpdateStatus, { type: "check-start" })).toEqual({ kind: "checking" });
  });

  it("keeps a ready banner across a later background check", () => {
    const ready: UpdateStatus = { kind: "ready", version: "1.2.0" };
    expect(updateReducer(ready, { type: "check-start" })).toEqual(ready);
    expect(updateReducer(ready, { type: "up-to-date" })).toEqual(ready);
  });

  it("tracks download progress", () => {
    const started = updateReducer({ kind: "checking" }, { type: "download-start", version: "1.2.0" });
    expect(started).toEqual({ kind: "downloading", version: "1.2.0", percent: null });
    expect(updateReducer(started, { type: "download-progress", percent: 42 })).toEqual({
      kind: "downloading",
      version: "1.2.0",
      percent: 42,
    });
  });

  it("ignores progress when not downloading", () => {
    expect(updateReducer({ kind: "idle" }, { type: "download-progress", percent: 10 })).toEqual({ kind: "idle" });
  });

  it("reaches ready on download-done", () => {
    const downloading: UpdateStatus = { kind: "downloading", version: "1.2.0", percent: 100 };
    expect(updateReducer(downloading, { type: "download-done", version: "1.2.0" })).toEqual({
      kind: "ready",
      version: "1.2.0",
    });
  });

  it("shows error but never over a ready banner", () => {
    expect(updateReducer({ kind: "checking" }, { type: "fail", message: "network" })).toEqual({
      kind: "error",
      message: "network",
    });
    const ready: UpdateStatus = { kind: "ready", version: "1.2.0" };
    expect(updateReducer(ready, { type: "fail", message: "network" })).toEqual(ready);
  });
});

describe("isMacOS", () => {
  it("detects mac platforms", () => {
    expect(isMacOS("MacIntel")).toBe(true);
    expect(isMacOS("Win32")).toBe(false);
    expect(isMacOS("Linux x86_64")).toBe(false);
  });
});
// === ANCHOR: AUTO_UPDATE_TEST_END ===
