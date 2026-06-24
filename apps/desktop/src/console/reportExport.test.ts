// === ANCHOR: REPORT_EXPORT_TEST_START ===
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Mock setupValues so apiBase() resolves to a predictable base URL.
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

// Mock appLog to suppress output in tests.
vi.mock("../diagnostics/appLog", () => ({
  appLogger: {
    latency: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  },
}));

// Mock Tauri plugins — must be registered before importing reportExport.
vi.mock("@tauri-apps/plugin-dialog", () => ({
  open: vi.fn(),
}));
vi.mock("@tauri-apps/plugin-fs", () => ({
  writeFile: vi.fn(),
}));
vi.mock("@tauri-apps/plugin-opener", () => ({
  openPath: vi.fn(),
}));

import { fetchSessionReportBytes, fetchSessionSummaryBytes } from "./sessionApi";
import { exportReports, exportSummary } from "./reportExport";

// --- fetchSessionReportBytes unit tests ---

describe("fetchSessionReportBytes", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("uses /report for md format", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      arrayBuffer: async () => new ArrayBuffer(4),
    });

    const result = await fetchSessionReportBytes("sess-1", "tok", "md");

    expect(result.ok).toBe(true);
    expect(result.status).toBe(200);
    const [url] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toMatch(/\/sess-1\/report$/);
  });

  it("uses /report.html for html format", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      arrayBuffer: async () => new ArrayBuffer(8),
    });

    await fetchSessionReportBytes("sess-1", "tok", "html");

    const [url] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toMatch(/\/sess-1\/report\.html$/);
  });

  it("uses /report.docx for docx format", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      arrayBuffer: async () => new ArrayBuffer(8),
    });

    await fetchSessionReportBytes("sess-1", "tok", "docx");

    const [url] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toMatch(/\/sess-1\/report\.docx$/);
  });

  it("uses /report.pdf for pdf format", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      arrayBuffer: async () => new ArrayBuffer(8),
    });

    await fetchSessionReportBytes("sess-1", "tok", "pdf");

    const [url] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toMatch(/\/sess-1\/report\.pdf$/);
  });

  it("returns ok:false with status on non-OK response (e.g. 503 for pdf)", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false,
      status: 503,
      arrayBuffer: async () => new ArrayBuffer(0),
    });

    const result = await fetchSessionReportBytes("sess-1", "tok", "pdf");
    expect(result.ok).toBe(false);
    expect(result.status).toBe(503);
    expect(result.data).toBeUndefined();
  });

  it("returns ok:false with status 0 on network error", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("network down"));

    const result = await fetchSessionReportBytes("sess-1", "tok", "md");
    expect(result.ok).toBe(false);
    expect(result.status).toBe(0);
  });

  it("URL-encodes the session ID", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      arrayBuffer: async () => new ArrayBuffer(0),
    });

    await fetchSessionReportBytes("id with/spaces", "tok", "md");
    const [url] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toContain("id%20with%2Fspaces");
  });

  it("sends Authorization header", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      arrayBuffer: async () => new ArrayBuffer(0),
    });

    await fetchSessionReportBytes("sess-1", "my-token", "md");
    const [, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)["Authorization"]).toBe("Bearer my-token");
  });

  it("returns Uint8Array data on success", async () => {
    const bytes = new Uint8Array([1, 2, 3, 4]);
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      arrayBuffer: async () => bytes.buffer,
    });

    const result = await fetchSessionReportBytes("sess-1", "tok", "md");
    expect(result.data).toBeInstanceOf(Uint8Array);
    expect(Array.from(result.data!)).toEqual([1, 2, 3, 4]);
  });
});

// --- exportReports unit tests (Tauri runtime mocked) ---

