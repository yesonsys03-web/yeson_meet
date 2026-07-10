// === ANCHOR: CAPTURE_API_TEST_START ===
import { afterEach, describe, expect, it, vi } from "vitest";
import { captureWsUrl, fetchCaptureToken } from "./captureApi";

describe("captureWsUrl", () => {
  it("captureWsUrl은 쿼리 없이 /ws/capture를 가리킨다", () => {
    const url = new URL(captureWsUrl({ protocol: "https:", host: "example.com" }));
    expect(url.pathname).toBe("/ws/capture");
    expect(url.search).toBe("");
    expect(url.protocol).toBe("wss:");
  });

  it("http(LAN dev)면 ws", () => {
    expect(captureWsUrl({ protocol: "http:", host: "localhost:5173" })).toMatch(/^ws:\/\/localhost:5173/);
  });
});

describe("fetchCaptureToken", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fetchCaptureToken은 세션 캡처 토큰을 발급받는다", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      expect(url).toContain("/api/v1/sessions/abc/capture-token");
      expect(init?.method).toBe("POST");
      expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer JWT");
      return {
        ok: true,
        json: async () => ({ token: "T", expires_at: "2026-07-10T00:00:00Z" }),
      } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    const token = await fetchCaptureToken("JWT", "abc");
    expect(token).toBe("T");
  });
});
// === ANCHOR: CAPTURE_API_TEST_END ===
