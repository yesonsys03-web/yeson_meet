// === ANCHOR: SESSION_API_TEST_START ===
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock setupValues so apiBase() resolves to a predictable base URL
// (ws://127.0.0.1:8000 → httpBaseFromWs → http://127.0.0.1:8000).
// STORAGE_KEY in setupValues.ts is "yeson-meet-desktop-setup".
vi.mock("../setup/setupValues", async (importOriginal) => {
  const real = await importOriginal<typeof import("../setup/setupValues")>();
  return {
    ...real,
    loadValues: () => ({
      ...real.DEFAULT_VALUES,
      serverWsBase: "ws://127.0.0.1:8000",
    }),
  };
});

vi.mock("../diagnostics/appLog", () => ({
  appLogger: {
    latency: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    debug: vi.fn(),
  },
}));

import { selfEnrollDevice } from "./sessionApi";

describe("selfEnrollDevice", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("POSTs to self-enroll with bearer and returns the api_key", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      new Response(JSON.stringify({ id: 1, name: "client-x", api_key: "KEY123" }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const key = await selfEnrollDevice("op-token", "client-x");

    expect(key).toBe("KEY123");
    const [url, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    expect(String(url)).toContain("/api/v1/devices/self-enroll");
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({ Authorization: "Bearer op-token" });
  });
});
// === ANCHOR: SESSION_API_TEST_END ===
