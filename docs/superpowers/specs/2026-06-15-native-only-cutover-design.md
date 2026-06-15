# native-only 컷오버 (Phase 4-A) — 설계

- **상태**: 승인됨 (2026-06-15)
- **브랜치**: `topyeson`
- **선행**: Mac(ScreenCaptureKit) + Windows(WASAPI) 네이티브 캡처 모두 실기 E2E 검증 완료(Phase 1·2). Phase 3 캡처 UX(상태칩·RMS 무음·레벨미터) 완료.
- **상위 컨텍스트**: Phase 4 = A(native-only 컷오버) + B(macOS codesign·notarization) + C(Windows 서명). **B·C는 외부 인증서가 선행돼야 하는 별도 사이클**. 본 문서는 A만 다룬다.
- **관련**: `project_native_only_next_steps` 메모리, ROADMAP §5.2 / PRD §5.2.

## 1. 목적 / 결정

네이티브 캡처(Mac SCK / Win WASAPI)가 양 플랫폼 실기 검증을 끝냈으므로, 전이용으로 남겨둔
sounddevice(BlackHole/Voicemeeter) 경로를 코드·데스크톱 UX·문서에서 제거하고 "native가 유일
캡처 경로"로 못박는다.

**왜 제거(유지 아님)**: sounddevice 비상망은 BlackHole/Voicemeeter가 *미리 설치·라우팅*돼 있어야
작동 — 우리가 없애려는 그 셋업이라 실전 비상 시 사실상 무용. 네이티브 실패는 이미 actionable
배너(`NativeCaptureError`)로 시끄럽게 드러남. git 히스토리에 코드가 남아 필요 시 되살릴 수 있어
**저후회 결정**. (사용자 합의, 2026-06-15.)

**numpy는 유지**: 레벨미터·RMS 무음(`rms.py`)이 numpy를 쓴다. 제거 가능한 의존성은
`sounddevice`·`samplerate`뿐.

## 2. 범위

단일 응집 서브프로젝트(캡처 경로 + 데스크톱 setup UX + 문서). 단일 spec + plan.

## 3. 사이드카 — sounddevice 서브트리 삭제

`sounddevice_source → capture → resample / device` 는 자기완결 의존 서브트리라 통째로 제거 가능
(검증: `grep -rln "import sounddevice"` → `device.py`·`capture.py`만; `samplerate` → `resample.py`만;
`numpy` → `rms.py`·`resample.py`·`capture.py`+tests, 즉 `rms.py`가 numpy를 독립적으로 사용).

- **삭제 파일**: `apps/client_sidecar/audio/sources/sounddevice_source.py`,
  `apps/client_sidecar/audio/capture.py`, `apps/client_sidecar/audio/resample.py`,
  `apps/client_sidecar/audio/device.py`.
- **삭제 테스트**: `tests/test_sounddevice_source.py`, `tests/test_device_select.py`,
  `tests/test_resample.py`.
- **`apps/client_sidecar/pyproject.toml`**: `sounddevice>=0.5`, `samplerate>=0.2.1` 의존성 제거.
  `numpy>=2.1` 유지.

## 4. 사이드카 — factory / config 단순화

### 4.1 `audio/sources/factory.py`
`make_source()`는 항상 `NativePipeSource(bin_path=...)` 반환:
- `sounddevice`/`auto` 분기 + 그 lazy-import 제거.
- 헬퍼 바이너리 없으면 기존대로 `FileNotFoundError`(시끄럽게 실패 — 패키징 갭 노출).
- `YESON_AUDIO_PROVIDER`가 설정돼 있고 값이 `native`가 아니면 **경고 로그 후 native 강제**
  (조용한 무시 아님): 예) `logger.warning("YESON_AUDIO_PROVIDER=%s is removed; native is the only path", provider)`.
  `native`/미설정이면 조용히 native.

### 4.2 `config/audio.py`
- 제거: `DEVICE_NAME_REGEX`, `DEVICE_INDEX`(sounddevice 전용), `YESON_AUDIO_PROVIDER` 상수,
  `AUDIO_PROVIDER` 정책 주석 블록 전체. **결정**: provider 개념은 config에서 완전히 사라지고,
  factory가 경고 판정용으로 `os.environ.get("YESON_AUDIO_PROVIDER")`를 직접 읽는다(config에 vestigial
  상수를 남기지 않음).