describe("exportReports (Tauri runtime)", () => {
  const originalFetch = globalThis.fetch;

  type TauriGlobal = typeof globalThis & { __TAURI_INTERNALS__?: unknown };

  // Simulate Tauri runtime presence.
  beforeEach(() => {
    (globalThis as TauriGlobal).__TAURI_INTERNALS__ = {};
    globalThis.fetch = vi.fn();
  });

  afterEach(() => {
    delete (globalThis as TauriGlobal).__TAURI_INTERNALS__;
    globalThis.fetch = originalFetch;
    vi.clearAllMocks();
  });

  it("calls writeFile for each successfully fetched format", async () => {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const { writeFile } = await import("@tauri-apps/plugin-fs");

    (open as ReturnType<typeof vi.fn>).mockResolvedValue("/tmp/exports");
    (writeFile as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      arrayBuffer: async () => new ArrayBuffer(4),
    });

    const result = await exportReports("sess-1", "tok", ["md", "html"], { openFolder: false });

    expect(writeFile).toHaveBeenCalledTimes(2);
    expect(result.saved).toContain("report.md");
    expect(result.saved).toContain("report.html");
    expect(result.skipped).toHaveLength(0);
  });

  it("skips formats that return non-OK HTTP status", async () => {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const { writeFile } = await import("@tauri-apps/plugin-fs");

    (open as ReturnType<typeof vi.fn>).mockResolvedValue("/tmp/exports");
    (writeFile as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if ((url as string).endsWith(".pdf")) {
        return Promise.resolve({ ok: false, status: 503, arrayBuffer: async () => new ArrayBuffer(0) });
      }
      return Promise.resolve({ ok: true, status: 200, arrayBuffer: async () => new ArrayBuffer(4) });
    });

    const result = await exportReports("sess-1", "tok", ["md", "pdf"], { openFolder: false });

    expect(result.saved).toContain("report.md");
    expect(result.skipped).toHaveLength(1);
    expect(result.skipped[0]!.fmt).toBe("pdf");
    expect(result.skipped[0]!.reason).toContain("503");
  });

  it("calls openPath when openFolder is true and files were saved", async () => {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const { writeFile } = await import("@tauri-apps/plugin-fs");
    const { openPath } = await import("@tauri-apps/plugin-opener");

    (open as ReturnType<typeof vi.fn>).mockResolvedValue("/tmp/exports");
    (writeFile as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);
    (openPath as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      arrayBuffer: async () => new ArrayBuffer(4),
    });

    await exportReports("sess-1", "tok", ["md"], { openFolder: true });

    expect(openPath).toHaveBeenCalledOnce();
  });

  it("does not call openPath when openFolder is false", async () => {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const { writeFile } = await import("@tauri-apps/plugin-fs");
    const { openPath } = await import("@tauri-apps/plugin-opener");

    (open as ReturnType<typeof vi.fn>).mockResolvedValue("/tmp/exports");
    (writeFile as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      arrayBuffer: async () => new ArrayBuffer(4),
    });

    await exportReports("sess-1", "tok", ["md"], { openFolder: false });

    expect(openPath).not.toHaveBeenCalled();
  });

  it("returns { saved: [], skipped: [], dir: null } when user cancels dialog", async () => {
    const { open } = await import("@tauri-apps/plugin-dialog");
    (open as ReturnType<typeof vi.fn>).mockResolvedValue(null);

    const result = await exportReports("sess-1", "tok", ["md"]);
    expect(result.saved).toHaveLength(0);
    expect(result.dir).toBeNull();
  });

  it("uses defaultDir option to skip dialog", async () => {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const { writeFile } = await import("@tauri-apps/plugin-fs");

    (writeFile as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      arrayBuffer: async () => new ArrayBuffer(4),
    });

    const result = await exportReports("sess-1", "tok", ["md"], { defaultDir: "/preset/dir", openFolder: false });

    expect(open).not.toHaveBeenCalled();
    expect(result.dir).toBe("/preset/dir");
    expect(result.saved).toContain("report.md");
  });
});

