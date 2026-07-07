const API = "/api/v1";

export type ReportRow = {
  session_id: string;
  title: string;
  status: string;
  started_at: string | null;
  ended_at: string | null;
  report_ready: boolean;
  summary_ready: boolean;
  size_bytes?: number;
};

export type ReportStorage = {
  total_bytes: number;
  session_count: number;
};

export type ReportKind = "report" | "summary";
export type ReportFmt = "md" | "html" | "docx" | "pdf";

function base(port: number): string {
  return `http://127.0.0.1:${port}`;
}

export async function listReports(port: number): Promise<ReportRow[]> {
  const r = await fetch(`${base(port)}${API}/reports?with_sizes=true`);
  if (!r.ok) throw new Error(`보고서 목록 조회 실패 (HTTP ${r.status})`);
  return ((await r.json()) as { items: ReportRow[] }).items;
}

export async function getReportStorage(port: number): Promise<ReportStorage> {
  const r = await fetch(`${base(port)}${API}/reports/storage`);
  if (!r.ok) throw new Error(`스토리지 정보 조회 실패 (HTTP ${r.status})`);
  return (await r.json()) as ReportStorage;
}

export function reportViewUrl(port: number, id: string, kind: ReportKind): string {
  const suffix = kind === "summary" ? "/summary/view" : "/view";
  return `${base(port)}${API}/reports/${id}${suffix}`;
}

export async function fetchReportBytes(
  port: number,
  id: string,
  kind: ReportKind,
  fmt: ReportFmt,
): Promise<Uint8Array> {
  const seg = kind === "summary" ? "/summary/download" : "/download";
  const r = await fetch(`${base(port)}${API}/reports/${id}${seg}?fmt=${fmt}`);
  if (!r.ok) throw new Error(`다운로드 실패 (HTTP ${r.status})`);
  return new Uint8Array(await r.arrayBuffer());
}

export async function deleteReportFiles(port: number, id: string): Promise<void> {
  const r = await fetch(`${base(port)}${API}/reports/${id}/files`, { method: "DELETE" });
  if (!r.ok && r.status !== 204) throw new Error(`보고서 파일 삭제 실패 (HTTP ${r.status})`);
}

export async function deleteReportSession(port: number, id: string): Promise<void> {
  const r = await fetch(`${base(port)}${API}/reports/${id}/session`, { method: "DELETE" });
  if (!r.ok && r.status !== 204) throw new Error(`세션 삭제 실패 (HTTP ${r.status})`);
}