- 유지: `NATIVE_HELPER_BIN_PATH`(+ 플랫폼 기본 경로), `TARGET_*`/`CHUNK_*`, `RMS_DBFS_THRESHOLD`,
  `RMS_SILENCE_GATE_ENABLED`.

### 4.3 영향받는 기존 테스트(삭제 아님, 갱신)
- `tests/test_source_factory.py`: native-only 동작으로 재작성(헬퍼 존재 시 NativePipeSource,
  없으면 FileNotFoundError, provider override 경고). sounddevice 분기 테스트 제거.
- `tests/test_config_audio_paths.py`: 제거된 상수 참조 정리, 헬퍼 경로 테스트 유지.
- `tests/test_audio_main_smoke.py`: sounddevice 가정 있으면 native-only로 정리.

## 5. 데스크톱 — setup UX를 native-first로

`audioDeviceName`은 sounddevice 전용 개념 → 데스크톱 값 모델·입력·검증·생성 명령에서 제거.

- **`setup/types.ts`**: `SetupValues`에서 `audioDeviceName` 필드 제거.
- **`setup/setupValues.ts`**: `audioDeviceName` 로드/기본/저장 제거.
- **`setup/platformConfig.ts`**: `audioDeviceName`·`audioDeviceHelp` 필드 + 값 제거.
  Windows 라벨/설명에서 "Voicemeeter" 문구 정리(native 캡처 기준).
- **`setup/SetupAssistant.tsx`**: `audioDeviceName` 입력 Field + 그 초기화 줄 제거.
- **`setup/sidecarCommands.ts`**: 생성 명령(zsh/PowerShell)에서 `YESON_AUDIO_DEVICE_NAME` 줄 제거.
- **`setup/sidecarRunner.ts` + `SidecarRunnerPanel.tsx`**: `audioDeviceName` 필수 검증 제거.
- **`setup/platformRunbook.ts`**: intro/steps/reminder를 native-first로 재기술 — Voicemeeter 설치·B1/A1
  라우팅 단계, BlackHole dev/fallback 노트 제거. "번들 네이티브 sidecar가 시스템 소리를 직접 캡처,
  별도 가상오디오 설치 불필요"가 양 플랫폼 공통.
- **`help/helpManualContent.ts`**: `windows-voicemeeter` 등 Voicemeeter/BlackHole 설치 섹션
  제거/재기술(native 기준 안내).

## 6. 문서

- **`docs/ROADMAP.md`**: §S2 산출물 / β-1 Voicemeeter 자동감지 항목을 "제거됨(native-only 컷오버),
  git 히스토리 보존"으로 재기술. 상단 native-track 노트에 컷오버 완료 줄 추가.
- **`docs/PRD.md`** §5.2: Windows+Voicemeeter / Mac+BlackHole 주 경로 서술을 native(SCK/WASAPI)
  정식 경로로 교체, 가상오디오는 제거된 레거시로 표기.

## 7. 테스트 / 검증

- 사이드카: `uv run pytest apps/client_sidecar/tests -q` 그린(삭제 3 + 갱신 후). factory native-only
  단위 검증(헬퍼 mock 존재/부재, provider override 경고).
- 데스크톱: `pnpm --filter @yeson-meet/desktop test` 그린 + `tsc --noEmit` 클린(필드 제거 타입 정합).
- 회귀: 캡처 상태칩·레벨미터 등 native 경로 기능 무영향(이들은 sounddevice와 무관).

## 8. 비범위

- **Rust `audio/voicemeeter_ffi.rs`·`bin/voicemeeter_dump.rs`**: 앱에 미탑재 dev 진단 바이너리.
  제거는 저우선 후속(이번은 캡처 경로 + 데스크톱 UX + 문서에 집중). 남겨도 출시 영향 없음.
- **B macOS codesign·notarization / C Windows 서명**: 외부 인증서 선행 필요, 별도 사이클.
- 캡처 소스 선택 UI(네이티브는 기본장치 고정이라 무의미), device 제거 자가치유.

## 9. 위험 / 주의

- 삭제 후 import 잔재(`from ...capture import`, `from ...device import` 등)가 없도록 grep 확인 필수.
- `NativePipeSource`/`win_job_object`/`native_pipe_source` 경로는 sounddevice와 무관 → 무영향.
- 데스크톱 `audioDeviceName` 제거 시 localStorage에 저장된 옛 값은 무시되면 그만(로드에서 필드 제거).
- 되돌리기: 전부 git 히스토리에 남음. 비상 시 revert 가능.
