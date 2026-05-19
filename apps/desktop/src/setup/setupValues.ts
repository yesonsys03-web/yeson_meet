// === ANCHOR: SETUPVALUES_START ===
import { defaultPlatform, PLATFORM_CONFIG } from "./platformConfig";
import type { SetupValues } from "./types";

const STORAGE_KEY = "yeson-meet-desktop-setup";
type StoredSetupValues = Omit<SetupValues, "deviceApiKey">;

function defaultValues(): SetupValues {
  const platform = defaultPlatform();
  return {
    platform,
    serverWsBase: "wss://192.168.0.38",
    deviceApiKey: "",
    sessionId: "",
    viewerUrl: "https://192.168.0.38/v/<viewer-token>",
    audioDeviceName: PLATFORM_CONFIG[platform].audioDeviceName,
  };
}

export const DEFAULT_VALUES: SetupValues = defaultValues();

// === ANCHOR: SETUPVALUES_LOADVALUES_START ===
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
      audioDeviceName: stored.audioDeviceName ?? PLATFORM_CONFIG[platform].audioDeviceName,
      deviceApiKey: "",
    };
  } catch {
    return DEFAULT_VALUES;
  }
}
// === ANCHOR: SETUPVALUES_LOADVALUES_END ===

// === ANCHOR: SETUPVALUES_STOREVALUES_START ===
export function storeValues(values: SetupValues): void {
  const { deviceApiKey: _deviceApiKey, ...safeValues } = values;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(safeValues));
}
// === ANCHOR: SETUPVALUES_STOREVALUES_END ===

// === ANCHOR: SETUPVALUES_HTTPBASEFROMWS_START ===
export function httpBaseFromWs(serverWsBase: string): string {
  if (serverWsBase.startsWith("wss://")) return serverWsBase.replace("wss://", "https://");
  if (serverWsBase.startsWith("https://")) return serverWsBase;
  throw new Error("WebSocket 서버 주소는 wss:// 로 시작해야 합니다.");
}
// === ANCHOR: SETUPVALUES_HTTPBASEFROMWS_END ===

// === ANCHOR: SETUPVALUES_ISSECUREVIEWERURL_START ===
export function isSecureViewerUrl(viewerUrl: string): boolean {
  try {
    return new URL(viewerUrl).protocol === "https:";
  } catch {
    return false;
  }
}
// === ANCHOR: SETUPVALUES_ISSECUREVIEWERURL_END ===
// === ANCHOR: SETUPVALUES_END ===
