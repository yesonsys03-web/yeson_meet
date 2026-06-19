// === ANCHOR: CREDENTIALS_CLIENT_START ===
import { invoke } from "@tauri-apps/api/core";
import { loadValues, storeValues } from "./setupValues";

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

// P2: partial-merge write-through for the advanced server-address field. Updates
// ONLY the keychain serverWsBase, preserving the Device API Key (which JS cannot
// read back), so editing the address after a key exists no longer risks wiping it.
// No-op in browser preview (!hasTauriRuntime).
export async function updateServerWsBase(serverWsBase: string): Promise<void> {
  if (!hasTauriRuntime()) return;
  await invoke("update_server_ws_base", { serverWsBase });
}

// P2: keychain is the authored source of the server WS address; localStorage is a
// derived cache. Reads keychain meta and, when it carries a serverWsBase, writes it
// into localStorage so the synchronous apiBase()/loadValues() readers see it.
// No-op in browser preview (!hasTauriRuntime) so VITE_API_BASE / existing localStorage
// keep working. Keychain-empty is non-destructive: existing localStorage value is kept.
export async function hydrateServerAddressFromKeychain(): Promise<void> {
  if (!hasTauriRuntime()) return;
  const meta = await loadCredentialsMeta();
  if (!meta.serverWsBase) return;
  storeValues({ ...loadValues(), serverWsBase: meta.serverWsBase });
}
// === ANCHOR: CREDENTIALS_CLIENT_END ===
