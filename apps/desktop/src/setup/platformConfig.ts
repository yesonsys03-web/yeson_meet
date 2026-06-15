// === ANCHOR: PLATFORM_CONFIG_START ===
import type { SetupPlatform } from "./types";

type PlatformConfig = {
  label: string;
  description: string;
  commandTitle: string;
  copyLabel: string;
  commandHint: string;
};

export const PLATFORM_CONFIG: Record<SetupPlatform, PlatformConfig> = {
  mac: {
    label: "Mac client (native system audio)",
    description: "패키지 Mac 앱은 번들된 네이티브 ScreenCaptureKit sidecar로 오디오를 캡처합니다.",
    commandTitle: "2. 생성된 macOS zsh 명령 (dev/fallback용)",
    copyLabel: "macOS 명령 복사",
    commandHint: "복사한 명령은 dev/fallback sounddevice 실행 전용입니다. yeson-meet 폴더에서 macOS Terminal/zsh에 붙여넣습니다.",
  },
  windows: {
    label: "Windows client (Voicemeeter)",
    description: "Windows 회의실 PC에서 Voicemeeter Output 입력으로 오디오를 보냅니다.",
    commandTitle: "2. 생성된 PowerShell 명령",
    copyLabel: "PowerShell 명령 복사",
    commandHint: "복사한 명령은 yeson-meet 폴더에서 Windows PowerShell에 붙여넣습니다.",
  },
};

export function defaultPlatform(): SetupPlatform {
  if (typeof navigator !== "undefined" && /mac/i.test(navigator.userAgent)) return "mac";
  return "windows";
}
// === ANCHOR: PLATFORM_CONFIG_END ===
