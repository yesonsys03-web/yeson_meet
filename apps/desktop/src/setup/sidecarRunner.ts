// === ANCHOR: SIDECAR_RUNNER_START ===
import { invoke } from "@tauri-apps/api/core";
import { appLogger } from "../diagnostics/appLog";
import type { SetupValues } from "./types";

export type SidecarStatus = {
  running: boolean;
  pid: number | null;
  detail: string;
};

type TauriWindow = Window & { __TAURI_INTERNALS__?: unknown };

function hasTauriRuntime(): boolean {
  return typeof window !== "undefined" && Boolean((window as TauriWindow).__TAURI_INTERNALS__);
}

function requireTauriRuntime(): void {
  if (!hasTauriRuntime()) {
    throw new Error("Sidecar 실행은 Tauri 데스크톱 앱에서만 사용할 수 있습니다.");
  }
}

export async function startSidecar(values: SetupValues): Promise<SidecarStatus> {
  requireTauriRuntime();
  validateSidecarValues(values);
  return timedSidecarAction("start_sidecar", () =>
    invoke<SidecarStatus>("start_sidecar", {
      request: {
        serverWsBase: values.serverWsBase,
        deviceApiKey: values.deviceApiKey, // vibelign: allow-secret — field name only, not a key value
        sessionId: values.sessionId,
        projectDir: values.sidecarProjectDir,
      },
    }),
  );
}

function validateSidecarValues(values: SetupValues): void {
  if (!values.sessionId.trim() || values.sessionId.includes("<")) {
    throw new Error("Live Meeting에서 회의를 만든 뒤 생성된 Session ID가 필요합니다.");
  }
  if (!values.serverWsBase.trim() || values.serverWsBase.includes("<")) {
    throw new Error("WebSocket 서버 주소를 입력하세요. 로컬 테스트는 ws://127.0.0.1:8000, LAN 테스트는 wss://<server-ip>:8000 입니다.");
  }
}

export async function stopSidecar(): Promise<SidecarStatus> {
  requireTauriRuntime();
  return timedSidecarAction("stop_sidecar", () => invoke<SidecarStatus>("stop_sidecar"));
}

async function timedSidecarAction(action: string, run: () => Promise<SidecarStatus>): Promise<SidecarStatus> {
  const startedAt = performance.now();
  appLogger.info("sidecar", `${action} requested`);
  try {
    const status = await run();
    appLogger.latency("sidecar", `${action} completed`, performance.now() - startedAt, { detail: status.detail });
    return status;
  } catch (error) {
    appLogger.error("sidecar", `${action} failed`, {
      durationMs: performance.now() - startedAt,
      detail: error instanceof Error ? error.message : String(error),
    });
    throw error;
  }
}
// === ANCHOR: SIDECAR_RUNNER_END ===
