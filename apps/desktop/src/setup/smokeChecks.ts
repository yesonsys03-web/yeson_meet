// === ANCHOR: SMOKECHECKS_START ===
import { httpBaseFromWs } from "./setupValues";
import type { SetupValues, SmokeCheck, SmokeCheckKey } from "./types";

export const SMOKE_CHECK_ORDER: SmokeCheckKey[] = ["server", "gemini"];

// === ANCHOR: SMOKECHECKS_INITIALSMOKECHECKS_START ===
export function initialSmokeChecks(): Record<SmokeCheckKey, SmokeCheck> {
  return {
    server: {
      key: "server",
      label: "서버 health",
      description: "회의실 PC가 서버까지 닿는지 확인합니다.",
      status: "idle",
      detail: "아직 확인하지 않았습니다.",
    },
    gemini: {
      key: "gemini",
      label: "Gemini 설정",
      description: "서버에 Gemini API Key가 들어가 있는지 확인합니다.",
      status: "idle",
      detail: "아직 확인하지 않았습니다.",
    },
  };
}
// === ANCHOR: SMOKECHECKS_INITIALSMOKECHECKS_END ===

// === ANCHOR: SMOKECHECKS_RUNSMOKECHECK_START ===
export async function runSmokeCheck(
  key: SmokeCheckKey,
  values: SetupValues,
): Promise<Pick<SmokeCheck, "status" | "detail">> {
  if (key === "server") return checkServer(values);
  return checkGemini(values);
}
// === ANCHOR: SMOKECHECKS_RUNSMOKECHECK_END ===

// === ANCHOR: SMOKECHECKS_CHECKSERVER_START ===
async function checkServer(values: SetupValues): Promise<Pick<SmokeCheck, "status" | "detail">> {
  const response = await fetch(`${httpBaseFromWs(values.serverWsBase)}/api/v1/health`);
  if (!response.ok) return { status: "fail", detail: `서버 health 실패: HTTP ${response.status}` };
  return { status: "ok", detail: "서버 health OK" };
}
// === ANCHOR: SMOKECHECKS_CHECKSERVER_END ===

// === ANCHOR: SMOKECHECKS_CHECKGEMINI_START ===
async function checkGemini(values: SetupValues): Promise<Pick<SmokeCheck, "status" | "detail">> {
  const response = await fetch(`${httpBaseFromWs(values.serverWsBase)}/api/v1/health/ai`);
  if (!response.ok) return { status: "fail", detail: `Gemini health 실패: HTTP ${response.status}` };

  // === ANCHOR: SMOKECHECKS_BODY_START ===
  const body = (await response.json()) as { gemini?: { configured?: boolean; status?: string } };
  // === ANCHOR: SMOKECHECKS_BODY_END ===
  if (!body.gemini?.configured) {
    return { status: "fail", detail: `Gemini 설정 필요: ${body.gemini?.status ?? "unknown"}` };
  }
  return { status: "ok", detail: "Gemini configured" };
}
// === ANCHOR: SMOKECHECKS_CHECKGEMINI_END ===
// === ANCHOR: SMOKECHECKS_END ===
