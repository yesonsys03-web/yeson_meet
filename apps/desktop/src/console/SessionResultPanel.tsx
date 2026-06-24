// === ANCHOR: SESSION_RESULT_PANEL_START ===
import { consoleStyles } from "./consoleStyles";
import type { CreatedSession, EndedSession } from "./types";
import { ViewerQrPanel } from "./ViewerQrPanel";

type SessionResultPanelProps = {
  createdSession: CreatedSession | null;
  endedSession: EndedSession | null;
  errorText: string;
  handoffText: string;
  statusText: string;
  autoOpenExport: boolean;
  busy: boolean;
  copyViewerUrl: () => void;
  downloadReport: () => void;
  exportReport: () => void;
  setAutoOpenExport: (v: boolean) => void;
};

export function SessionResultPanel({
  createdSession,
  endedSession,
  errorText,
  handoffText,
  statusText,
  autoOpenExport,
  busy,
  copyViewerUrl,
  downloadReport,
  exportReport,
  setAutoOpenExport,
}: SessionResultPanelProps) {
  const hasSession = Boolean(createdSession ?? endedSession);

  return (
    <>
      <div style={errorText ? consoleStyles.statusError : consoleStyles.statusOk}>{errorText || statusText}</div>
      <div style={consoleStyles.statusInfo}>{handoffText}</div>
      {createdSession ? (
        <div style={consoleStyles.linkBox}>
          <strong>Viewer URL</strong>
          <div>{createdSession.viewer_url}</div>
          <ViewerQrPanel viewerUrl={createdSession.viewer_url} />
          <button type="button" onClick={copyViewerUrl} style={{ ...consoleStyles.mutedAction, marginTop: 10 }}>
            Copy viewer URL
          </button>
        </div>
      ) : null}
      {endedSession ? (
        <div style={consoleStyles.linkBox}>
          <strong>Report path</strong>
          <div>{endedSession.report_path}</div>
          <button type="button" onClick={downloadReport} style={{ ...consoleStyles.mutedAction, marginTop: 10 }}>
            Load Markdown report
          </button>
        </div>
      ) : null}
      {hasSession ? (
        <div style={{ marginTop: 14, display: "grid", gap: 10 }}>
          <button
            type="button"
            onClick={exportReport}
            disabled={busy}
            style={{ ...consoleStyles.mutedAction, ...(busy ? consoleStyles.actionDisabled : null) }}
          >
            보고서 익스포트 (MD / HTML / DOCX / PDF)
          </button>
          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: "#94a3b8", cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={autoOpenExport}
              onChange={(e) => setAutoOpenExport(e.target.checked)}
              style={{ accentColor: "#38bdf8" }}
            />
            익스포트 후 폴더 자동 열기
          </label>
        </div>
      ) : null}
    </>
  );
}
// === ANCHOR: SESSION_RESULT_PANEL_END ===
