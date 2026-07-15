// === ANCHOR: SERVER_CONFIG_PANEL_START ===
import { useCallback, useEffect, useState } from "react";
import { append } from "../appLog";
import { MlxModelPanel } from "./MlxModelPanel";
import {
  DEFAULT_PROVIDER,
  EMPTY_META,
  type ServerConfigMeta,
  appleTranslateAvailable,
  bootstrapAdmin,
  clearServerConfig,
  installFastTranslation,
  loadServerConfigMeta,
  passwordStrengthError,
  saveServerConfig,
} from "./serverConfig";

// Apple 전사 바이너리를 요구하는 provider — 실리콘맥 빌드에서만 실제 동작한다.
const APPLE_PROVIDERS = new Set(["apple_live_translate", "apple_mlx_live_translate"]);

function errorToText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function presence(label: string, configured: boolean): string {
  return configured ? `${label}: configured` : `${label}: not set`;
}

const PROVIDERS = [
  "gemini_live_translate",
  "gemini_live",
  "google_stt_translate",
  "apple_live_translate",
  "apple_mlx_live_translate",
] as const;
const SUMMARY_BACKENDS = ["auto", "claude", "codex"] as const;

export default function ServerConfigPanel(props: { port: number; running: boolean }) {
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
  const [mlxModel, setMlxModel] = useState("");
  const [viewerBase, setViewerBase] = useState("");
  const [summaryBackend, setSummaryBackend] = useState<string>("auto");
  const [summaryModel, setSummaryModel] = useState("");

  // First-run operator account form.
  const [adminEmail, setAdminEmail] = useState("");
  const [adminPassword, setAdminPassword] = useState("");

  // 고속(저지연) 번역 모델 설치 — apple_live_translate provider 전용 인라인 상태.
  const [installingFast, setInstallingFast] = useState(false);
  const [fastInstallMsg, setFastInstallMsg] = useState<string | null>(null);

  // 이 기기에서 Apple provider가 실제 동작 가능한지(실리콘맥 번들 여부). 인텔맥/
  // 윈도우에서는 false → apple provider 옵션을 보이되 비활성 처리한다.
  const [appleAvailable, setAppleAvailable] = useState(false);

  const syncMeta = useCallback((next: ServerConfigMeta) => {
    setMeta(next);
    // Hydrate the non-secret editable fields from the projection (secrets stay blank).
    setGoogleProject(next.googleCloudProject);
    setSttLanguage(next.googleSttLanguageCode);
    setTranslateTarget(next.googleTranslateTargetLanguage);
    setProvider(next.provider || DEFAULT_PROVIDER);
    setMlxModel(next.mlxModel);
    setViewerBase(next.viewerBase);
    setSummaryBackend(next.summaryBackend || "auto");
    setSummaryModel(next.summaryModel);
  }, []);

  const refresh = useCallback(async () => {
    try {
      syncMeta(await loadServerConfigMeta());
      setAppleAvailable(await appleTranslateAvailable());
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
        yesonMlxModel: mlxModel,
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
  }, [geminiApiKey, googleCredsJson, googleProject, sttLanguage, translateTarget, provider, mlxModel, viewerBase, summaryBackend, summaryModel, syncMeta]);

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

  const onInstallFastTranslation = useCallback(async () => {
    setFastInstallMsg(null);
    setInstallingFast(true);
    try {
      const detail = await installFastTranslation();
      setFastInstallMsg(detail);
      append({ level: "info", source: "config", message: `fast translation install: ${detail}` });
    } catch (err) {
      const text = errorToText(err);
      setFastInstallMsg(text);
      append({ level: "error", source: "config", message: `fast translation install: ${text}` });
    } finally {
      setInstallingFast(false);
    }
  }, []);

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
          {PROVIDERS.map((p) => {
            // apple provider는 실리콘맥 번들에서만 동작 — 그 외 기기에서는 보이되 비활성.
            const appleGated = APPLE_PROVIDERS.has(p) && !appleAvailable;
            const baseLabel =
              p === "apple_live_translate"
                ? `${p} (실험적)`
                : p === "apple_mlx_live_translate"
                  ? "Apple 전사 + 로컬 LLM 번역 (실험적)"
                  : p;
            return (
              <option
                key={p}
                value={p}
                disabled={appleGated}
                title={
                  p === "apple_live_translate"
                    ? "실험적 — 실리콘맥 전용. 자막 리듬·품질이 gemini_live_translate보다 낮음. 회의에는 gemini_live_translate 권장"
                    : p === "apple_mlx_live_translate"
                      ? "실험적 — 실리콘맥 전용. Apple 전사 + 로컬 LLM 번역. 회의에는 gemini_live_translate 권장"
                      : undefined
                }
              >
                {baseLabel}{appleGated ? " — 실리콘맥 전용 (이 기기 미지원)" : ""}
              </option>
            );
          })}
        </select>
      </Field>

      {provider === "apple_live_translate" ? (
        <div style={{ marginTop: -4, marginBottom: 10 }}>
          <button style={styles.button} onClick={onInstallFastTranslation} disabled={installingFast || busy}>
            {installingFast ? "설치 중…" : "고속 번역 모델 설치"}
          </button>
          {fastInstallMsg ? <p style={{ ...styles.sub, margin: "6px 0 0" }}>{fastInstallMsg}</p> : null}
        </div>
      ) : null}

      {provider === "apple_mlx_live_translate" ? (
        <MlxModelPanel selectedModel={mlxModel} onSelectModel={setMlxModel} port={props.port} running={props.running} />
      ) : null}

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
