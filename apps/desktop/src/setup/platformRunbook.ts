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
    title: "Mac에서 자막 켜는 순서",
    intro: "Mac 앱은 BlackHole 같은 가상 오디오를 따로 깔 필요 없이, 스피커로 나오는 회의 소리를 그대로 잡아 번역 자막으로 보냅니다.",
    steps: [
      {
        title: "화면 기록 권한 켜기",
        detail: "시스템 설정 → 개인정보 보호 및 보안 → 화면 기록에서 yeson-meet를 켭니다. 이 권한이 있어야 회의 소리를 자막으로 바꿀 수 있어요. (앱을 새로 설치·업데이트하면 다시 켜야 할 수 있습니다.)",
      },
      {
        title: "회의 소리를 평소 스피커로 재생",
        detail: "자막으로 내보낼 소리가 Mac 기본 출력(스피커·이어폰)으로 나오게 두세요. 출력 장치를 바꿔도 그 장치를 따라갑니다.",
      },
      {
        title: "맨 위에서 '회의 시작' 누르기",
        detail: "맨 위 빠른 시작에서 회의 시작을 누르면 자막 송출이 자동으로 켜집니다. 따로 실행할 프로그램이나 터미널 명령은 없습니다.",
      },
    ],
    reminder: "상대방 목소리(스피커로 들리는 소리)만 자막이 됩니다. 같은 방에서 마이크로 말하는 우리 쪽 목소리는 자막에 나오지 않아요.",
  },
  windows: {
    title: "Windows에서 자막 켜는 순서",
    intro: "Windows 회의실 PC는 Voicemeeter 같은 가상 오디오를 따로 깔 필요 없이, 스피커로 나오는 회의 소리를 그대로 잡아 번역 자막으로 보냅니다.",
    steps: [
      {
        title: "회의 소리를 평소 스피커로 재생",
        detail: "자막으로 내보낼 소리가 Windows 기본 출력 장치로 재생되게 두세요. 출력 장치를 바꿔도 그 장치를 따라갑니다.",
      },
      {
        title: "맨 위에서 '회의 시작' 누르기",
        detail: "맨 위 빠른 시작에서 회의 시작을 누르면 자막 송출이 자동으로 켜집니다. 따로 실행할 프로그램이나 명령은 없습니다.",
      },
    ],
    reminder: "상대방 목소리(스피커로 들리는 소리)만 자막이 됩니다. 같은 방에서 마이크로 말하는 우리 쪽 목소리는 자막에 나오지 않아요.",
  },
};
// === ANCHOR: PLATFORM_RUNBOOK_END ===
