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
  yesonMlxModel: string;
  viewerBase: string;
  summaryBackend: string;
  summaryModel: string;
};

export type ServerConfigMeta = {
  hasGeminiKey: boolean;
  hasGoogleCredentials: boolean;
  hasJwtSecret: boolean;
  googleCloudProject: string;
  googleSttLanguageCode: string;
  googleTranslateTargetLanguage: string;
  provider: string;
  mlxModel: string;
  viewerBase: string;
  summaryBackend: string;
  summaryModel: string;
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
  mlxModel: "",
  viewerBase: "",
  summaryBackend: "auto",
  summaryModel: "",
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

// 저지연 EN→KO 번역 모델 1회 설치(성능 후속). apple_live_translate provider 전용.
// 실리콘맥 번들에서만 성공하고, 그 외에서는 Rust가 Err(문자열)를 던진다.
export async function installFastTranslation(): Promise<string> {
  return invoke<string>("install_fast_translation");
}

export async function mlxModelStatus(modelId: string): Promise<boolean> {
  if (!hasTauriRuntime()) return false;
  return invoke<boolean>("mlx_model_status", { modelId });
}

// 이 기기에서 Apple 전사·MLX 하이브리드 provider가 실제 동작 가능한지(번들에
// apple-live-translate 바이너리가 있는지 = 실리콘맥 빌드). 인텔맥/윈도우/구버전
// macOS에서는 false → provider 옵션은 보이되 비활성. 런타임 주입과 같은 진실.
export async function appleTranslateAvailable(): Promise<boolean> {
  if (!hasTauriRuntime()) return false;
  return invoke<boolean>("apple_translate_available");
}

export async function downloadMlxModel(modelId: string): Promise<string> {
  return invoke<string>("mlx_download_model", { modelId });
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
