// === ANCHOR: CAPTURE_SUPPORT_START ===
// 탭 오디오 캡처는 Chromium 계열 + 보안 컨텍스트(https 또는 localhost)에서만
// 동작한다. LAN http://<IP>:8000 접속이 가장 흔한 실패 경로라 먼저 검사한다.
export type CaptureSupport = { ok: true } | { ok: false; reason: "insecure-context" | "no-display-media" };

type SupportEnv = { isSecureContext: boolean; hasGetDisplayMedia: boolean };

function defaultEnv(): SupportEnv {
  return {
    isSecureContext: window.isSecureContext,
    hasGetDisplayMedia: typeof navigator.mediaDevices?.getDisplayMedia === "function",
  };
}

export function checkCaptureSupport(env: SupportEnv = defaultEnv()): CaptureSupport {
  if (!env.isSecureContext) return { ok: false, reason: "insecure-context" };
  if (!env.hasGetDisplayMedia) return { ok: false, reason: "no-display-media" };
  return { ok: true };
}

export function isChromiumLike(ua: string = navigator.userAgent): boolean {
  return /Chrom(e|ium)\//.test(ua);
}
// === ANCHOR: CAPTURE_SUPPORT_END ===
