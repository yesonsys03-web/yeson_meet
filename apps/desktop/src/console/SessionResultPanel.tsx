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
  copyViewerUrl: () => void;
  downloadReport: () => void;
};

export function SessionResultPanel({
  createdSession,
  endedSession,
  errorText,
  handoffText,
  statusText,
  copyViewerUrl,
  downloadReport,
}: SessionResultPanelProps) {
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
    </>
  );
}
// === ANCHOR: SESSION_RESULT_PANEL_END ===
