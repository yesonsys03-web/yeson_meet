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
  burnVideoJob, cancelAllVideoJobs, cancelVideoJob, createYoutubeJob,
  deleteTranslateModel, downloadGpuPack, downloadTranslateModel,
  getGpuStatus, getVideoStorage, installOllama, listTranslateEngines, listTranslateModels,
  listVideoModels, rebuildVideoJob, refreshTranslateModels,
  setGpuEnabled, uploadVideoJob, videoMediaUrl,
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

  it("listTranslateModels GETs /api/v1/translate-models", async () => {
    fetchMock.mockResolvedValue({
      ok: true, status: 200,
      json: async () => ({ models: [], runtime: "ollama", ollama_installed: true, ollama_running: true }),
    });
    const out = await listTranslateModels();
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toBe("http://localhost:8000/api/v1/translate-models");
    expect(out.runtime).toBe("ollama");
  });

  it("downloadTranslateModel POSTs /{name}/download", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 202, json: async () => ({ status: "started" }) });
    await downloadTranslateModel("qwen");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toBe("http://localhost:8000/api/v1/translate-models/qwen/download");
    expect(init.method).toBe("POST");
  });

  it("deleteTranslateModel DELETEs /{name}", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 204, json: async () => ({}) });
    await deleteTranslateModel("qwen_hifi");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toBe("http://localhost:8000/api/v1/translate-models/qwen_hifi");
    expect(init.method).toBe("DELETE");
  });

  it("installOllama POSTs /translate-models/ollama/install", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 202, json: async () => ({ status: "started" }) });
    await installOllama();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toBe("http://localhost:8000/api/v1/translate-models/ollama/install");
    expect(init.method).toBe("POST");
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
    await burnVideoJob("j1", { position: "top", margin_v: 20, font_size: 24, color: "#ffff00" });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toContain("/api/v1/video-jobs/j1/burn");
    const body = JSON.parse(init.body as string);
    expect(body.position).toBe("top");
    expect(body.color).toBe("#ffff00");
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

  it("getVideoStorage GETs /api/v1/video-jobs/storage", async () => {
    fetchMock.mockResolvedValue({
      ok: true, status: 200,
      json: async () => ({ total_bytes: 123, job_count: 2, keep: 10 }),
    });
    const out = await getVideoStorage();
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toBe("http://localhost:8000/api/v1/video-jobs/storage");
    expect(out).toEqual({ total_bytes: 123, job_count: 2, keep: 10 });
  });

  it("videoMediaUrl builds capability URL without token", () => {
    expect(videoMediaUrl("j1")).toBe("http://localhost:8000/api/v1/video-jobs/j1/media");
  });

  it("getGpuStatus GETs /api/v1/video-models/gpu", async () => {
    const status = {
      supported: true, gpu_name: "NVIDIA GeForce RTX 3060", installed: false,
      downloading: false, progress: null, cuda_available: false, enabled: false,
      approx_bytes: 1_000_000_000,
    };
    fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => status });
    const out = await getGpuStatus();
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toBe("http://localhost:8000/api/v1/video-models/gpu");
    expect(out).toEqual(status);
  });

  it("getGpuStatus carries cuda_ok/cuda_reason when transcription CUDA is unavailable", async () => {
    const status = {
      supported: true, gpu_name: "NVIDIA GeForce RTX 3060", installed: true,
      downloading: false, progress: null, cuda_available: false, enabled: true,
      approx_bytes: 1_000_000_000, cuda_ok: false, cuda_reason: "cuDNN 미설치",
    };
    fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => status });
    const out = await getGpuStatus();
    expect(out.cuda_ok).toBe(false);
    expect(out.cuda_reason).toBe("cuDNN 미설치");
  });

  it("downloadGpuPack POSTs /api/v1/video-models/gpu/pack", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 202, json: async () => ({ status: "started" }) });
    await downloadGpuPack();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toBe("http://localhost:8000/api/v1/video-models/gpu/pack");
    expect(init.method).toBe("POST");
  });

  it("rebuildVideoJob POSTs /api/v1/video-jobs/{id}/rebuild", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 202, json: async () => ({ status: "queued" }) });
    await rebuildVideoJob("j1");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toBe("http://localhost:8000/api/v1/video-jobs/j1/rebuild");
    expect(init.method).toBe("POST");
  });

  it("cancelVideoJob POSTs /api/v1/video-jobs/{id}/cancel", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 202, json: async () => ({ status: "canceled" }) });
    await cancelVideoJob("j1");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toBe("http://localhost:8000/api/v1/video-jobs/j1/cancel");
    expect(init.method).toBe("POST");
  });

  it("cancelAllVideoJobs POSTs /api/v1/video-jobs/cancel-all", async () => {
    fetchMock.mockResolvedValue({
      ok: true, status: 200,
      json: async () => ({ cancelled_queued: 2, cancelled_active: 1 }),
    });
    const out = await cancelAllVideoJobs();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toBe("http://localhost:8000/api/v1/video-jobs/cancel-all");
    expect(init.method).toBe("POST");
    expect(out).toEqual({ cancelled_queued: 2, cancelled_active: 1 });
  });

  it("setGpuEnabled POSTs JSON body", async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => ({ enabled: true }) });
    await setGpuEnabled(true);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toBe("http://localhost:8000/api/v1/video-models/gpu/enable");
    expect(JSON.parse(init.body as string)).toEqual({ enabled: true });
  });

  it("throws on non-ok responses", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 409, json: async () => ({ detail: "x" }) });
    await expect(listVideoModels()).rejects.toThrow(/409/);
  });

  it("refreshTranslateModels가 refresh=1로 호출한다", async () => {
    fetchMock.mockResolvedValue({
      ok: true, status: 200,
      json: async () => ({ models: [], runtime: "ollama", ollama_installed: true, ollama_running: true }),
    });
    await refreshTranslateModels();
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(String(url)).toContain("/api/v1/translate-models?refresh=1");
  });
});
