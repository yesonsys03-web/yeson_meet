// === ANCHOR: MEETING_LIFECYCLE_PANEL_START ===
import { ContractPreview } from "./ContractPreview";
import { LifecycleFields } from "./LifecycleFields";
import { LiveSubtitlePreview } from "./LiveSubtitlePreview";
import { SessionResultPanel } from "./SessionResultPanel";
import { consoleStyles } from "./consoleStyles";
import { useMeetingLifecycle } from "./useMeetingLifecycle";

export function MeetingLifecyclePanel() {
  const lifecycle = useMeetingLifecycle();
  const canStartMeeting = Boolean(lifecycle.draft.operatorToken) && !lifecycle.busy;

  return (
    <section style={consoleStyles.panel}>
      <header style={consoleStyles.header}>
        <div>
          <h1 style={consoleStyles.title}>Meeting lifecycle shell</h1>
          <p style={consoleStyles.subtitle}>
            Slice 4 starts here: operator login token, meeting start payload, viewer URL handoff, and end/report actions are
            shaped around the existing server contracts before wiring live network calls.
          </p>
        </div>
        <span style={consoleStyles.badge}>S4 starter</span>
      </header>

      <div style={consoleStyles.grid}>
        <div style={consoleStyles.card}>
          <LifecycleFields draft={lifecycle.draft} busy={lifecycle.busy} updateDraft={lifecycle.updateDraft} onLogin={lifecycle.login} />
          <div style={consoleStyles.row}>
            <button
              type="button"
              onClick={lifecycle.startMeeting}
              disabled={!canStartMeeting}
              style={{ ...consoleStyles.action, ...(!canStartMeeting ? consoleStyles.actionDisabled : null) }}
            >
              {lifecycle.draft.operatorToken ? "Start meeting" : "Login 후 Start meeting"}
            </button>
            <button
              type="button"
              onClick={lifecycle.finishMeeting}
              disabled={lifecycle.busy || !lifecycle.createdSession}
              style={{
                ...consoleStyles.mutedAction,
                ...(lifecycle.busy || !lifecycle.createdSession ? consoleStyles.actionDisabled : null),
              }}
            >
              End meeting
            </button>
          </div>
          {!lifecycle.draft.operatorToken ? (
            <p style={consoleStyles.statusInfo}>회의를 만들려면 먼저 Operator password를 입력하고 Login operator를 눌러야 합니다.</p>
          ) : null}
          <SessionResultPanel
            createdSession={lifecycle.createdSession}
            endedSession={lifecycle.endedSession}
            errorText={lifecycle.errorText}
            handoffText={lifecycle.handoffText}
            statusText={lifecycle.statusText}
            autoOpenExport={lifecycle.autoOpenExport}
            busy={lifecycle.busy}
            copyViewerUrl={lifecycle.copyViewerUrl}
            downloadReport={lifecycle.downloadReport}
            exportReport={lifecycle.exportReport}
            setAutoOpenExport={lifecycle.setAutoOpenExport}
          />
          <LiveSubtitlePreview
            operatorToken={lifecycle.draft.operatorToken}
            sessionId={lifecycle.createdSession?.session_id ?? null}
          />
        </div>

        <ContractPreview contractPreview={lifecycle.contractPreview} reportText={lifecycle.reportText} reportHtml={lifecycle.reportHtml} />
      </div>
    </section>
  );
}
// === ANCHOR: MEETING_LIFECYCLE_PANEL_END ===
