import { apiBase } from "./sessionApi";

export type VideoModelInfo = {
  name: string;
  label: string;
  approx_bytes: number;
  downloaded: boolean;
  disk_bytes: number;
  downloading: boolean;
  progress: number | null;
  // 서버 내장 모델(예: Apple 온디바이스): 다운로드/삭제 대상이 아님 — 클라는
  // 삭제 버튼을 숨기고 크기 대신 "내장"을 표시한다.
  builtin?: boolean;
  // 이 기기에서 실제 선택 가능한지. 생략(undefined)이면 사용 가능으로 본다.
  // Apple 온디바이스 모델은 인텔맥/윈도우/구버전 macOS에서 false로 와서
  // 목록엔 보이되 비활성 처리된다(번역 엔진의 available과 동일 정책).
  available?: boolean;
};

export type VideoJobSummary = {
  job_id: string;
  title: string;
  source_type: "youtube" | "upload";
  source_ref: string;
  whisper_model: string;
  translate_provider: string | null;
  status: string;
  progress: number;
  error: string | null;
  created_at: string | null;
};

export type VideoSegmentOut = {
  seq: number;
  start_ms: number;
  end_ms: number;
  text_en: string;
  text_ko: string;
};

export type VideoJobDetail = VideoJobSummary & { segments: VideoSegmentOut[] };

export type TranslateEngineInfo = {
  value: string;
  label: string;
  available: boolean;
};

export type BurnStyle = {
  position: "bottom" | "top";
  margin_v: number;
  font_size: number;
  color: string;
};

async function request<T>(url: string, init: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) throw new Error(`video api failed: HTTP ${response.status}`);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function listVideoModels(): Promise<VideoModelInfo[]> {
  const out = await request<{ models: VideoModelInfo[] }>(
    `${apiBase()}/api/v1/video-models`, {});
  return out.models;
}

export async function downloadVideoModel(name: string): Promise<void> {
  await request(`${apiBase()}/api/v1/video-models/${name}/download`,
    { method: "POST" });
}

export async function deleteVideoModel(name: string): Promise<void> {
  await request(`${apiBase()}/api/v1/video-models/${name}`,
    { method: "DELETE" });
}

// 로컬 번역 모델(Qwen) — 런타임(mlx/ollama)은 서버 플랫폼이 결정한다.
export type TranslateModelInfo = {
  name: string;
  label: string;
  runtime: "mlx" | "ollama";
  approx_bytes: number;
  downloaded: boolean;
  downloading: boolean;
  progress: number | null;
  downloadable: boolean;
};

export type TranslateModelsResponse = {
  models: TranslateModelInfo[];
  runtime: "mlx" | "ollama";
  ollama_installed: boolean;
  ollama_running: boolean;
};

export async function listTranslateModels(): Promise<TranslateModelsResponse> {
  // 구버전 서버 번들에는 /translate-models 라우트가 없다 — 호출부에서 404를 잡아 탭만 숨긴다.
  return request(`${apiBase()}/api/v1/translate-models`, {});
}

export async function downloadTranslateModel(name: string): Promise<void> {
  await request(`${apiBase()}/api/v1/translate-models/${name}/download`,
    { method: "POST" });
}

export async function deleteTranslateModel(name: string): Promise<void> {
  await request(`${apiBase()}/api/v1/translate-models/${name}`,
    { method: "DELETE" });
}

export type GpuStatus = {
  supported: boolean;
  gpu_name: string | null;
  installed: boolean;
  downloading: boolean;
  progress: number | null;
  cuda_available: boolean;
  // cuda_ok/cuda_reason: 전사 CUDA 인식 성공 여부와 실패 사유(예: "cuDNN 미설치").
  // enabled+installed인데 cuda_ok=false면 전사는 CPU로 조용히 폴백 중 — UI가
  // 사유를 보여줄 수 있도록 서버가 표면화한다.
  cuda_ok?: boolean;
  cuda_reason?: string | null;
  enabled: boolean;
  approx_bytes: number;
  last_error?: string | null;
};

export async function getGpuStatus(): Promise<GpuStatus> {
  return request(`${apiBase()}/api/v1/video-models/gpu`, {});
}

