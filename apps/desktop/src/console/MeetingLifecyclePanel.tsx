// === ANCHOR: MEETING_LIFECYCLE_PANEL_START ===
import { ContractPreview } from "./ContractPreview";
import { LifecycleFields } from "./LifecycleFields";
import { SessionResultPanel } from "./SessionResultPanel";
import { consoleStyles } from "./consoleStyles";
import { useMeetingLifecycle } from "./useMeetingLifecycle";

export function MeetingLifecyclePanel() {
  const lifecycle = useMeetingLifecycle();

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
              disabled={lifecycle.busy}
              style={{ ...consoleStyles.action, ...(lifecycle.busy ? consoleStyles.actionDisabled : null) }}
            >
              Start meeting
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
          <SessionResultPanel
            createdSession={lifecycle.createdSession}
            endedSession={lifecycle.endedSession}
            errorText={lifecycle.errorText}
            statusText={lifecycle.statusText}
            copyViewerUrl={lifecycle.copyViewerUrl}
            downloadReport={lifecycle.downloadReport}
          />
        </div>

        <ContractPreview contractPreview={lifecycle.contractPreview} reportText={lifecycle.reportText} />
      </div>
    </section>
  );
}
// === ANCHOR: MEETING_LIFECYCLE_PANEL_END ===
