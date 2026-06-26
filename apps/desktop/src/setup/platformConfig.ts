// === ANCHOR: PLATFORM_CONFIG_START ===
import type { SetupPlatform } from "./types";

export function defaultPlatform(): SetupPlatform {
  if (typeof navigator !== "undefined" && /mac/i.test(navigator.userAgent)) return "mac";
  return "windows";
}
// === ANCHOR: PLATFORM_CONFIG_END ===
