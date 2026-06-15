// === ANCHOR: PLATFORM_RUNBOOK_START ===
import type { SetupPlatform } from "./types";

export type RunbookStep = {
  title: string;
  detail: string;
};

export type PlatformRunbook = {
  title: string;
  intro: string;
  steps: RunbookStep[];
  reminder: string;
};

export const PLATFORM_RUNBOOKS: Record<SetupPlatform, PlatformRunbook> = {
  mac: {
    title: "Mac client 실행 순서",
    intro: "패키지 Mac 앱은 번들된 네이티브 ScreenCaptureKit sidecar로 시스템 소리를 직접 캡처해 yeson-meet 서버로 보냅니다. BlackHole 설치는 필요 없습니다.",
    steps: [
      {
        title: "화면 기록 권한 허용 (Screen Recording)",
        detail: "시스템 설정 > 개인정보 보호 및 보안 > 화면 기록에서 yeson-meet 앱을 허용합니다. ScreenCaptureKit이 시스템 오디오를 캡처하려면 이 권한이 필요합니다.",
      },
      {
        title: "자막을 내보낼 소리를 기본 출력 장치로 재생",
        detail: "캡처는 Mac 기본 출력 장치(시스템 사운드 설정)를 따라갑니다. 회의 소리가 그 장치로 나오게 두세요.",
      },
      {
        title: "Live Meeting에서 회의 만들기",
        detail: "테스트 계정으로 로그인하고 Start meeting을 누른 뒤, Setup 탭으로 돌아와 Session ID와 Viewer URL이 채워졌는지 봅니다.",
      },
      {
        title: "번들 sidecar 시작",
        detail: "Device API Key를 붙여넣고 'Sidecar 시작' 버튼을 누르면 번들된 네이티브 sidecar가 실행됩니다. 별도 Terminal 명령은 필요 없습니다.",
      },
    ],
    reminder: "패키지 Mac 앱은 번들된 네이티브 sidecar를 사용하므로 가상 오디오/장치 이름 설정은 필요 없습니다. Device API Key는 저장하지 않으니 sidecar 시작 직전에 다시 붙여넣으세요.",
  },
  windows: {
    title: "Windows client 실행 순서",
    intro: "Windows 회의실 PC는 번들된 네이티브 WASAPI sidecar가 기본 출력 장치의 소리를 직접 캡처해 yeson-meet 서버로 보냅니다. Voicemeeter 등 가상 오디오 설치는 필요 없습니다.",
    steps: [
      {
        title: "자막을 내보낼 소리를 기본 출력 장치로 재생",
        detail: "캡처는 Windows 기본 출력 장치를 따라갑니다. 자막으로 내보낼 회의 소리가 그 장치로 재생되게 두세요.",
      },
      {
        title: "Live Meeting에서 회의 만들기",
        detail: "테스트 계정으로 로그인하고 Start meeting을 누른 뒤 Setup 탭에서 새 Session ID와 Viewer URL을 확인합니다.",
      },
      {
        title: "번들 sidecar 시작",
        detail: "Device API Key를 붙여넣고 'Sidecar 시작' 버튼을 누르거나 PowerShell 명령을 사용해 번들된 네이티브 sidecar를 실행합니다.",
      },
    ],
    reminder: "캡처는 Windows 기본 출력 장치를 따라갑니다 — 자막을 내보낼 소리가 그 장치로 재생되게 두세요. Device API Key는 저장되지 않으니 sidecar 시작 직전에 다시 붙여넣으세요.",
  },
};
// === ANCHOR: PLATFORM_RUNBOOK_END ===
