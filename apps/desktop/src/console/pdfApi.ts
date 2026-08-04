// PDF 스토리보드 번역 API 클라이언트. request 헬퍼는 videoApi.ts의 것과 동형
// 의도적 복제 — videoApi의 비공개 헬퍼를 export로 승격하지 않는다(최소 접촉).
import { apiBase } from "./sessionApi";

export type PdfJobSummary = {
  job_id: string;
  title: string;
  source_ref: string;
  format: string | null;
  translate_provider: string | null;
  status: string;
  progress: number;
  error: string | null;
  page_count: number | null;
  block_count: number | null;
  created_at: string | null;
  // 목록 배지 — 서버는 소형 파일 두 개만 읽어 채운다(계획 파일은 열지 않는다).
  has_edits?: boolean;
  stale?: boolean;
};

export type PdfPanel = { index: number; rect: [number, number, number, number] };

export type PdfPanelsResponse = {
  page_size: [number, number];
  is_panel_page: boolean;
  panels: PdfPanel[];
};

export type PdfLabelItem = {
  id: string;
  origin: "auto" | "manual";
  kind: string;
  page: number;
  panel_index: number | null;
  rect: [number, number, number, number];
  fontsize: number;
  source_text: string;
  text: string;
  edited: boolean;
  editable: boolean;
};

export type PdfLabelsResponse = {
  items: PdfLabelItem[];
  total: number;
  edits_version: number;
  stale: boolean;
  plan_missing?: boolean;
  dangling: { target: string; page: number; text: string | null }[];
  unresolved: { id: string; page: number; panel_index: number; text: string }[];
};

async function request<T>(url: string, init: RequestInit): Promise<T> {
  const resp = await fetch(url, init);
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const body = await resp.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* JSON 아님 — 상태코드만 */
    }
    throw new Error(detail);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export function isActivePdfStatus(status: string): boolean {
  return ["queued", "extracting", "translating", "overlaying"].includes(status);
}

export function pdfUploadUrl(): string {
  return `${apiBase()}/api/v1/pdf-jobs/upload`;
}

export async function uploadPdfJob(
  file: File, title: string,
  translateProvider?: string, translateCliModel?: string,
): Promise<{ job_id: string }> {
  const form = new FormData();
  form.append("file", file);
  if (title) form.append("title", title);
  if (translateProvider) form.append("translate_provider", translateProvider);
  if (translateCliModel) form.append("translate_cli_model", translateCliModel);
  return request(pdfUploadUrl(), { method: "POST", body: form });
}

export async function listPdfJobs(): Promise<PdfJobSummary[]> {
  const out = await request<{ items: PdfJobSummary[] }>(
    `${apiBase()}/api/v1/pdf-jobs`, {});
  return out.items;
}

export async function getPdfJob(jobId: string): Promise<PdfJobSummary> {
  return request(`${apiBase()}/api/v1/pdf-jobs/${jobId}`, {});
}

export function pdfPageUrl(
  jobId: string, page: number, variant: "source" | "translated",
): string {
  return `${apiBase()}/api/v1/pdf-jobs/${jobId}/page/${page}?variant=${variant}`;
}

export function pdfDownloadUrl(jobId: string): string {
  return `${apiBase()}/api/v1/pdf-jobs/${jobId}/download`;
}

export async function cancelPdfJob(jobId: string): Promise<void> {
  await request(`${apiBase()}/api/v1/pdf-jobs/${jobId}/cancel`,
    { method: "POST" });
}

export async function deletePdfJob(jobId: string): Promise<void> {
  await request(`${apiBase()}/api/v1/pdf-jobs/${jobId}`, { method: "DELETE" });
}

// ── 라벨 편집 ────────────────────────────────────────────────────────────────

const JSON_HEADERS = { "Content-Type": "application/json" };

function labelsUrl(jobId: string): string {
  return `${apiBase()}/api/v1/pdf-jobs/${jobId}/labels`;
}

