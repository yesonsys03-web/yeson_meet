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
  // 엑스시트 PDF 파이프라인이 동시에 띄우는 CLI 세션 수(전사·번역). 기기·구독
  // 상태에 따라 적정값이 달라 운영자가 고른다. 0 = 미설정(기본값 사용).
  pdfTranscribeWorkers: number;
  pdfTranslateWorkers: number;
  // 클라이언트 앱에 노출할 PDF 번역 기능(탭) 스위치. 미설정(옛 설정 블롭)은
  // Rust에서 켜짐으로 읽으므로 여기서는 항상 실제 값을 보낸다.
  pdfStoryboardEnabled: boolean;
  pdfXsheetEnabled: boolean;
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
  pdfTranscribeWorkers: number;
  pdfTranslateWorkers: number;
  pdfStoryboardEnabled: boolean;
  pdfXsheetEnabled: boolean;
};

export type BootstrapAdminResult = {
  created: boolean;
  detail: string;
};

export const DEFAULT_PROVIDER = "gemini_live";

// 워커 수 범위 — 상한은 서버가 전사 워커를 8로 클램프하는 값과 같다
// (handwriting_transcribe._workers). 기본 6은 실측 근거: 3→6에서 전사 1.55배
// (22→35크롭/분)·번역 1.58배(32.0→20.2분), 2026-08-25 A3 116p.
export const MIN_PDF_WORKERS = 1;
export const MAX_PDF_WORKERS = 8;
export const DEFAULT_PDF_WORKERS = 6;

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
  pdfTranscribeWorkers: DEFAULT_PDF_WORKERS,
  pdfTranslateWorkers: DEFAULT_PDF_WORKERS,
  pdfStoryboardEnabled: true,
  pdfXsheetEnabled: true,
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
