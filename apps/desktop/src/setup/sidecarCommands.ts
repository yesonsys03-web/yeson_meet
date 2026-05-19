// === ANCHOR: SIDECAR_COMMANDS_START ===
import { PLATFORM_CONFIG } from "./platformConfig";
import type { SetupValues } from "./types";

function powerShellLiteral(value: string): string {
  return `"${value
    .replaceAll("`", "``")
    .replaceAll('"', '`"')
    .replaceAll("$", "`$")
    .replaceAll("\r", "`r")
    .replaceAll("\n", "`n")}"`;
}

function shellLiteral(value: string): string {
  return `'${value.replaceAll("'", `'"'"'`)}'`;
}

function powerShellValue(value: string, placeholder: string): string {
  return powerShellLiteral(value || placeholder);
}

function shellValue(value: string, placeholder: string): string {
  return shellLiteral(value || placeholder);
}

export function buildWindowsSidecarCommand(values: SetupValues): string {
  return [
    `$env:SERVER_WS_BASE=${powerShellValue(values.serverWsBase, "wss://<server-host>")}`,
    `$env:YESON_DEVICE_API_KEY=${powerShellValue(values.deviceApiKey, "<plaintext-device-key>")}`,
    `$env:YESON_SESSION_ID=${powerShellValue(values.sessionId, "<session-uuid>")}`,
    '$env:YESON_SIDECAR_MODE="audio"',
    `$env:YESON_AUDIO_DEVICE_NAME=${powerShellValue(values.audioDeviceName, PLATFORM_CONFIG.windows.audioDeviceName)}`,
    "uv run python -m apps.client_sidecar.main",
  ].join("\n");
}

export function buildMacSidecarCommand(values: SetupValues): string {
  return [
    `export SERVER_WS_BASE=${shellValue(values.serverWsBase, "wss://<server-host>")}`,
    `export YESON_DEVICE_API_KEY=${shellValue(values.deviceApiKey, "<plaintext-device-key>")}`,
    `export YESON_SESSION_ID=${shellValue(values.sessionId, "<session-uuid>")}`,
    'export YESON_SIDECAR_MODE="audio"',
    `export YESON_AUDIO_DEVICE_NAME=${shellValue(values.audioDeviceName, PLATFORM_CONFIG.mac.audioDeviceName)}`,
    "uv run python -m apps.client_sidecar.main",
  ].join("\n");
}

export function buildSidecarCommand(values: SetupValues): string {
  return values.platform === "mac" ? buildMacSidecarCommand(values) : buildWindowsSidecarCommand(values);
}
// === ANCHOR: SIDECAR_COMMANDS_END ===
