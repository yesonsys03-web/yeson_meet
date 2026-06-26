// === ANCHOR: REPORT_EXPORT_START ===
import type { ReportFormat } from "./sessionApi";
import { fetchSessionReportBytes, fetchSessionSummaryBytes } from "./sessionApi";

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

type WriteFileFn = (path: string, data: Uint8Array) => Promise<void>;

// `report.md` → `report-2026-06-26_14-15-30.md`
function withTimestampSuffix(name: string): string {
  const dot = name.lastIndexOf(".");
  const stem = dot === -1 ? name : name.slice(0, dot);
  const ext = dot === -1 ? "" : name.slice(dot);
  const ts = new Date().toISOString().slice(0, 19).replace("T", "_").replace(/:/g, "-");
  return `${stem}-${ts}${ext}`;
}

/**
 * Write `baseName` into `dir`. If that write throws — the file already exists and
 * is locked (e.g. still open in a viewer on Windows) or overwriting is blocked —
 * retry once with a timestamped sibling name so a re-export never silently fails
 * on a name collision. Returns the filename actually written; re-throws only if
 * the retry also fails (e.g. the folder itself is not writable).
 */
async function writeExport(writeFile: WriteFileFn, dir: string, baseName: string, data: Uint8Array): Promise<string> {
  try {
    await writeFile(`${dir}/${baseName}`, data);
    return baseName;
  } catch {
    const alt = withTimestampSuffix(baseName);
    await writeFile(`${dir}/${alt}`, data);
    return alt;
  }
}

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
    try {
      saved.push(await writeExport(writeFile, dir, filename, result.data));
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

/**
 * Export the standalone summary to a user-selected directory in the requested
 * formats (md/html/docx/pdf). Mirrors {@link exportReports}: directory picker →
 * for each fmt fetch bytes → writeFile → open folder. A format the server has
 * not yet produced (404) is skipped with "요약 아직 생성 안 됨", not fatal.
 *
 * When Tauri runtime is unavailable (browser dev), falls back to browser
 * download for text formats and skips binary ones gracefully.
 */
export async function exportSummary(
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
        const result = await fetchSessionSummaryBytes(sessionId, operatorToken, fmt);
        if (!result.ok || !result.data) {
          skipped.push({ fmt, reason: result.status === 404 ? "요약 아직 생성 안 됨" : `HTTP ${result.status}` });
          continue;
        }
        const blob = new Blob([result.data.buffer as ArrayBuffer], { type: "text/plain" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `summary.${fmt}`;
        a.click();
        saved.push(`summary.${fmt}`);
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

  // Directory picker (not save-file) so sibling formats land in one folder.
  let dir: string | null = opts.defaultDir ?? null;
  if (!dir) {
    const picked = await open({ directory: true, title: "요약본 저장 폴더 선택" });
    if (!picked || typeof picked !== "string") {
      // User cancelled.
      return { saved, skipped, dir: null };
    }
    dir = picked;
  }

  for (const fmt of formats) {
    const result = await fetchSessionSummaryBytes(sessionId, operatorToken, fmt);
    if (!result.ok || !result.data) {
      skipped.push({ fmt, reason: result.status === 404 ? "요약 아직 생성 안 됨" : `HTTP ${result.status || "네트워크 오류"}` });
      continue;
    }
    const filename = `summary.${fmt}`;
    try {
      saved.push(await writeExport(writeFile, dir, filename, result.data));
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
// === ANCHOR: REPORT_EXPORT_END ===
