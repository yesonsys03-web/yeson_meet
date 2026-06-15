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
    commandTitle: "2. 생성된 macOS zsh 명령 (dev 실행용)",
    copyLabel: "macOS 명령 복사",
    commandHint: "복사한 명령은 dev 실행 전용입니다. 패키지 앱은 번들된 네이티브 sidecar를 사용하므로 필요 없습니다.",
  },
  windows: {
    label: "Windows client (native system audio)",
    description: "Windows 회의실 PC는 번들된 네이티브 WASAPI sidecar로 시스템 소리를 직접 캡처해 yeson-meet 서버로 보냅니다.",
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