// --- fetchSessionSummaryBytes unit tests ---

describe("fetchSessionSummaryBytes", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("uses /report.summary for md format", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      arrayBuffer: async () => new ArrayBuffer(4),
    });

    await fetchSessionSummaryBytes("sess-1", "tok", "md");

    const [url] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toMatch(/\/sess-1\/report\.summary$/);
  });

  it("uses /report.summary.html for html format", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      arrayBuffer: async () => new ArrayBuffer(8),
    });

    await fetchSessionSummaryBytes("sess-1", "tok", "html");

    const [url] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toMatch(/\/sess-1\/report\.summary\.html$/);
  });

  it("uses /report.summary.docx for docx format", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      arrayBuffer: async () => new ArrayBuffer(8),
    });

    await fetchSessionSummaryBytes("sess-1", "tok", "docx");

    const [url] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toMatch(/\/sess-1\/report\.summary\.docx$/);
  });

  it("returns ok:false with status on 404 (summary not yet generated)", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false,
      status: 404,
      arrayBuffer: async () => new ArrayBuffer(0),
    });

    const result = await fetchSessionSummaryBytes("sess-1", "tok", "md");
    expect(result.ok).toBe(false);
    expect(result.status).toBe(404);
  });
});

// --- exportSummary unit tests (Tauri runtime mocked) ---

describe("exportSummary (Tauri runtime)", () => {
  const originalFetch = globalThis.fetch;

  type TauriGlobal = typeof globalThis & { __TAURI_INTERNALS__?: unknown };

  beforeEach(() => {
    (globalThis as TauriGlobal).__TAURI_INTERNALS__ = {};
    globalThis.fetch = vi.fn();
  });

  afterEach(() => {
    delete (globalThis as TauriGlobal).__TAURI_INTERNALS__;
    globalThis.fetch = originalFetch;
    vi.clearAllMocks();
  });

  it("writes summary.{fmt} for each selected format", async () => {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const { writeFile } = await import("@tauri-apps/plugin-fs");

    (open as ReturnType<typeof vi.fn>).mockResolvedValue("/tmp/exports");
    (writeFile as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      arrayBuffer: async () => new ArrayBuffer(4),
    });

    const result = await exportSummary("sess-1", "tok", ["md", "html"], { openFolder: false });

    expect(writeFile).toHaveBeenCalledTimes(2);
    expect(result.saved).toContain("summary.md");
    expect(result.saved).toContain("summary.html");
    expect(result.skipped).toHaveLength(0);
  });

  it("skips 404 formats with '요약 아직 생성 안 됨'", async () => {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const { writeFile } = await import("@tauri-apps/plugin-fs");

    (open as ReturnType<typeof vi.fn>).mockResolvedValue("/tmp/exports");
    (writeFile as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
      if ((url as string).endsWith(".docx")) {
        return Promise.resolve({ ok: false, status: 404, arrayBuffer: async () => new ArrayBuffer(0) });
      }
      return Promise.resolve({ ok: true, status: 200, arrayBuffer: async () => new ArrayBuffer(4) });
    });

    const result = await exportSummary("sess-1", "tok", ["md", "docx"], { openFolder: false });

    expect(result.saved).toContain("summary.md");
    expect(result.skipped).toHaveLength(1);
    expect(result.skipped[0]!.fmt).toBe("docx");
    expect(result.skipped[0]!.reason).toContain("요약 아직 생성 안 됨");
  });

  it("returns { saved: [], dir: null } when user cancels dialog", async () => {
    const { open } = await import("@tauri-apps/plugin-dialog");
    (open as ReturnType<typeof vi.fn>).mockResolvedValue(null);

    const result = await exportSummary("sess-1", "tok", ["md"]);
    expect(result.saved).toHaveLength(0);
    expect(result.dir).toBeNull();
  });
});
// === ANCHOR: REPORT_EXPORT_TEST_END ===
