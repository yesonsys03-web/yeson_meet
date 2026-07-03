import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../setup/setupValues", async (importOriginal) => {
  const real = await importOriginal<typeof import("../setup/setupValues")>();
  return {
    ...real,
    loadValues: () => ({ ...real.DEFAULT_VALUES, serverWsBase: "ws://localhost:8000" }),
  };
});
vi.mock("../diagnostics/appLog", () => ({
  appLogger: { latency: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

import {
  burnVideoJob, createYoutubeJob, listVideoModels, uploadVideoJob, videoMediaUrl,
} from "./videoApi";

describe("videoApi", () => {
  const originalFetch = globalThis.fetch;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue({
      ok: true, status: 200, json: async () => ({}),
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("listVideoModels GETs /api/v1/video-models with bearer", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => ({ models: [] }) });
    await listVideoModels("tok");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toBe("http://localhost:8000/api/v1/video-models");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok");
  });

  it("createYoutubeJob POSTs JSON body", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 201, json: async () => ({ job_id: "j1" }) });
    const out = await createYoutubeJob({ url: "https://youtu.be/x", whisperModel: "small" }, "tok");
    expect(out.job_id).toBe("j1");
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      youtube_url: "https://youtu.be/x", whisper_model: "small", title: null,
    });
  });

  it("uploadVideoJob sends multipart FormData", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 201, json: async () => ({ job_id: "j2" }) });
    const file = new File([new Uint8Array([1, 2, 3])], "clip.mp4", { type: "video/mp4" });
    await uploadVideoJob(file, "small", "제목", "tok");
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const form = init.body as FormData;
    expect(form.get("whisper_model")).toBe("small");
    expect((form.get("file") as File).name).toBe("clip.mp4");
  });

  it("burnVideoJob POSTs style body", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 202, json: async () => ({ status: "burning" }) });
    await burnVideoJob("j1", { position: "top", margin_v: 20, font_size: 24 }, "tok");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toContain("/api/v1/video-jobs/j1/burn");
    expect(JSON.parse(init.body as string).position).toBe("top");
  });

  it("videoMediaUrl builds capability URL without token", () => {
    expect(videoMediaUrl("j1")).toBe("http://localhost:8000/api/v1/video-jobs/j1/media");
  });

  it("throws on non-ok responses", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 409, json: async () => ({ detail: "x" }) });
    await expect(listVideoModels("tok")).rejects.toThrow(/409/);
  });
});
