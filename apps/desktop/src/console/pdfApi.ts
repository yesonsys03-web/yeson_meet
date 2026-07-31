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
