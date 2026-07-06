import { apiBase } from "./sessionApi";

export type VideoModelInfo = {
  name: string;
  label: string;
  approx_bytes: number;
  downloaded: boolean;
  disk_bytes: number;
  downloading: boolean;
  progress: number | null;
};

export type VideoJobSummary = {
  job_id: string;
  title: string;
  source_type: "youtube" | "upload";
  source_ref: string;
  whisper_model: string;
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

export type BurnStyle = {
  position: "bottom" | "top";
  margin_v: number;
  font_size: number;
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

export async function createYoutubeJob(
  params: { url: string; whisperModel: string; title?: string },
): Promise<{ job_id: string }> {
  return request(`${apiBase()}/api/v1/video-jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      youtube_url: params.url,
      whisper_model: params.whisperModel,
      title: params.title ?? null,
    }),
  });
}

export async function uploadVideoJob(
  file: File, whisperModel: string, title: string,
): Promise<{ job_id: string }> {
  const form = new FormData();
  form.append("file", file);
  form.append("whisper_model", whisperModel);
  if (title) form.append("title", title);
  return request(`${apiBase()}/api/v1/video-jobs/upload`,
    { method: "POST", body: form });
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

export function videoMediaUrl(jobId: string): string {
  return `${apiBase()}/api/v1/video-jobs/${jobId}/media`;
}

export function videoDownloadUrl(jobId: string, kind: "video" | "srt"): string {
  return `${apiBase()}/api/v1/video-jobs/${jobId}/download?kind=${kind}`;
}
