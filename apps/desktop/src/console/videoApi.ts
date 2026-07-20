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
  // 카탈로그 티어에서만 채워진다(정적 엔진에는 이 키 자체가 없다). 이 서버가
  // 해당 런타임(mlx/ollama)을 아예 지원하지 않을 때만 값이 온다 — 미설치와는
  // 다른 상태이므로 optional로 유지한다.
  reason?: string | null;
};

export type BurnStyle = {
  position: "bottom" | "top";
  margin_v: number;
  font_size: number;
  color: string;
};

async function request<T>(url: string, init: RequestInit): Promise<T> {
  // JSON 본문에는 Content-Type을 자동으로 붙인다 — 없으면 FastAPI가 본문을 JSON으로
  // 파싱하지 않아 422가 난다(실기). 함수마다 손으로 붙이면 새 엔드포인트를 추가할
  // 때 또 빠뜨린다. FormData(멀티파트)는 브라우저가 boundary와 함께 직접 설정해야
  // 하므로 문자열 본문일 때만 건드린다.
  if (typeof init.body === "string"
      && !(init.headers as Record<string, string> | undefined)?.["Content-Type"]) {
    init = { ...init,
             headers: { ...(init.headers as Record<string, string> | undefined),
                        "Content-Type": "application/json" } };
  }
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

export async function refreshVideoModels(): Promise<VideoModelInfo[]> {
  const out = await request<{ models: VideoModelInfo[] }>(
    `${apiBase()}/api/v1/video-models?refresh=1`, {});
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
  // 이 서버의 런타임을 아예 지원하지 않는 티어에만 채워진다(예: "실리콘맥 전용").
  // null인데 downloaded=false면 그냥 미설치 — 다운로드하면 쓸 수 있다.
  reason: string | null;
  mlx_repo: string | null;
  // MLX 리포 용량(≈RAM). approx_bytes는 이 서버의 런타임 기준값이라, Ollama
  // 서버에서는 MLX 용량을 알 수 없어 서버가 별도로 싣는다(라이브 자막 패널용).
  mlx_bytes: number;
  ollama_tag: string | null;
};

export type OllamaInstallStatus = {
  supported: boolean;
  downloading: boolean;
  progress: number;
  launched: boolean;
  last_error: string | null;
};

export type TranslateModelsResponse = {
  models: TranslateModelInfo[];
  runtime: "mlx" | "ollama";
  ollama_installed: boolean;
  ollama_running: boolean;
  // ollama 런타임에서만 non-null. 미설치 시 클라가 '설치' 버튼을 표시한다.
  ollama_install: OllamaInstallStatus | null;
};

export async function listTranslateModels(): Promise<TranslateModelsResponse> {
  // 구버전 서버 번들에는 /translate-models 라우트가 없다 — 호출부에서 404를 잡아 탭만 숨긴다.
  return request(`${apiBase()}/api/v1/translate-models`, {});
}

export async function refreshTranslateModels(): Promise<TranslateModelsResponse> {
  return request(`${apiBase()}/api/v1/translate-models?refresh=1`, {});
}

// 공식 Ollama 설치 프로그램을 서버 머신에 받아 실행(반자동). 설치는 서버 컴퓨터에서 진행된다.
export async function installOllama(): Promise<void> {
  await request(`${apiBase()}/api/v1/translate-models/ollama/install`,
    { method: "POST" });
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

export type RetranslateResult = {
  total: number;
  retranslated: number;
  remaining: number;
};

export async function retranslateSegments(
  jobId: string, provider: string, cliModel?: string,
): Promise<RetranslateResult> {
  return request<RetranslateResult>(
    `${apiBase()}/api/v1/video-jobs/${jobId}/retranslate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, cli_model: cliModel ?? null }),
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

export type SceneSegment = { label: string; start_ms: number; end_ms: number };

export type ScenesData = {
  scanned: boolean;
  // 긴 영상 스캔 진행 상태(백엔드가 증분 기록). scanning 중이면 ocr_done/total_frames로
  // 진척을 표시하고, error가 있으면 폴링을 멈춘다.
  scanning?: boolean;
  ocr_done?: number;
  total_frames?: number;
  error?: string | null;
  frames: Array<{ t_ms: number; text: string }>;
  segments_scene: SceneSegment[];
  segments_sequence: SceneSegment[];
  rule: SlateRuleInput | null;
  interval_ms?: number;
  // 사용자가 지정한 슬레이트 구역(비율). 없으면 전체 프레임 + 상단 밴드 가정.
  ocr_region?: OcrRegion | null;
};

export type SlateRuleInput = {
  delimiters?: string[];
  seq_tokens: number[];
  scene_tokens?: number[];
  min_ms?: number;
};

export async function scanScenes(jobId: string): Promise<void> {
  await request(`${apiBase()}/api/v1/video-jobs/${jobId}/scenes/scan`,
    { method: "POST" });
}

export async function getScenes(jobId: string): Promise<ScenesData> {
  return request(`${apiBase()}/api/v1/video-jobs/${jobId}/scenes`, {});
}

export async function setSceneRule(
  jobId: string, rule: SlateRuleInput,
): Promise<{ segments_scene: SceneSegment[]; segments_sequence: SceneSegment[] }> {
  return request(`${apiBase()}/api/v1/video-jobs/${jobId}/scenes/rule`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(rule),
  });
}

export async function overrideSceneSegments(
  jobId: string, mode: "scene" | "sequence", segments: SceneSegment[],
): Promise<void> {
  await request(`${apiBase()}/api/v1/video-jobs/${jobId}/scenes/segments`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, segments }),
  });
}

export async function exportScenes(
  jobId: string, mode: "scene" | "sequence", outDir?: string,
): Promise<{ status: string; count: number }> {
  return request(`${apiBase()}/api/v1/video-jobs/${jobId}/scenes/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, out_dir: outDir ?? null }),
  });
}

