// === ANCHOR: SETUPVALUES_START ===
import type { SetupValues } from "./types";

const STORAGE_KEY = "yeson-meet-desktop-setup";
type StoredSetupValues = Omit<SetupValues, "deviceApiKey">;

export const DEFAULT_VALUES: SetupValues = {
  serverWsBase: "wss://192.168.0.38",
  deviceApiKey: "",
  sessionId: "",
  viewerUrl: "https://192.168.0.38/v/<viewer-token>",
  audioDeviceName: "Voicemeeter",
};

// === ANCHOR: SETUPVALUES_LOADVALUES_START ===
export function loadValues(): SetupValues {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_VALUES;
    const stored = JSON.parse(raw) as Partial<StoredSetupValues>;
    return { ...DEFAULT_VALUES, ...stored, deviceApiKey: "" };
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

// === ANCHOR: SETUPVALUES_POWERSHELLLITERAL_START ===
function powerShellLiteral(value: string): string {
  return `"${value
    .replaceAll("`", "``")
    .replaceAll('"', '`"')
    .replaceAll("$", "`$")
    .replaceAll("\r", "`r")
    .replaceAll("\n", "`n")}"`;
}
// === ANCHOR: SETUPVALUES_POWERSHELLLITERAL_END ===

// === ANCHOR: SETUPVALUES_COMMANDVALUE_START ===
function commandValue(value: string, placeholder: string): string {
  return powerShellLiteral(value || placeholder);
}
// === ANCHOR: SETUPVALUES_COMMANDVALUE_END ===

// === ANCHOR: SETUPVALUES_BUILDPOWERSHELL_START ===
export function buildPowerShell(values: SetupValues): string {
  return [
    `$env:SERVER_WS_BASE=${commandValue(values.serverWsBase, "wss://<server-host>")}`,
    `$env:YESON_DEVICE_API_KEY=${commandValue(values.deviceApiKey, "<plaintext-device-key>")}`,
    `$env:YESON_SESSION_ID=${commandValue(values.sessionId, "<session-uuid>")}`,
    '$env:YESON_SIDECAR_MODE="audio"',
    `$env:YESON_AUDIO_DEVICE_NAME=${commandValue(values.audioDeviceName, "Voicemeeter")}`,
    "uv run python -m apps.client_sidecar.main",
  ].join("\n");
}
// === ANCHOR: SETUPVALUES_BUILDPOWERSHELL_END ===
// === ANCHOR: SETUPVALUES_END ===
