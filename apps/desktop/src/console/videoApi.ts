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

// 사용자가 눈으로 확인해 "문제없음"으로 표시한 경계오류 구간. 확인 당시의 경계를
// 함께 들고 다닌다 — 나중에 그 씬 경계가 바뀌면 확인표시를 무시해야 하기 때문이다
// (바뀐 경계를 안 본 채로 숨기지 않는다).
export type BoundaryOk = { label: string; start_ms: number; end_ms: number };

// 스캔 방식 — interval(간격 OCR 샘플링+정밀화, 기존)과 fingerprint(전 프레임
// 지문 컷 감지, 프레임 정확·정밀화 불필요)를 나란히 두고 UI에서 고른다.
export type SceneMethod = "interval" | "fingerprint";

export type ScenesData = {
  scanned: boolean;
  // 긴 영상 스캔 진행 상태(백엔드가 증분 기록). scanning 중이면 ocr_done/total_frames로
  // 진척을 표시하고, error가 있으면 폴링을 멈춘다.
  scanning?: boolean;
  ocr_done?: number;
  total_frames?: number;
  // 판독 카운터가 아직 없는 앞 구간(크롭·프레임 추출·컷 감지)의 단계 이름과
  // 살아있음 신호. stage_tick은 산출물이 실제로 늘 때만 오른다 — 정체 판정이
  // 이 값도 진척으로 봐야 멀쩡한 스캔을 실패로 오인하지 않는다.
  stage?: string | null;
  stage_tick?: number;
  error?: string | null;
  frames: Array<{ t_ms: number; text: string }>;
  segments_scene: SceneSegment[];
  segments_sequence: SceneSegment[];
  rule: SlateRuleInput | null;
  interval_ms?: number;
  // 썸네일 간격/개수는 스캔 간격과 분리(성기게) — 필름스트립 격자 계산에 쓴다.
  thumb_interval_ms?: number;
  thumb_count?: number;
  // 사용자가 지정한 슬레이트 구역(비율). 없으면 전체 프레임 + 상단 밴드 가정.
  ocr_region?: OcrRegion | null;
  // 스캔 방식 — fingerprint면 정밀화 단계가 없다(경계가 이미 프레임 정확).
  method?: SceneMethod;
  // 지문 스캔의 영상 전체 길이(마지막 런 끝) — 간격 방식은 프레임 격자로
  // 유도하지만 지문 런은 격자가 없어 명시 값을 쓴다.
  total_ms?: number | null;
  // 실제 영상 fps(측정값). 머리·꼬리 검수에서 구간 끝 프레임 시각을 프레임 단위로
  // 잡는 데 쓴다. 서버가 아직 안 보내면 24로 가정(23.976 NTSC에서도 정확).
  video_fps?: number;
  // 경계 오류(혼입) 검사 결과 — 씬 모드 세그먼트 중 머리/꼬리 프레임에 이웃
  // 슬레이트가 잡힌 구간(플래그된 것만). '⚠ 경계 오류' 필터 탭이 index를 쓴다.
  boundary_issues?: Array<{ index: number; label: string; head: boolean; tail: boolean }>;
  // 사용자가 '문제없음'으로 확인한 구간. 경계가 그대로면 경계오류 탭에서 빠지고,
  // 그 씬 경계를 고치면 다시 나타난다(boundaryIssueIndices).
  boundary_ok?: BoundaryOk[];
};

export type SlateRuleInput = {
  delimiters?: string[];
  seq_tokens: number[];
  scene_tokens?: number[];
  min_ms?: number;
  // 예시 슬레이트 한 줄(예: "Seq 01A_S01 - Panel 1") — 선언하면 서버 canonical화가
  // 토큰별 머리글자를 다수결 대신 이 구조로 스냅한다(Seq↔Seg류 오독). 옵트인.
  example?: string | null;
};

