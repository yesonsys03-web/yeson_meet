// === ANCHOR: MEETING_LIFECYCLE_PANEL_START ===
import { useMemo, useState } from "react";
import { consoleStyles } from "./consoleStyles";
import type { MeetingDraft } from "./types";

const initialDraft: MeetingDraft = {
  title: "Client weekly sync",
  clientLabel: "CLIENT-A",
  visibility: "org",
  operatorToken: "",
};

export function MeetingLifecyclePanel() {
  const [draft, setDraft] = useState<MeetingDraft>(initialDraft);

  const sessionPayload = useMemo(
    () => ({
      title: draft.title,
      client_label: draft.clientLabel || null,
      visibility: draft.visibility,
    }),
    [draft],
  );

  const contractPreview = useMemo(
    () =>
      [
        "POST /api/v1/sessions",
        "Authorization: Bearer <operator-token>",
        JSON.stringify(sessionPayload, null, 2),
        "",
        "POST /api/v1/sessions/{session_id}/end",
        "GET  /api/v1/sessions/{session_id}/report",
      ].join("\n"),
    [sessionPayload],
  );

  function updateDraft<K extends keyof MeetingDraft>(key: K, value: MeetingDraft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

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
          <div style={consoleStyles.row}>
            <label style={consoleStyles.field}>
              <span style={consoleStyles.label}>Meeting title</span>
              <input
                value={draft.title}
                onChange={(event) => updateDraft("title", event.currentTarget.value)}
                style={consoleStyles.input}
              />
            </label>
            <label style={consoleStyles.field}>
              <span style={consoleStyles.label}>Client label</span>
              <input
                value={draft.clientLabel}
                onChange={(event) => updateDraft("clientLabel", event.currentTarget.value)}
                style={consoleStyles.input}
              />
            </label>
          </div>

          <label style={consoleStyles.field}>
            <span style={consoleStyles.label}>Operator bearer token</span>
            <input
              type="password"
              value={draft.operatorToken}
              onChange={(event) => updateDraft("operatorToken", event.currentTarget.value)}
              placeholder="Paste operator JWT when backend login is wired"
              style={consoleStyles.input}
            />
          </label>

          <div style={consoleStyles.row}>
            <button type="button" style={consoleStyles.action}>
              Start meeting placeholder
            </button>
            <button type="button" style={consoleStyles.mutedAction}>
              End + report placeholder
            </button>
          </div>
        </div>

        <aside style={consoleStyles.card}>
          <h2 style={{ marginTop: 0 }}>Server contract</h2>
          <pre style={consoleStyles.code}>{contractPreview}</pre>
          <ol style={consoleStyles.steps}>
            <li>Create session and show returned viewer URL.</li>
            <li>Run sidecar with the session ID from the setup assistant.</li>
            <li>End session and download generated Markdown report.</li>
          </ol>
        </aside>
      </div>
    </section>
  );
}
// === ANCHOR: MEETING_LIFECYCLE_PANEL_END ===