export function sceneThumbUrl(jobId: string, index: number): string {
  return `${apiBase()}/api/v1/video-jobs/${jobId}/scenes/thumb/${index}`;
}

// 임의 시각 썸네일 — 정밀화된 구간 시작(2초 격자 밖) 프레임 확인용.
export function sceneThumbAtUrl(jobId: string, tMs: number): string {
  return `${apiBase()}/api/v1/video-jobs/${jobId}/scenes/thumb-at?t_ms=${tMs}`;
}

// 슬레이트 구역(프레임 대비 비율) — 쇼마다 위치가 달라 사용자가 드래그로 지정한다.
export type OcrRegion = { x: number; y: number; w: number; h: number };

export type SlateTemplate = {
  name: string;
  region: OcrRegion;
  delimiters: string[];
  seq_tokens: number[];
  scene_tokens: number[];
};

export async function setOcrRegion(
  jobId: string, region: OcrRegion,
): Promise<{ ocr_region: OcrRegion }> {
  return request(`${apiBase()}/api/v1/video-jobs/${jobId}/scenes/ocr-region`,
    { method: "POST", body: JSON.stringify(region) });
}

// 지정한 구역으로 한 프레임만 읽어본다 — 긴 스캔 전에 구역이 맞는지 확인.
export async function testOcrRegion(
  jobId: string, tMs: number, region: OcrRegion | null,
): Promise<{ text: string; tokens: string[] }> {
  return request(`${apiBase()}/api/v1/video-jobs/${jobId}/scenes/ocr-test`,
    { method: "POST", body: JSON.stringify({ t_ms: tMs, region }) });
}

// 진행 중인 스캔/정밀화/익스포트 중단.
export async function cancelSceneOps(jobId: string): Promise<void> {
  await request(`${apiBase()}/api/v1/video-jobs/${jobId}/scenes/cancel`,
    { method: "POST" });
}

export async function listSlateTemplates(): Promise<{ templates: SlateTemplate[] }> {
  return request(`${apiBase()}/api/v1/video-jobs/slate-templates`, {});
}

export async function saveSlateTemplate(
  t: SlateTemplate,
): Promise<{ templates: SlateTemplate[] }> {
  return request(`${apiBase()}/api/v1/video-jobs/slate-templates`,
    { method: "POST", body: JSON.stringify(t) });
}

export async function deleteSlateTemplate(
  name: string,
): Promise<{ templates: SlateTemplate[] }> {
  return request(
    `${apiBase()}/api/v1/video-jobs/slate-templates/${encodeURIComponent(name)}`,
    { method: "DELETE" });
}

export type ExportStatus = {
  exporting: boolean;
  done: number;
  total: number;
  error: string | null;
  out_dir: string | null;
  files: string[];
};

export async function getExportStatus(jobId: string): Promise<ExportStatus> {
  return request(`${apiBase()}/api/v1/video-jobs/${jobId}/scenes/export/status`, {});
}

export type RefineStatus = {
  refining: boolean;
  done: number;
  total: number;
  error: string | null;
};

export async function refineScenes(
  jobId: string, mode: "scene" | "sequence",
): Promise<{ status: string; total: number }> {
  return request(`${apiBase()}/api/v1/video-jobs/${jobId}/scenes/refine`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode }),
  });
}

export async function getRefineStatus(jobId: string): Promise<RefineStatus> {
  return request(`${apiBase()}/api/v1/video-jobs/${jobId}/scenes/refine/status`, {});
}