export async function scanScenes(
  jobId: string, intervalS = 2.0, method: SceneMethod = "interval",
): Promise<void> {
  await request(`${apiBase()}/api/v1/video-jobs/${jobId}/scenes/scan`,
    { method: "POST", body: JSON.stringify({ interval_s: intervalS, method }) });
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

// indices를 주면 그 구간만 다시 굽는다(개별 씬 익스포트) — 생략하면 전체.
// 파일명은 서버가 항상 전체 목록 기준으로 dedupe하므로 부분 익스포트도 전체
// 익스포트와 같은 파일을 갱신한다.
export async function exportScenes(
  jobId: string, mode: "scene" | "sequence", outDir?: string, indices?: number[],
): Promise<{ status: string; count: number }> {
  return request(`${apiBase()}/api/v1/video-jobs/${jobId}/scenes/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, out_dir: outDir ?? null,
                           indices: indices ?? null }),
  });
}

// 익스포트한 클립 한 개를 내려받는 주소. 자르기는 서버가 하고(원본·ffmpeg가
// 서버에 있다) 저장은 클라가 한다 — 두 PC가 다를 때 사용자가 고른 폴더가 비어
// 있던 문제의 수정. name은 서버가 만든 파일명 그대로.
export function sceneExportFileUrl(jobId: string, name: string): string {
  return `${apiBase()}/api/v1/video-jobs/${jobId}/scenes/export/file`
    + `?name=${encodeURIComponent(name)}`;
}

// 클라가 다 받은 뒤 서버 사본을 지운다 — 서버는 넘겨줄 목적으로만 굽는다.
// 받는 중 실패하면 부르지 않는다(원본이 사라지면 재인코딩을 다시 해야 한다).
export async function cleanupSceneExport(jobId: string): Promise<{ deleted: number }> {
  return request(`${apiBase()}/api/v1/video-jobs/${jobId}/scenes/export/cleanup`,
    { method: "POST" });
}

// 서버가 이 폴더에 직접 구워도 되는지 확인한다. 클라가 방금 쓴 탐침 파일이 서버
// 쪽에서도 같은 경로로 읽히고 서버도 거기에 쓸 수 있으면 direct=true — 같은 PC이거나
// 같은 공유 폴더라는 증거다. 그때는 굽기→받기 중계를 통째로 건너뛴다.
// 구버전 서버에는 이 라우트가 없어 404가 나는데, 호출자가 잡아 중계로 폴백한다.
export async function probeExportDir(
  jobId: string, dir: string, token: string,
): Promise<{ direct: boolean; reason: string }> {
  return request(`${apiBase()}/api/v1/video-jobs/${jobId}/scenes/export/probe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dir, token }),
  });
}

export function sceneThumbUrl(jobId: string, index: number): string {
  return `${apiBase()}/api/v1/video-jobs/${jobId}/scenes/thumb/${index}`;
}

// 임의 시각 썸네일 — 정밀화된 구간 시작(2초 격자 밖) 프레임 확인용. heightPx를 주면
// 그 높이로 추출한다(머리·꼬리 검수는 슬레이트를 읽어야 해 크게 요청). 미지정=서버
// 기본 90(필름스트립 격자용). 옛 서버는 h 파라미터를 무시하고 90을 준다(무해).
export function sceneThumbAtUrl(jobId: string, tMs: number, heightPx?: number): string {
  const h = heightPx ? `&h=${heightPx}` : "";
  return `${apiBase()}/api/v1/video-jobs/${jobId}/scenes/thumb-at?t_ms=${tMs}${h}`;
}

// 슬레이트 구역(프레임 대비 비율) — 쇼마다 위치가 달라 사용자가 드래그로 지정한다.
export type OcrRegion = { x: number; y: number; w: number; h: number };

export type SlateTemplate = {
  name: string;
  region: OcrRegion;
  delimiters: string[];
  seq_tokens: number[];
  scene_tokens: number[];
  scan_interval_s?: number;
  // 스캔 방식(간격/지문)도 쇼 단위로 정해지는 값이라 템플릿에 함께 저장한다.
  method?: SceneMethod;
  // 예시 슬레이트도 쇼 단위 포맷이라 템플릿에 함께 저장한다.
  example?: string | null;
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

export type BoundaryStatus = {
  checking: boolean;
  done: number;
  total: number;
  error: string | null;
};

// 경계 오류(혼입) 검사 시작 — 씬 모드 세그먼트의 머리·꼬리 프레임을 OCR해 이웃
// 슬레이트가 잡힌 구간을 표시한다. 결과는 getScenes의 boundary_issues에 실린다.
export async function startBoundaryCheck(jobId: string): Promise<void> {
  await request(`${apiBase()}/api/v1/video-jobs/${jobId}/scenes/boundary-check`, {
    method: "POST",
  });
}

// 확인 목록 '전체'를 교체한다(빈 배열 = 모두 해제) — 추가·삭제를 나누면 부분 상태가
// 어긋난다. 클라가 목록의 주인이다.
export async function saveBoundaryOk(
  jobId: string, items: BoundaryOk[],
): Promise<{ count: number }> {
  return request(`${apiBase()}/api/v1/video-jobs/${jobId}/scenes/boundary-ok`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items }),
  });
}

export async function getBoundaryStatus(jobId: string): Promise<BoundaryStatus> {
  return request(
    `${apiBase()}/api/v1/video-jobs/${jobId}/scenes/boundary-check/status`, {});
}
