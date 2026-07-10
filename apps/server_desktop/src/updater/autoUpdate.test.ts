// === ANCHOR: AUTO_UPDATE_TEST_START ===
import { describe, expect, it } from "vitest";

import { initialUpdateStatus, updateReducer, type UpdateStatus } from "./autoUpdate";

describe("updateReducer (server)", () => {
  it("moves idle → checking on check-start", () => {
    expect(updateReducer(initialUpdateStatus, { type: "check-start" })).toEqual({ kind: "checking" });
  });

  it("keeps a ready banner across a later background check", () => {
    const ready: UpdateStatus = { kind: "ready", version: "1.2.0" };
    expect(updateReducer(ready, { type: "check-start" })).toEqual(ready);
    expect(updateReducer(ready, { type: "up-to-date" })).toEqual(ready);
    expect(updateReducer({ kind: "checking" }, { type: "up-to-date" })).toEqual({ kind: "up-to-date" });
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

  it("moves ready → applying on apply-start", () => {
    const ready: UpdateStatus = { kind: "ready", version: "1.2.0" };
    expect(updateReducer(ready, { type: "apply-start", version: "1.2.0" })).toEqual({
      kind: "applying",
      version: "1.2.0",
    });
  });

  it("ignores background check/up-to-date while applying", () => {
    const applying: UpdateStatus = { kind: "applying", version: "1.2.0" };
    expect(updateReducer(applying, { type: "check-start" })).toEqual(applying);
    expect(updateReducer(applying, { type: "up-to-date" })).toEqual(applying);
    expect(updateReducer(applying, { type: "fail", message: "network" })).toEqual(applying);
  });

  it("surfaces a distinct apply-error on install/relaunch failure", () => {
    const applying: UpdateStatus = { kind: "applying", version: "1.2.0" };
    expect(updateReducer(applying, { type: "apply-fail", version: "1.2.0", message: "locked" })).toEqual({
      kind: "apply-error",
      version: "1.2.0",
      message: "locked",
    });
  });

  it("keeps an apply-error banner across a later background/manual check", () => {
    const applyError: UpdateStatus = { kind: "apply-error", version: "1.2.0", message: "locked" };
    expect(updateReducer(applyError, { type: "check-start" })).toEqual(applyError);
    expect(updateReducer(applyError, { type: "up-to-date" })).toEqual(applyError);
  });

  it("retries from apply-error → applying on apply-start", () => {
    const applyError: UpdateStatus = { kind: "apply-error", version: "1.2.0", message: "locked" };
    expect(updateReducer(applyError, { type: "apply-start", version: "1.2.0" })).toEqual({
      kind: "applying",
      version: "1.2.0",
    });
  });
});
// === ANCHOR: AUTO_UPDATE_TEST_END ===
