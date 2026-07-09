// === ANCHOR: CAPTURE_SUPPORT_TEST_START ===
import { describe, expect, it } from "vitest";
import { checkCaptureSupport, isChromiumLike } from "./captureSupport";

describe("checkCaptureSupport", () => {
  it("정상", () => {
    expect(checkCaptureSupport({ isSecureContext: true, hasGetDisplayMedia: true })).toEqual({ ok: true });
  });
  it("비보안 컨텍스트 우선 보고", () => {
    expect(checkCaptureSupport({ isSecureContext: false, hasGetDisplayMedia: false })).toEqual({ ok: false, reason: "insecure-context" });
  });
  it("getDisplayMedia 없음", () => {
    expect(checkCaptureSupport({ isSecureContext: true, hasGetDisplayMedia: false })).toEqual({ ok: false, reason: "no-display-media" });
  });
});

describe("isChromiumLike", () => {
  it("Chrome/Edge true", () => {
    expect(isChromiumLike("Mozilla/5.0 ... Chrome/126.0 Safari/537.36")).toBe(true);
    expect(isChromiumLike("Mozilla/5.0 ... Chrome/126.0 Safari/537.36 Edg/126.0")).toBe(true);
  });
  it("Firefox/Safari false", () => {
    expect(isChromiumLike("Mozilla/5.0 (Macintosh) Gecko/20100101 Firefox/128.0")).toBe(false);
    expect(isChromiumLike("Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 Version/17.5 Safari/605.1.15")).toBe(false);
  });
});
// === ANCHOR: CAPTURE_SUPPORT_TEST_END ===
