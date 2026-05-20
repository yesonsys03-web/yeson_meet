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
    intro: "Mac 회의실 PC는 BlackHole로 회의 소리를 받아서 yeson-meet sidecar로 보냅니다.",
    steps: [
      {
        title: "BlackHole 설치",
        detail: "Mac에 BlackHole 2ch를 설치하고, 사운드 입력 장치 목록에 BlackHole이 보이는지 확인합니다.",
      },
      {
        title: "회의 소리를 BlackHole로 보내기",
        detail: "Google Meet/Zoom/Teams의 스피커를 BlackHole 또는 BlackHole이 포함된 Multi-Output Device로 맞춥니다.",
      },
      {
        title: "Live Meeting에서 회의 만들기",
        detail: "테스트 계정으로 로그인하고 Start meeting을 누른 뒤, Setup 탭으로 돌아와 Session ID와 Viewer URL이 채워졌는지 봅니다.",
      },
      {
        title: "zsh 명령 실행",
        detail: "Sidecar project folder를 yeson_meet 폴더로 지정하고, Device API Key를 붙여넣은 뒤 앱 버튼이나 zsh 명령으로 실행합니다.",
      },
    ],
    reminder: "Mac 기본 오디오 장치 이름은 (?i)blackhole입니다. Device API Key는 저장하지 않으니 오디오 테스트 직전에 다시 붙여넣으세요. 아직 Python sidecar는 앱에 완전 내장하지 않고 yeson_meet 폴더 경로로 실행합니다.",
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
