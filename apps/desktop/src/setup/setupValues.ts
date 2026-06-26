// === ANCHOR: SETUPVALUES_START ===
import { defaultPlatform } from "./platformConfig";
import type { SetupValues } from "./types";

const STORAGE_KEY = "yeson-meet-desktop-setup";
export const SETUP_VALUES_UPDATED_EVENT = "yeson-meet-desktop-setup-updated";
type StoredSetupValues = Omit<SetupValues, "deviceApiKey">;

function defaultValues(): SetupValues {
  const platform = defaultPlatform();
  return {
    platform,
    serverWsBase: "",
    deviceApiKey: "",
    sessionId: "",
    viewerUrl: "",
    sidecarProjectDir: "",
  };
}

export const DEFAULT_VALUES: SetupValues = defaultValues();

// === ANCHOR: SETUPVALUES_LOADVALUES_START ===
// P2 invariant: localStorage `serverWsBase` is a DERIVED CACHE of the keychain;
// never author it independently. The authored writer of record is the keychain
// via saveCredentials — hydrated here by hydrateServerAddressFromKeychain().
export function loadValues(): SetupValues {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_VALUES;
    const stored = JSON.parse(raw) as Partial<StoredSetupValues>;
    const platform = stored.platform ?? DEFAULT_VALUES.platform;
    return {
      ...DEFAULT_VALUES,
      ...stored,
      platform,
      deviceApiKey: "",
    };
  } catch {
    return DEFAULT_VALUES;
  }
}
// === ANCHOR: SETUPVALUES_LOADVALUES_END ===

// === ANCHOR: SETUPVALUES_STOREVALUES_START ===
// P2 invariant: localStorage `serverWsBase` = derived cache of the keychain;
// never author independently — the authored writer of record is the keychain
// via saveCredentials. storeValues only re-derives the cache here.
export function storeValues(values: SetupValues): void {
  const { deviceApiKey: _deviceApiKey, ...safeValues } = values;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(safeValues));
  window.dispatchEvent(new CustomEvent(SETUP_VALUES_UPDATED_EVENT));
}
// === ANCHOR: SETUPVALUES_STOREVALUES_END ===

// === ANCHOR: SETUPVALUES_HTTPBASEFROMWS_START ===
export function httpBaseFromWs(serverWsBase: string): string {
  if (serverWsBase.startsWith("wss://")) return serverWsBase.replace("wss://", "https://");
  if (serverWsBase.startsWith("ws://")) return serverWsBase.replace("ws://", "http://");
  if (serverWsBase.startsWith("https://")) return serverWsBase;
  if (serverWsBase.startsWith("http://")) return serverWsBase;
  throw new Error("WebSocket 서버 주소는 ws:// 또는 wss:// 로 시작해야 합니다.");
}
// === ANCHOR: SETUPVALUES_HTTPBASEFROMWS_END ===

// === ANCHOR: SETUPVALUES_END ===