export async function listPdfLabels(
  jobId: string,
  opts: { kind?: string; page?: number; q?: string; offset?: number; limit?: number } = {},
): Promise<PdfLabelsResponse> {
  const qs = new URLSearchParams();
  if (opts.kind) qs.set("kind", opts.kind);
  if (opts.page !== undefined) qs.set("page", String(opts.page));
  if (opts.q) qs.set("q", opts.q);
  if (opts.offset) qs.set("offset", String(opts.offset));
  if (opts.limit) qs.set("limit", String(opts.limit));
  return request(`${labelsUrl(jobId)}?${qs.toString()}`, {});
}

export async function getPdfPanels(
  jobId: string, page: number,
): Promise<PdfPanelsResponse> {
  return request(
    `${apiBase()}/api/v1/pdf-jobs/${jobId}/page/${page}/panels`, {});
}

export async function decodePanelLabel(
  texts: string[],
): Promise<{ lines: string[] | null }> {
  return request(`${apiBase()}/api/v1/pdf-jobs/decode-panel-label`, {
    method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ texts }),
  });
}

export async function createPdfLabel(jobId: string, body: {
  page: number; panel_index: number; rel: [number, number];
  size?: [number, number]; source_text: string; text: string;
  fontsize?: number; edits_version: number;
}): Promise<{ id: string; edits_version: number }> {
  return request(labelsUrl(jobId), {
    method: "POST", headers: JSON_HEADERS, body: JSON.stringify(body),
  });
}

export async function patchPdfLabel(jobId: string, itemId: string, body: {
  text?: string; rect?: [number, number, number, number]; edits_version: number;
}): Promise<{ edits_version: number }> {
  return request(`${labelsUrl(jobId)}/${itemId}`, {
    method: "PATCH", headers: JSON_HEADERS, body: JSON.stringify(body),
  });
}

export async function repointPdfLabel(jobId: string, itemId: string, body: {
  page: number; panel_index: number; rel?: [number, number]; edits_version: number;
}): Promise<{ edits_version: number }> {
  return request(`${labelsUrl(jobId)}/${itemId}/panel`, {
    method: "PATCH", headers: JSON_HEADERS, body: JSON.stringify(body),
  });
}

export async function deletePdfLabel(
  jobId: string, itemId: string, editsVersion: number,
): Promise<{ edits_version: number }> {
  return request(`${labelsUrl(jobId)}/${itemId}`, {
    method: "DELETE", headers: JSON_HEADERS,
    body: JSON.stringify({ edits_version: editsVersion }),
  });
}

export async function purgeDanglingLabels(
  jobId: string, editsVersion: number,
): Promise<{ edits_version: number; manual_count: number }> {
  return request(`${labelsUrl(jobId)}/purge-dangling`, {
    method: "POST", headers: JSON_HEADERS,
    body: JSON.stringify({ edits_version: editsVersion }),
  });
}

export async function rebakePdfJob(jobId: string): Promise<{ status: string }> {
  return request(`${apiBase()}/api/v1/pdf-jobs/${jobId}/rebake`,
    { method: "POST" });
}

export async function retranslatePdfJob(
  jobId: string,
): Promise<{ status: string }> {
  return request(`${apiBase()}/api/v1/pdf-jobs/${jobId}/retranslate`,
    { method: "POST" });
}

/**
 * 404의 두 종류를 구분한다.
 *
 * 없는 작업은 서버가 `작업을 찾을 수 없습니다`를 주고(`pdf_jobs.py:66`),
 * 라우트 자체가 없으면 FastAPI 기본 `Not Found`가 온다 — 후자는 거의 항상
 * **서버 재동결을 안 한 상태**다(dev에서 라우트를 추가하고 굽지 않으면 번들
 * 바이너리에는 그 라우트가 없다).
 */
export function explainPdfError(err: unknown): string {
  const msg = String(err instanceof Error ? err.message : err);
  if (msg === "Not Found") {
    return "서버에 이 기능이 없습니다 — 서버를 다시 동결하세요 (build-server.sh)";
  }
  return msg;
}
