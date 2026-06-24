// === ANCHOR: REPORT_EXPORT_START ===
import type { ReportFormat } from "./sessionApi";
import { fetchSessionReportBytes, fetchSessionSummary } from "./sessionApi";

type TauriGlobal = typeof globalThis & { __TAURI_INTERNALS__?: unknown };

function hasTauriRuntime(): boolean {
  return Boolean((globalThis as TauriGlobal).__TAURI_INTERNALS__);
}

export type ExportResult = {
  saved: string[];
  skipped: Array<{ fmt: ReportFormat; reason: string }>;
  dir: string | null;
};

export const DEFAULT_EXPORT_FORMATS: ReportFormat[] = ["md", "html", "docx", "pdf"];

/**
 * Export session reports to a user-selected directory (or `defaultDir` if
 * provided) and open the folder afterwards.
 *
 * Flow: dialog → pick dir → for each fmt: fetch bytes → writeFile → open dir.
 * Non-OK responses (e.g. pdf 503 when soffice is absent) are skipped, not fatal.
 * When Tauri runtime is unavailable (browser dev), falls back to browser download
 * for text formats and skips binary ones gracefully.
 */
export async function exportReports(
  sessionId: string,
  operatorToken: string,
  formats: ReportFormat[] = DEFAULT_EXPORT_FORMATS,
  opts: { defaultDir?: string; openFolder?: boolean } = {},
): Promise<ExportResult> {
  const { openFolder = true } = opts;
  const saved: string[] = [];
  const skipped: Array<{ fmt: ReportFormat; reason: string }> = [];

  if (!hasTauriRuntime()) {
    // Browser dev fallback: trigger browser download for text formats only.
    for (const fmt of formats) {
      if (fmt === "md" || fmt === "html") {
        const result = await fetchSessionReportBytes(sessionId, operatorToken, fmt);
        if (!result.ok || !result.data) {
          skipped.push({ fmt, reason: `HTTP ${result.status}` });
          continue;
        }
        const blob = new Blob([result.data.buffer as ArrayBuffer], { type: "text/plain" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `report.${fmt}`;
        a.click();
        saved.push(`report.${fmt}`);
      } else {
        skipped.push({ fmt, reason: "브라우저 환경에서는 바이너리 포맷을 저장할 수 없습니다." });
      }
    }
    return { saved, skipped, dir: null };
  }

  // Tauri path: lazy-import plugins to avoid module-load failures in browser.
  const [{ open }, { writeFile }, { openPath }] = await Promise.all([
    import("@tauri-apps/plugin-dialog"),
    import("@tauri-apps/plugin-fs"),
    import("@tauri-apps/plugin-opener"),
  ]);

  // Pick a destination FOLDER (not a save-file dialog). A save-file dialog only
  // grants fs scope to the single chosen file, so writing sibling formats
  // (report.html/.docx/.pdf) into the same folder was blocked — only report.md
  // survived. A directory picker lets every format land in the chosen folder.
  let dir: string | null = opts.defaultDir ?? null;
  if (!dir) {
    const picked = await open({ directory: true, title: "보고서 저장 폴더 선택" });
    if (!picked || typeof picked !== "string") {
      // User cancelled.
      return { saved, skipped, dir: null };
    }
    dir = picked;
  }

  for (const fmt of formats) {
    const result = await fetchSessionReportBytes(sessionId, operatorToken, fmt);
    if (!result.ok || !result.data) {
      skipped.push({ fmt, reason: `HTTP ${result.status || "네트워크 오류"}` });
      continue;
    }
    const filename = fmt === "md" ? "report.md" : `report.${fmt}`;
    const filePath = `${dir}/${filename}`;
    try {
      await writeFile(filePath, result.data);
      saved.push(filename);
    } catch (err) {
      skipped.push({ fmt, reason: err instanceof Error ? err.message : String(err) });
    }
  }

  if (openFolder && dir && saved.length > 0) {
    try {
      await openPath(dir);
    } catch {
      // best-effort — folder open failure must not break the export result
    }
  }

  return { saved, skipped, dir };
}

export type SummaryExportResult =
  | { saved: true; path: string }
  | { saved: false; reason: string };

/**
 * Fetch the standalone summary.md from the server and save it via a Tauri
 * save-file dialog. Returns a result describing success or the reason for
 * failure (including "not yet available" when the server returns 404).
 *
 * Gracefully degrades when Tauri runtime is unavailable (browser dev).
 */
export async function exportSummary(
  sessionId: string,
  operatorToken: string,
): Promise<SummaryExportResult> {
  const result = await fetchSessionSummary(sessionId, operatorToken);
  if (!result.ok) {
    if (result.status === 404) {
      return { saved: false, reason: "요약이 아직 생성되지 않았습니다 (잠시 후 다시 시도하세요)." };
    }
    return { saved: false, reason: `서버 오류: HTTP ${result.status || "네트워크 오류"}` };
  }
  const text = result.text ?? "";

  if (!hasTauriRuntime()) {
    // Browser dev fallback: trigger browser download.
    const blob = new Blob([text], { type: "text/markdown" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "summary.md";
    a.click();
    return { saved: true, path: "summary.md" };
  }

  // Use writeFile (not writeTextFile) so the existing fs:allow-write-file scope
  // applies — writeTextFile needs a separate fs:allow-write-text-file permission.
  const [{ save }, { writeFile }] = await Promise.all([
    import("@tauri-apps/plugin-dialog"),
    import("@tauri-apps/plugin-fs"),
  ]);

  const filePath = await save({
    defaultPath: "summary.md",
    title: "요약본 저장",
    filters: [{ name: "Markdown", extensions: ["md"] }],
  });
  if (!filePath || typeof filePath !== "string") {
    return { saved: false, reason: "취소됨" };
  }

  try {
    await writeFile(filePath, new TextEncoder().encode(text));
    return { saved: true, path: filePath };
  } catch (err) {
    return { saved: false, reason: err instanceof Error ? err.message : String(err) };
  }
}
// === ANCHOR: REPORT_EXPORT_END ===
