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
        audioDeviceName: values.audioDeviceName,
        projectDir: values.sidecarProjectDir,
      },
    }),
  );
}

function validateSidecarValues(values: SetupValues): void {
  if (!values.deviceApiKey.trim()) {
    throw new Error("테스트용 오디오 키(Device API Key)를 먼저 입력하세요. 이 값은 저장되지 않아서 sidecar 시작 직전에 매번 붙여넣어야 합니다.");
  }
  if (!values.sessionId.trim() || values.sessionId.includes("<")) {
    throw new Error("Live Meeting에서 회의를 만든 뒤 생성된 Session ID가 필요합니다.");
  }
  if (!values.serverWsBase.trim() || values.serverWsBase.includes("<")) {
    throw new Error("WebSocket 서버 주소를 입력하세요. 로컬 테스트는 ws://127.0.0.1:8000, LAN 테스트는 wss://192.168.0.38 입니다.");
  }
  if (!values.audioDeviceName.trim()) {
    throw new Error("오디오 장치 이름을 입력하세요. Mac 기본값은 (?i)blackhole입니다.");
  }
}

export async function stopSidecar(): Promise<SidecarStatus> {
  requireTauriRuntime();
  return timedSidecarAction("stop_sidecar", () => invoke<SidecarStatus>("stop_sidecar"));
}

export async function loadSidecarStatus(): Promise<SidecarStatus> {
  if (!hasTauriRuntime()) {
    appLogger.info("sidecar", "Sidecar status requested in browser preview");
    return {
      running: false,
      pid: null,
      detail: "브라우저 미리보기에서는 sidecar 실행 버튼이 비활성화됩니다.",
    };
  }
  return timedSidecarAction("sidecar_status", () => invoke<SidecarStatus>("sidecar_status"));
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
