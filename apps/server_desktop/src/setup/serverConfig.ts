// === ANCHOR: SERVER_CONFIG_CLIENT_START ===
// Keychain-backed server config/secrets bridge (Slice 4). Mirrors the client
// app's setup/credentials.ts pattern: a value-bearing input type for writes and
// a presence-only `meta` projection for reads, so the UI never receives a secret
// value — only whether each secret is "configured". The generated-once
// JWT_SECRET is owned entirely by Rust and is never represented here.
import { invoke } from "@tauri-apps/api/core";

export type ServerConfigInput = {
  geminiApiKey: string; // vibelign: allow-secret — field name only, not a key value
  googleApplicationCredentialsJson: string; // vibelign: allow-secret — field name only
  googleCloudProject: string;
  googleSttLanguageCode: string;
  googleTranslateTargetLanguage: string;
  yesonAiProvider: string;
  viewerBase: string;
};

export type ServerConfigMeta = {
  hasGeminiKey: boolean;
  hasGoogleCredentials: boolean;
  hasJwtSecret: boolean;
  googleCloudProject: string;
  googleSttLanguageCode: string;
  googleTranslateTargetLanguage: string;
  provider: string;
  viewerBase: string;
};

export type BootstrapAdminResult = {
  created: boolean;
  detail: string;
};

export const DEFAULT_PROVIDER = "gemini_live";

export const EMPTY_META: ServerConfigMeta = {
  hasGeminiKey: false,
  hasGoogleCredentials: false,
  hasJwtSecret: false,
  googleCloudProject: "",
  googleSttLanguageCode: "",
  googleTranslateTargetLanguage: "",
  provider: DEFAULT_PROVIDER,
  viewerBase: "",
};

type TauriWindow = Window & { __TAURI_INTERNALS__?: unknown };

function hasTauriRuntime(): boolean {
  return typeof window !== "undefined" && Boolean((window as TauriWindow).__TAURI_INTERNALS__);
}

export async function loadServerConfigMeta(): Promise<ServerConfigMeta> {
  if (!hasTauriRuntime()) return EMPTY_META;
  return invoke<ServerConfigMeta>("server_config_meta");
}

export async function saveServerConfig(request: ServerConfigInput): Promise<ServerConfigMeta> {
  return invoke<ServerConfigMeta>("save_server_config", { request });
}

export async function clearServerConfig(): Promise<void> {
  await invoke("clear_server_config");
}

export async function bootstrapAdmin(email: string, password: string): Promise<BootstrapAdminResult> {
  return invoke<BootstrapAdminResult>("bootstrap_admin", { request: { email, password } });
}

// Minimum password strength for the first-run operator account. A small,
// explicit floor — not a policy engine — so the console never comes up with a
// trivially-guessable operator credential exposed over the Slice 5 tunnel.
export const MIN_PASSWORD_LENGTH = 12;

export function passwordStrengthError(password: string): string | null {
  if (password.length < MIN_PASSWORD_LENGTH) {
    return `password must be at least ${MIN_PASSWORD_LENGTH} characters`;
  }
  const hasLetter = /[A-Za-z]/.test(password);
  const hasDigit = /[0-9]/.test(password);
  if (!hasLetter || !hasDigit) {
    return "password must contain both letters and numbers";
  }
  return null;
}
// === ANCHOR: SERVER_CONFIG_CLIENT_END ===
