// === ANCHOR: LIFECYCLE_FIELDS_START ===
import { consoleStyles } from "./consoleStyles";
import type { MeetingDraft } from "./types";

type LifecycleFieldsProps = {
  draft: MeetingDraft;
  busy: boolean;
  updateDraft: <K extends keyof MeetingDraft>(key: K, value: MeetingDraft[K]) => void;
  onLogin: () => void;
};

export function LifecycleFields({ draft, busy, updateDraft, onLogin }: LifecycleFieldsProps) {
  return (
    <>
      <div style={consoleStyles.row}>
        <TextField label="Operator email" value={draft.email} type="email" onChange={(value) => updateDraft("email", value)} />
        <TextField label="Operator password" value={draft.password} type="password" onChange={(value) => updateDraft("password", value)} />
      </div>
      <button type="button" onClick={onLogin} disabled={busy} style={{ ...consoleStyles.mutedAction, marginBottom: 16 }}>
        Login operator
      </button>
      <div style={consoleStyles.row}>
        <TextField label="Meeting title" value={draft.title} onChange={(value) => updateDraft("title", value)} />
        <TextField label="Client label" value={draft.clientLabel} onChange={(value) => updateDraft("clientLabel", value)} />
      </div>
      <label style={consoleStyles.field}>
        <span style={consoleStyles.label}>Visibility</span>
        <select
          value={draft.visibility}
          onChange={(event) => updateDraft("visibility", event.currentTarget.value as MeetingDraft["visibility"])}
          style={consoleStyles.input}
        >
          <option value="org">org</option>
          <option value="private">private</option>
        </select>
      </label>
      <TextField
        label="Operator bearer token"
        value={draft.operatorToken}
        type="password"
        placeholder="Login or paste operator JWT"
        onChange={(value) => updateDraft("operatorToken", value)}
      />
    </>
  );
}

function TextField({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  type?: string;
}) {
  return (
    <label style={consoleStyles.field}>
      <span style={consoleStyles.label}>{label}</span>
      <input
        type={type}
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.currentTarget.value)}
        style={consoleStyles.input}
      />
    </label>
  );
}
// === ANCHOR: LIFECYCLE_FIELDS_END ===
