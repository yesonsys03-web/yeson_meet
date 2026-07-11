// === ANCHOR: SERVER_CONFIG_PANEL_START ===
import { useCallback, useEffect, useState } from "react";
import { append } from "../appLog";
import {
  DEFAULT_PROVIDER,
  EMPTY_META,
  type ServerConfigMeta,
  bootstrapAdmin,
  clearServerConfig,
  loadServerConfigMeta,
  passwordStrengthError,
  saveServerConfig,
} from "./serverConfig";

function errorToText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function presence(label: string, configured: boolean): string {
  return configured ? `${label}: configured` : `${label}: not set`;
}

const PROVIDERS = ["gemini_live_translate", "gemini_live", "google_stt_translate", "apple_live_translate"] as const;
const SUMMARY_BACKENDS = ["auto", "claude", "codex"] as const;

export default function ServerConfigPanel() {
  const [meta, setMeta] = useState<ServerConfigMeta>(EMPTY_META);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Secret inputs hold the operator's *new* value; blank means "leave the stored
  // secret untouched" (the Rust side preserves blanks). We never echo a stored
  // secret back into these fields.
  const [geminiApiKey, setGeminiApiKey] = useState("");
  const [googleCredsJson, setGoogleCredsJson] = useState("");
  const [googleProject, setGoogleProject] = useState("");
  const [sttLanguage, setSttLanguage] = useState("");
  const [translateTarget, setTranslateTarget] = useState("");
  const [provider, setProvider] = useState<string>(DEFAULT_PROVIDER);
  const [viewerBase, setViewerBase] = useState("");
  const [summaryBackend, setSummaryBackend] = useState<string>("auto");
  const [summaryModel, setSummaryModel] = useState("");

  // First-run operator account form.
  const [adminEmail, setAdminEmail] = useState("");
  const [adminPassword, setAdminPassword] = useState("");

  const syncMeta = useCallback((next: ServerConfigMeta) => {
    setMeta(next);
    // Hydrate the non-secret editable fields from the projection (secrets stay blank).
    setGoogleProject(next.googleCloudProject);
    setSttLanguage(next.googleSttLanguageCode);
    setTranslateTarget(next.googleTranslateTargetLanguage);
    setProvider(next.provider || DEFAULT_PROVIDER);
    setViewerBase(next.viewerBase);
    setSummaryBackend(next.summaryBackend || "auto");
    setSummaryModel(next.summaryModel);
  }, []);

  const refresh = useCallback(async () => {
    try {
      syncMeta(await loadServerConfigMeta());
    } catch (err) {
      setError(errorToText(err));
    }
  }, [syncMeta]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const onSave = useCallback(async () => {
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      const next = await saveServerConfig({
        geminiApiKey,
        googleApplicationCredentialsJson: googleCredsJson,
        googleCloudProject: googleProject,
        googleSttLanguageCode: sttLanguage,
        googleTranslateTargetLanguage: translateTarget,
        yesonAiProvider: provider,
        viewerBase,
        summaryBackend,
        summaryModel,
      });
      syncMeta(next);
      // Clear the secret inputs so stored secrets are never re-shown.
      setGeminiApiKey("");
      setGoogleCredsJson("");
      setNotice("configuration saved to the OS keychain");
      append({ level: "info", source: "config", message: "server config saved" });
    } catch (err) {
      const text = errorToText(err);
      setError(text);
      append({ level: "error", source: "config", message: text });
    } finally {
      setBusy(false);
    }
  }, [geminiApiKey, googleCredsJson, googleProject, sttLanguage, translateTarget, provider, viewerBase, summaryBackend, summaryModel, syncMeta]);

  const onClear = useCallback(async () => {
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      await clearServerConfig();
      await refresh();
      setNotice("configuration cleared from the keychain");
      append({ level: "info", source: "config", message: "server config cleared" });
    } catch (err) {
      setError(errorToText(err));
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  const onCreateOperator = useCallback(async () => {
    setError(null);
    setNotice(null);
    const pwError = passwordStrengthError(adminPassword);
    if (!adminEmail.trim()) {
      setError("email is required");
      return;
    }
    if (pwError) {
      setError(pwError);
      return;
    }
    setBusy(true);
    try {
      const result = await bootstrapAdmin(adminEmail.trim(), adminPassword);
      setAdminPassword("");
      setNotice(result.detail);
      append({ level: "info", source: "config", message: `operator bootstrap: ${result.detail}` });
    } catch (err) {
      const text = errorToText(err);
      setError(text);
      append({ level: "error", source: "config", message: text });
    } finally {
      setBusy(false);
    }
  }, [adminEmail, adminPassword]);

  return (
    <section style={styles.panel}>
      <h2 style={styles.heading}>server configuration</h2>
      <p style={styles.sub}>
        Secrets are stored in the OS keychain and injected into the server at start — never written to disk.
      </p>

      <div style={styles.statusRow}>
        <span style={meta.hasGeminiKey ? styles.chipOn : styles.chipOff}>{presence("Gemini key", meta.hasGeminiKey)}</span>
        <span style={meta.hasGoogleCredentials ? styles.chipOn : styles.chipOff}>
          {presence("Google credentials", meta.hasGoogleCredentials)}
        </span>
        <span style={meta.hasJwtSecret ? styles.chipOn : styles.chipOff}>{presence("JWT secret", meta.hasJwtSecret)}</span>
      </div>

      <Field label="GEMINI_API_KEY">
        <input
          type="password"
          value={geminiApiKey}
          placeholder={meta.hasGeminiKey ? "configured — leave blank to keep" : "not set"}
          onChange={(e) => setGeminiApiKey(e.target.value)}
          style={styles.input}
        />
      </Field>

      <Field label="provider">
        <select value={provider} onChange={(e) => setProvider(e.target.value)} style={styles.input}>
          {PROVIDERS.map((p) => (
            <option key={p} value={p} title={p === "apple_live_translate" ? "실리콘맥 전용 — 다른 서버에서 선택하면 count-only 모드로 시작됩니다" : undefined}>
              {p}
            </option>
          ))}
        </select>
      </Field>

      <Field label="요약 백엔드 (summary backend)">
        <select value={summaryBackend} onChange={(e) => setSummaryBackend(e.target.value)} style={styles.input}>
          {SUMMARY_BACKENDS.map((b) => (
            <option key={b} value={b}>
              {b === "auto" ? "auto (claude → codex 자동 감지)" : b}
            </option>
          ))}
        </select>
      </Field>

      <Field label="요약 모델 (summary model · 선택)">
        <input
          type="text"
          value={summaryModel}
          placeholder="모델을 받는 backend에서만 사용 (예: deepseek)"
          onChange={(e) => setSummaryModel(e.target.value)}
          style={styles.input}
        />
      </Field>

      <Field label="VIEWER_BASE">
        <input
          type="text"
          value={viewerBase}
          placeholder="https://viewer.example.com"
          onChange={(e) => setViewerBase(e.target.value)}
          style={styles.input}
        />
      </Field>

      <Field label="GOOGLE_APPLICATION_CREDENTIALS_JSON">
        <textarea
          value={googleCredsJson}
          placeholder={meta.hasGoogleCredentials ? "configured — leave blank to keep" : "service-account JSON"}
          onChange={(e) => setGoogleCredsJson(e.target.value)}
          style={{ ...styles.input, height: 64, resize: "vertical" }}
        />
      </Field>

      <Field label="GOOGLE_CLOUD_PROJECT">
        <input type="text" value={googleProject} onChange={(e) => setGoogleProject(e.target.value)} style={styles.input} />
      </Field>

      <Field label="GOOGLE_STT_LANGUAGE_CODE">
        <input
          type="text"
          value={sttLanguage}
          placeholder="en-US"
          onChange={(e) => setSttLanguage(e.target.value)}
          style={styles.input}
        />
      </Field>

      <Field label="GOOGLE_TRANSLATE_TARGET_LANGUAGE">
        <input
          type="text"
          value={translateTarget}
          placeholder="ko"
          onChange={(e) => setTranslateTarget(e.target.value)}
          style={styles.input}
        />
      </Field>

      <div style={styles.buttonRow}>
        <button style={{ ...styles.button, ...styles.primary }} onClick={onSave} disabled={busy}>
          Save configuration
        </button>
        <button style={styles.button} onClick={onClear} disabled={busy}>
          Clear configuration
        </button>
      </div>

      <h2 style={{ ...styles.heading, marginTop: 24 }}>create operator account</h2>
      <p style={styles.sub}>
        First-run only: the first operator is created locally (no network). Once an operator exists this is a no-op.
      </p>
      <Field label="operator email">
        <input type="email" value={adminEmail} onChange={(e) => setAdminEmail(e.target.value)} style={styles.input} />
      </Field>
      <Field label="operator password">
        <input
          type="password"
          value={adminPassword}
          placeholder="min 12 chars, letters + numbers"
          onChange={(e) => setAdminPassword(e.target.value)}
          style={styles.input}
        />
      </Field>
      <div style={styles.buttonRow}>
        <button style={{ ...styles.button, ...styles.primary }} onClick={onCreateOperator} disabled={busy}>
          Create operator account
        </button>
      </div>

      {error ? <p style={styles.error}>{error}</p> : null}
      {notice ? <p style={styles.notice}>{notice}</p> : null}
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={styles.field}>
      <span style={styles.fieldLabel}>{label}</span>
      {children}
    </label>
  );
}

const styles: Record<string, React.CSSProperties> = {
  panel: { padding: "16px 20px", borderBottom: "1px solid var(--ys-border-subtle)", background: "transparent", color: "var(--ys-text-body)" },
  heading: { fontSize: 14, margin: "0 0 4px", fontWeight: 600, textTransform: "uppercase", letterSpacing: 0.5, color: "var(--ys-text-strong)" },
  sub: { margin: "0 0 12px", fontSize: 12, color: "var(--ys-text-muted)" },
  statusRow: { display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12 },
  chipOn: {
    fontSize: 11,
    fontWeight: 600,
    padding: "3px 8px",
    borderRadius: "var(--ys-radius-pill)",
    background: "var(--ys-success-bg)",
    color: "var(--ys-success-text)",
  },
  chipOff: {
    fontSize: 11,
    fontWeight: 600,
    padding: "3px 8px",
    borderRadius: "var(--ys-radius-pill)",
    background: "var(--ys-danger-bg)",
    color: "var(--ys-danger-text)",
  },
  field: { display: "flex", flexDirection: "column", gap: 4, marginBottom: 10 },
  fieldLabel: { fontSize: 11, textTransform: "uppercase", letterSpacing: 0.5, color: "var(--ys-text-faint)" },
  input: {
    padding: "6px 8px",
    background: "var(--ys-bg-app)",
    border: "1px solid var(--ys-border-strong)",
    borderRadius: "var(--ys-radius-control)",
    color: "var(--ys-text-body)",
    fontSize: 13,
    fontFamily: "inherit",
  },
  buttonRow: { display: "flex", gap: 10, marginTop: 8 },
  button: {
    padding: "7px 16px",
    borderRadius: "var(--ys-radius-control)",
    border: "1px solid var(--ys-border-strong)",
    background: "transparent",
    color: "var(--ys-text-label)",
    cursor: "pointer",
    fontSize: 13,
  },
  primary: { background: "var(--ys-accent-strong)", borderColor: "var(--ys-accent-strong)", color: "var(--ys-on-accent)", fontWeight: 600 },
  error: { margin: "12px 0 0", color: "var(--ys-danger-text)", fontSize: 13 },
  notice: { margin: "12px 0 0", color: "var(--ys-success-text)", fontSize: 13 },
};
// === ANCHOR: SERVER_CONFIG_PANEL_END ===