export async function downloadGpuPack(): Promise<void> {
  await request(`${apiBase()}/api/v1/video-models/gpu/pack`,
    { method: "POST" });
}

export async function setGpuEnabled(enabled: boolean): Promise<void> {
  await request(`${apiBase()}/api/v1/video-models/gpu/enable`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
}

export async function createYoutubeJob(
  params: {
    url: string; whisperModel: string; title?: string;
    translateProvider?: string; translateCliModel?: string;
  },
): Promise<{ job_id: string }> {
  return request(`${apiBase()}/api/v1/video-jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      youtube_url: params.url,
      whisper_model: params.whisperModel,
      title: params.title ?? null,
      translate_provider: params.translateProvider || null,
      translate_cli_model: params.translateCliModel || null,
    }),
  });
}

export async function uploadVideoJob(
  file: File, whisperModel: string, title: string,
  translateProvider?: string, translateCliModel?: string,
): Promise<{ job_id: string }> {
  const form = new FormData();
  form.append("file", file);
  form.append("whisper_model", whisperModel);
  if (title) form.append("title", title);
  if (translateProvider) form.append("translate_provider", translateProvider);
  if (translateCliModel) form.append("translate_cli_model", translateCliModel);
  return request(`${apiBase()}/api/v1/video-jobs/upload`,
    { method: "POST", body: form });
}

export async function listTranslateEngines(): Promise<TranslateEngineInfo[]> {
  const out = await request<{ engines: TranslateEngineInfo[] }>(
    `${apiBase()}/api/v1/video-jobs/translate-engines`, {});
  return out.engines;
}

export type VideoStorageInfo = {
  total_bytes: number;
  job_count: number;
  keep: number;
};

export async function getVideoStorage(): Promise<VideoStorageInfo> {
  return request(`${apiBase()}/api/v1/video-jobs/storage`, {});
}

export async function listVideoJobs(): Promise<VideoJobSummary[]> {
  const out = await request<{ items: VideoJobSummary[] }>(
    `${apiBase()}/api/v1/video-jobs`, {});
  return out.items;
}

export async function getVideoJob(jobId: string): Promise<VideoJobDetail> {
  return request(`${apiBase()}/api/v1/video-jobs/${jobId}`, {});
}

export async function patchSegments(
  jobId: string, edits: Array<{ seq: number; text_ko: string }>,
): Promise<void> {
  await request(`${apiBase()}/api/v1/video-jobs/${jobId}/segments`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ edits }),
  });
}

export async function burnVideoJob(
  jobId: string, style: BurnStyle,
): Promise<void> {
  await request(`${apiBase()}/api/v1/video-jobs/${jobId}/burn`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(style),
  });
}

export async function deleteVideoJob(jobId: string): Promise<void> {
  await request(`${apiBase()}/api/v1/video-jobs/${jobId}`,
    { method: "DELETE" });
}

export async function rebuildVideoJob(jobId: string): Promise<void> {
  await request(`${apiBase()}/api/v1/video-jobs/${jobId}/rebuild`,
    { method: "POST" });
}

export async function cancelVideoJob(jobId: string): Promise<void> {
  await request(`${apiBase()}/api/v1/video-jobs/${jobId}/cancel`,
    { method: "POST" });
}

export type CancelAllResult = { cancelled_queued: number; cancelled_active: number };

export async function cancelAllVideoJobs(): Promise<CancelAllResult> {
  return request(`${apiBase()}/api/v1/video-jobs/cancel-all`,
    { method: "POST" });
}

export function videoUploadUrl(): string {
  // Tauri 네이티브 업로드 커맨드(upload_video_file)에 넘길 엔드포인트 URL.
  return `${apiBase()}/api/v1/video-jobs/upload`;
}

export function videoMediaUrl(jobId: string): string {
  return `${apiBase()}/api/v1/video-jobs/${jobId}/media`;
}

export function videoDownloadUrl(jobId: string, kind: "video" | "srt"): string {
  return `${apiBase()}/api/v1/video-jobs/${jobId}/download?kind=${kind}`;
}
