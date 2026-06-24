// === ANCHOR: SESSION_REPORT_HTML_TEST_START ===
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock setupValues so apiBase() resolves to a predictable base URL.
// This must appear before any import that transitively calls loadValues().
vi.mock("../setup/setupValues", async (importOriginal) => {
  const real = await importOriginal<typeof import("../setup/setupValues")>();
  return {
    ...real,
    loadValues: () => ({
      ...real.DEFAULT_VALUES,
      serverWsBase: "ws://localhost:8000",
    }),
  };
});

import { fetchSessionReportHtml } from "./sessionApi";

const MOCK_HTML = "<!DOCTYPE html><html><body>Report</body></html>";

describe("fetchSessionReportHtml", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("calls the correct URL with Authorization header", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      text: async () => MOCK_HTML,
    });

    await fetchSessionReportHtml("session-abc", "tok-xyz");

    expect(globalThis.fetch).toHaveBeenCalledOnce();
    const [url, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/session-abc/report.html");
    expect((init.headers as Record<string, string>)["Authorization"]).toBe("Bearer tok-xyz");
  });

  it("returns the response text on success", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      text: async () => MOCK_HTML,
    });

    const result = await fetchSessionReportHtml("session-abc", "tok-xyz");
    expect(result).toBe(MOCK_HTML);
  });

  it("throws on non-OK response", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false,
      status: 409,
      text: async () => "",
    });

    await expect(fetchSessionReportHtml("session-abc", "tok-xyz")).rejects.toThrow("409");
  });

  it("URL-encodes the session ID", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      text: async () => MOCK_HTML,
    });

    await fetchSessionReportHtml("session id/with spaces", "tok");
    const [url] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toContain("session%20id%2Fwith%20spaces");
  });
});
// === ANCHOR: SESSION_REPORT_HTML_TEST_END ===
