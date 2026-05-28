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
    intro: "패키지 Mac 앱은 번들된 네이티브 ScreenCaptureKit sidecar로 회의 소리를 직접 캡처해 yeson-meet 서버로 보냅니다. BlackHole 설치는 필요 없습니다.",
    steps: [
      {
        title: "화면 기록 권한 허용 (Screen Recording)",
        detail: "시스템 설정 > 개인정보 보호 및 보안 > 화면 기록에서 yeson-meet 앱을 허용합니다. ScreenCaptureKit이 시스템 오디오를 캡처하려면 이 권한이 필요합니다.",
      },
      {
        title: "Live Meeting에서 회의 만들기",
        detail: "테스트 계정으로 로그인하고 Start meeting을 누른 뒤, Setup 탭으로 돌아와 Session ID와 Viewer URL이 채워졌는지 봅니다.",
      },
      {
        title: "번들 sidecar 시작",
        detail: "Device API Key를 붙여넣고 'Sidecar 시작' 버튼을 누르면 번들된 네이티브 sidecar가 실행됩니다. 별도 Terminal 명령은 필요 없습니다.",
      },
      {
        title: "(dev/fallback) BlackHole + zsh 경로",
        detail: "개발/대체 경로에서만: BlackHole 2ch를 설치하고 회의 스피커를 BlackHole로 보낸 뒤, Sidecar project folder를 yeson_meet 폴더로 지정하고 오디오 장치 이름을 (?i)blackhole로 둔 채 zsh 명령으로 실행합니다.",
      },
    ],
    reminder: "패키지 Mac 앱은 번들된 네이티브 sidecar를 사용하므로 BlackHole/오디오 장치 이름 설정은 필요 없습니다. (dev/fallback sounddevice 실행 시에만 오디오 장치 이름 (?i)blackhole이 쓰입니다.) Device API Key는 저장하지 않으니 sidecar 시작 직전에 다시 붙여넣으세요.",
  },
  windows: {
    title: "Windows client 실행 순서",
    intro: "Windows 회의실 PC는 Voicemeeter로 회의 소리를 나눠서 yeson-meet sidecar로 보냅니다.",
    steps: [
      {
        title: "Voicemeeter Banana 설치",
        detail: "설치 후 반드시 재부팅하고, Windows 출력 장치를 Voicemeeter Input으로 바꿉니다.",
      },
      {
        title: "A1/B1 라우팅 확인",
        detail: "A1은 실제 스피커, B1은 sidecar가 받을 Voicemeeter Output으로 켭니다.",
      },
      {
        title: "Live Meeting에서 회의 만들기",
        detail: "테스트 계정으로 로그인하고 Start meeting을 누른 뒤 Setup 탭에서 새 Session ID와 Viewer URL을 확인합니다.",
      },
      {
        title: "PowerShell 명령 실행",
        detail: "Sidecar project folder를 yeson_meet 폴더로 지정하고, Device API Key를 붙여넣은 뒤 앱 버튼이나 PowerShell 명령으로 실행합니다.",
      },
    ],
    reminder: "Windows 기본 오디오 장치 이름은 Voicemeeter입니다. sidecar 자동 실행 버튼은 아직 다음 단계 작업입니다.",
  },
};
// === ANCHOR: PLATFORM_RUNBOOK_END ===
