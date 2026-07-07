import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { deleteVideoJob, getStorage, listVideoJobs } from "./videoJobsAdmin";

describe("videoJobsAdmin", () => {
  const originalFetch = globalThis.fetch;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("listVideoJobs GETs the loopback list with with_sizes", async () => {
    fetchMock.mockResolvedValue({
      ok: true, status: 200,
      json: async () => ({ items: [{ job_id: "j1", size_bytes: 10 }] }),
    });
    const out = await listVideoJobs(8000);
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toBe("http://127.0.0.1:8000/api/v1/video-jobs?with_sizes=true");
    expect(out).toHaveLength(1);
    expect(out[0]!.job_id).toBe("j1");
    expect(out[0]!.size_bytes).toBe(10);
  });

  it("getStorage GETs the loopback storage endpoint", async () => {
    fetchMock.mockResolvedValue({
      ok: true, status: 200,
      json: async () => ({ total_bytes: 5, job_count: 1, keep: 10 }),
    });
    const out = await getStorage(8000);
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toBe("http://127.0.0.1:8000/api/v1/video-jobs/storage");
    expect(out.keep).toBe(10);
  });

  it("deleteVideoJob DELETEs by id on the loopback", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 204, json: async () => ({}) });
    await deleteVideoJob(8000, "j1");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toBe("http://127.0.0.1:8000/api/v1/video-jobs/j1");
    expect(init.method).toBe("DELETE");
  });

  it("throws on non-ok list responses", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 500, json: async () => ({}) });
    await expect(listVideoJobs(8000)).rejects.toThrow(/500/);
  });
});
