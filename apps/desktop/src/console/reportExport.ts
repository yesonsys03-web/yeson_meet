// === ANCHOR: REPORT_EXPORT_START ===
import type { ReportFormat } from "./sessionApi";
import { fetchSessionReportBytes } from "./sessionApi";

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
  const [{ save }, { writeFile }, { openPath }] = await Promise.all([
    import("@tauri-apps/plugin-dialog"),
    import("@tauri-apps/plugin-fs"),
    import("@tauri-apps/plugin-opener"),
  ]);

  // Pick save directory via dialog.
  let dir: string | null = opts.defaultDir ?? null;
  if (!dir) {
    // save() with directory option to pick a folder; fall back to Documents.
    const picked = await save({
      title: "보고서 저장 폴더 선택",
      defaultPath: `report.md`,
      filters: [{ name: "Markdown", extensions: ["md"] }],
    });
    if (!picked) {
      // User cancelled.
      return { saved, skipped, dir: null };
    }
    // Extract directory from the chosen path (strip filename).
    dir = picked.replace(/[\\/][^\\/]+$/, "") || picked;
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
// === ANCHOR: REPORT_EXPORT_END ===
