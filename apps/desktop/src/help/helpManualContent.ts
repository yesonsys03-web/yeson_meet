// === ANCHOR: HELP_MANUAL_CONTENT_START ===
import type { HelpSection } from "./types";

export const helpManualSections: HelpSection[] = [
  {
    id: "server-restart",
    eyebrow: "서버 PC",
    title: "서버 다시 시작하기",
    summary: "자막 서버 코드를 고쳤거나 Gemini 설정을 바꿨다면 서버 프로그램을 새 버전으로 다시 켭니다. 지금은 Docker지만, 나중에 Linux 서버로 바뀌어도 확인 방법은 같습니다.",
    steps: [
      {
        title: "1. 회의와 sidecar를 먼저 멈추기",
        body: "회의 중에 서버를 껐다 켜면 자막 연결이 끊깁니다. 테스트 회의를 종료하고 회의실 PC의 sidecar 터미널도 Ctrl+C로 멈춥니다.",
      },
      {
        title: "2. 서버 컨테이너 다시 빌드하기",
        body: "서버 PC의 yeson-meet 폴더에서 아래 명령을 실행합니다. 코드 변경까지 반영되는 가장 안전한 재시작입니다.",
        command: "docker compose --env-file .env -f deploy/docker-compose.yml up -d --build server",
      },
      {
        title: "3. 서버가 살아났는지 확인하기",
        body: "서버 종류와 상관없이 health 주소가 ok를 보여주면 정상입니다. Docker든 Linux 서비스든 먼저 이 주소로 살아 있는지 확인합니다.",
        command: "curl http://<server-address>/api/v1/health",
      },
      {
        title: "4. 로그 보기",
        body: "자막이 안 나오거나 Gemini 설정이 의심되면 서버 로그를 봅니다. 현재 Docker 테스트에서는 아래 명령을 쓰고, 나중에 Linux 서버에서는 운영자가 정한 서비스 로그 명령을 쓰면 됩니다.",
        command: "docker compose --env-file .env -f deploy/docker-compose.yml logs -f server",
      },
    ],
  },
  {
    id: "client-setup",
    eyebrow: "회의실 PC",
    title: "클라이언트 컴퓨터 준비하기",
    summary: "회의실 PC는 소리를 잡아서 서버로 보내는 역할입니다. 서버 주소, Device Key, Session ID가 맞아야 합니다.",
    steps: [
      {
        title: "1. 서버 주소 확인하기",
        body: "Mac 로컬 테스트는 ws://127.0.0.1:8000 을 씁니다. 다른 컴퓨터에서 붙는 LAN 테스트라면 서버 PC의 주소를 사용합니다.",
      },
      {
        title: "2. 회의 만들기",
        body: "Setup Assistant 첫 화면에서 로그인하고 회의를 만들면 Session ID와 Viewer URL이 자동으로 채워집니다.",
      },
      {
        title: "3. 소리 재생 장치 확인하기",
        body: "회의 소리를 기본 출력 장치로 재생하면 번들된 네이티브 sidecar가 자동으로 캡처합니다. 별도 가상 오디오 설치는 필요 없습니다.",
      },
      {
        title: "4. sidecar 실행하기",
        body: "Setup Assistant가 보여주는 실행 명령을 복사해서 회의실 PC 터미널에 붙여넣습니다. 자막이 끊기면 sidecar를 Ctrl+C로 멈춘 뒤 다시 실행합니다.",
      },
    ],
  },
  {
    id: "windows-voicemeeter",
    eyebrow: "Windows 회의실 PC",
    title: "Windows 네이티브 오디오 캡처",
    summary:
      "Windows 번들 앱은 네이티브 WASAPI sidecar로 기본 출력 장치의 소리를 직접 캡처합니다. Voicemeeter 등 가상 오디오 설치는 더 이상 필요 없습니다.",
    steps: [
      {
        title: "1. 기본 출력 장치 확인하기",
        body:
          "작업표시줄 우하단 스피커 아이콘을 우클릭하고 '사운드 설정'을 엽니다. 자막으로 내보낼 회의 소리가 재생되는 장치가 기본 출력 장치로 설정돼 있는지 확인합니다.",
      },
      {
        title: "2. sidecar 시작하기",
        body:
          "Setup Assistant에서 Device API Key를 붙여넣고 'Sidecar 시작' 버튼을 누릅니다. 번들된 네이티브 WASAPI sidecar가 기본 출력 장치의 소리를 자동으로 캡처합니다.",
      },
    ],
  },
  {
    id: "subtitle-check",
    eyebrow: "테스트 순서",
    title: "자막이 잘 나오는지 확인하기",
    summary: "서버, sidecar, viewer 세 군데를 순서대로 보면 어디서 막혔는지 빨리 찾을 수 있습니다.",
    steps: [
      {
        title: "1. 서버 상태",
        body: "health 확인이 실패하면 서버부터 다시 시작합니다. Gemini health가 configured인지도 확인합니다. 주소는 현재 서버 주소로 바꿔서 실행합니다.",
        command: "curl http://<server-address>/api/v1/health/ai",
      },
      {
        title: "2. sidecar 상태",
        body: "sidecar 터미널에 audio ws connected가 보이고, 말하거나 영상을 틀 때 chunk가 계속 올라가야 합니다.",
      },
      {
        title: "3. viewer 상태",
        body: "폰이나 노트북에서 Viewer URL을 엽니다. 자막이 너무 길게 쏟아지면 서버를 최신 코드로 다시 빌드했는지 확인합니다.",
      },
    ],
  },
];
// === ANCHOR: HELP_MANUAL_CONTENT_END ===
