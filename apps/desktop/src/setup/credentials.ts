// === ANCHOR: CREDENTIALS_CLIENT_START ===
import { invoke } from "@tauri-apps/api/core";

export type CredentialsInput = {
  serverWsBase: string;
  email: string;
  password: string; // vibelign: allow-secret — field name only, not a key value
  deviceApiKey: string; // vibelign: allow-secret — field name only, not a key value
};

export type CredentialsMeta = {
  hasCredentials: boolean;
  serverWsBase: string;
  email: string;
  hasDeviceKey: boolean;
};

export type OperatorLogin = {
  serverWsBase: string;
  email: string;
  password: string; // vibelign: allow-secret — field name only, not a key value
};

export const EMPTY_META: CredentialsMeta = {
  hasCredentials: false,
  serverWsBase: "",
  email: "",
  hasDeviceKey: false,
};

type TauriWindow = Window & { __TAURI_INTERNALS__?: unknown };

function hasTauriRuntime(): boolean {
  return typeof window !== "undefined" && Boolean((window as TauriWindow).__TAURI_INTERNALS__);
}

export async function saveCredentials(request: CredentialsInput): Promise<void> {
  await invoke("save_credentials", { request });
}

export async function clearCredentials(): Promise<void> {
  await invoke("clear_credentials");
}

export async function loadCredentialsMeta(): Promise<CredentialsMeta> {
  if (!hasTauriRuntime()) return EMPTY_META;
  try {
    return await invoke<CredentialsMeta>("credentials_meta");
  } catch {
    return EMPTY_META;
  }
}

export async function loadOperatorLogin(): Promise<OperatorLogin> {
  return invoke<OperatorLogin>("load_operator_login");
}
// === ANCHOR: CREDENTIALS_CLIENT_END ===
