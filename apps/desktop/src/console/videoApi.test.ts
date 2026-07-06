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
  burnVideoJob, createYoutubeJob, listTranslateEngines, listVideoModels, uploadVideoJob,
  videoMediaUrl,
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

  it("listVideoModels GETs /api/v1/video-models without auth", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => ({ models: [] }) });
    await listVideoModels();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toBe("http://localhost:8000/api/v1/video-models");
    expect((init.headers as Record<string, string> | undefined)?.Authorization).toBeUndefined();
  });

  it("createYoutubeJob POSTs JSON body", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 201, json: async () => ({ job_id: "j1" }) });
    const out = await createYoutubeJob({ url: "https://youtu.be/x", whisperModel: "small" });
    expect(out.job_id).toBe("j1");
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      youtube_url: "https://youtu.be/x", whisper_model: "small", title: null,
      translate_provider: null, translate_cli_model: null,
    });
  });

  it("createYoutubeJob sends translate_provider/translate_cli_model when set", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 201, json: async () => ({ job_id: "j1" }) });
    await createYoutubeJob({
      url: "https://youtu.be/x", whisperModel: "small",
      translateProvider: "opencode", translateCliModel: "opencode/deepseek-v4-flash-free",
    });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string);
    expect(body.translate_provider).toBe("opencode");
    expect(body.translate_cli_model).toBe("opencode/deepseek-v4-flash-free");
  });

  it("uploadVideoJob sends multipart FormData", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 201, json: async () => ({ job_id: "j2" }) });
    const file = new File([new Uint8Array([1, 2, 3])], "clip.mp4", { type: "video/mp4" });
    await uploadVideoJob(file, "small", "제목");
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const form = init.body as FormData;
    expect(form.get("whisper_model")).toBe("small");
    expect((form.get("file") as File).name).toBe("clip.mp4");
    expect(form.get("translate_provider")).toBeNull();
  });

  it("uploadVideoJob appends translate_provider/translate_cli_model when set", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 201, json: async () => ({ job_id: "j2" }) });
    const file = new File([new Uint8Array([1, 2, 3])], "clip.mp4", { type: "video/mp4" });
    await uploadVideoJob(file, "small", "제목", "claude");
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const form = init.body as FormData;
    expect(form.get("translate_provider")).toBe("claude");
  });

  it("burnVideoJob POSTs style body", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 202, json: async () => ({ status: "burning" }) });
    await burnVideoJob("j1", { position: "top", margin_v: 20, font_size: 24 });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toContain("/api/v1/video-jobs/j1/burn");
    expect(JSON.parse(init.body as string).position).toBe("top");
  });

  it("listTranslateEngines GETs /api/v1/video-jobs/translate-engines", async () => {
    fetchMock.mockResolvedValue({
      ok: true, status: 200,
      json: async () => ({
        engines: [
          { value: "gemini", label: "Gemini (기본)", available: true },
          { value: "claude", label: "Claude 구독", available: false },
        ],
      }),
    });
    const out = await listTranslateEngines();
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toBe("http://localhost:8000/api/v1/video-jobs/translate-engines");
    expect(out).toEqual([
      { value: "gemini", label: "Gemini (기본)", available: true },
      { value: "claude", label: "Claude 구독", available: false },
    ]);
  });

  it("videoMediaUrl builds capability URL without token", () => {
    expect(videoMediaUrl("j1")).toBe("http://localhost:8000/api/v1/video-jobs/j1/media");
  });

  it("throws on non-ok responses", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 409, json: async () => ({ detail: "x" }) });
    await expect(listVideoModels()).rejects.toThrow(/409/);
  });
});
