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
  return ["queued", "extracting", "transcribing", "translating", "overlaying"]
    .includes(status);
}

export function pdfUploadUrl(): string {
  return `${apiBase()}/api/v1/pdf-jobs/upload`;
}

export async function uploadPdfJob(
  file: File, title: string,
  translateProvider?: string, translateCliModel?: string,
  formatHint?: string,
): Promise<{ job_id: string }> {
  const form = new FormData();
  form.append("file", file);
  if (title) form.append("title", title);
  if (translateProvider) form.append("translate_provider", translateProvider);
  if (translateCliModel) form.append("translate_cli_model", translateCliModel);
  if (formatHint) form.append("format_hint", formatHint);
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

/** 서버가 응답 1건에 실어 주는 최대 항목 수(`pdf_jobs.py`의 `min(limit, 500)`). */
const LABEL_PAGE_MAX = 500;

/**
 * 한 응답에 잘리지 않게 **끝까지** 받아 하나로 잇는다.
 *
 * 서버 상한이 500인데 실물 문서는 그보다 많다(1037p 표본 = 1321개). 한 번만
 * 부르면 뒷페이지 라벨이 목록에서도 화면 박스에서도 통째로 사라져 **편집 자체가
 * 불가능**해진다. 게다가 수동 라벨은 합성 결과 맨 뒤에 붙어서(`compose`) 가장
 * 먼저 잘린다 — 방금 넣은 라벨이 사라져 보인다.
 *
 * 받는 도중 다른 클라이언트가 편집하면 `offset` 기준이 흔들려 항목이 겹치거나
 * 빠진다. 그래서 `edits_version`이 바뀌면 이어붙이지 않고 **처음부터 다시**
 * 받는다 — 조용히 어긋난 목록보다 한 번 더 받는 편이 낫다.
 *
 * `fetchPage`를 주입받는 이유는 이 리포에 컴포넌트/네트워크 테스트 인프라가
 * 없어서다(테스트는 전부 node 환경 순수 로직). 이 함수만 직접 잠근다.
 */
export async function collectAllLabels(
  fetchPage: (offset: number) => Promise<PdfLabelsResponse>,
  attempts = 3,
): Promise<PdfLabelsResponse> {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const first = await fetchPage(0);
    const items = [...first.items];
    let torn = false;
    while (items.length < first.total) {
      const next = await fetchPage(items.length);
      if (next.edits_version !== first.edits_version) { torn = true; break; }
      // 더 줄 게 없는데 total과 어긋난다 — 무한 루프 대신 받은 만큼으로 끝낸다.
      if (!next.items.length) break;
      items.push(...next.items);
    }
    if (!torn) return { ...first, items };
  }
  throw new Error("라벨 목록을 받는 중 편집이 계속 바뀌었습니다 — 다시 시도하세요");
}

/** 목록표·오버레이가 같은 **전량**을 보도록 페이지를 밀어 가며 받는다. */
export async function fetchAllPdfLabels(
  jobId: string, opts: { kind?: string; q?: string } = {},
): Promise<PdfLabelsResponse> {
  return collectAllLabels((offset) =>
    listPdfLabels(jobId, { ...opts, offset, limit: LABEL_PAGE_MAX }));
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

// ── 서버 운영자 기능 스위치 ──────────────────────────────────────────────────

export type PdfFeatures = {
  storyboard: boolean; xsheet: boolean;
  defaultProvider: string; blockedProviders: string[];
};

export const ALL_PDF_FEATURES_ENABLED: PdfFeatures = {
  storyboard: true, xsheet: true,
  // 서버 조회 실패 시에도 gemini는 잠근다(API 비용은 서버가 최종 막지만
  // 옛 서버엔 그 게이트가 없다).
  defaultProvider: "claude", blockedProviders: ["gemini"],
};

/** 모르는 값은 **켜짐**으로 읽는다 — 차단은 서버가 하고 화면은 표시만 한다. */
export function parsePdfFeatures(body: unknown): PdfFeatures {
  const root = body as {
    formats?: Record<string, unknown>;
    default_provider?: unknown; blocked_providers?: unknown;
  } | null;
  const formats = root?.formats;
  const flag = (key: "storyboard" | "xsheet"): boolean =>
    typeof formats?.[key] === "boolean" ? (formats[key] as boolean) : true;
  // 엔진 정책만은 반대 방향 — 못 읽으면 기본값(claude·gemini 차단)으로 잠근다.
  const raw = root?.blocked_providers;
  const blocked = Array.isArray(raw) && raw.every((v) => typeof v === "string")
    ? (raw as string[]) : [...ALL_PDF_FEATURES_ENABLED.blockedProviders];  // 폴백 상수를 참조로 공유하지 않는다
  const dflt = typeof root?.default_provider === "string" && root.default_provider
    ? root.default_provider : ALL_PDF_FEATURES_ENABLED.defaultProvider;
  return {
    storyboard: flag("storyboard"), xsheet: flag("xsheet"),
    defaultProvider: dflt, blockedProviders: blocked,
  };
}

/**
 * 절대 던지지 않는다. 라우트가 없는 옛 서버(404)나 일시적 통신 실패에 기능을
 * 숨기면 멀쩡한 서버가 반쪽이 된다 — 그럴 땐 둘 다 켜진 것으로 본다.
 */
export async function fetchPdfFeatures(): Promise<PdfFeatures> {
  try {
    const resp = await fetch(`${apiBase()}/api/v1/pdf-jobs/features`);
    if (!resp.ok) return ALL_PDF_FEATURES_ENABLED;
    return parsePdfFeatures(await resp.json());
  } catch {
    return ALL_PDF_FEATURES_ENABLED;
  }
}

export function pdfFormatEnabled(
  features: PdfFeatures, format: "storyboard" | "xsheet",
): boolean {
  return features[format];
}

/** 막혀 있거나 비어 있는 선택만 기본 엔진으로 되돌린다. */
export function resolvePdfProvider(
  current: string, features: PdfFeatures,
): string {
  if (!current || features.blockedProviders.includes(current)) {
    return features.defaultProvider;
  }
  return current;
}
