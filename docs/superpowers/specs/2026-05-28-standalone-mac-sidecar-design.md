# Standalone Mac sidecar — PyInstaller 번들 + OS 신뢰저장소 TLS

> Design spec · 2026-05-28 · branch `topyeson`
> 선행: [[project_native_only_next_steps]] step 1(packaging seam) 완료. 본 작업은 그 step 1 노트의
> 선결과제 ①(PyInstaller)·②(CA 신뢰)를 해소해 **macOS standalone local/internal beta 실행**을 완성한다.

## 1. 목표 / 비목표

**목표.** 패키지 `.app`가 Python sidecar를 **repo · uv · PATH · 동봉 Caddy CA 에 전혀 의존하지 않고**
실행되는 진짜 standalone 을 만든다. 스모크 때 썼던 두 워크어라운드를 모두 제거한다:
- Setup 의 sidecar project dir = repo 지정 (dev uv+python 폴백)
- `SSL_CERT_FILE=…caddy root.crt` (사설 CA 신뢰)

**비목표 (이번 슬라이스 아님).**
- Windows sidecar 번들링 — Phase 2 WASAPI 묶음. 본 작업은 macOS 만.
- universal(arm64+x86_64) 바이너리 / codesign / notarization — β-5 막판 묶음. 따라서 이번 산출물은 **local/internal beta only** 이며, 외부 배포용 `.app` 로 간주하지 않는다.
- 번들 앱 내 sounddevice emergency fallback — native-only 컷오버 방향에 따라 dev(uv) 에서만 유지.

## 2. 현재 상태 (확인됨)

- Rust seam 이미 존재: `apps/desktop/src-tauri/src/sidecar.rs::locate_bundled_sidecar()` 가 exe 옆의
  `yeson-sidecar-{triple}` 를 찾아 **직접 실행**하고, 없으면 `uv+python` dev 폴백으로 내려간다.
  → 본 작업은 **그 바이너리를 생산·동봉**하기만 하면 휴면 중인 bundled 경로가 활성화된다. **Rust 변경 불필요.**
- `tauri.macos.conf.json` 은 helper(`binaries/yeson-mac-audio-helper`)만 `externalBin` 에 올림 — sidecar 누락.
- `tauri.windows.conf.json` 은 이미 `binaries/yeson-sidecar` 를 선언(Phase 2 용).
- 서버는 Caddy `tls internal`(사설 CA) → sidecar 의 `websockets.connect(wss://…)` 가 기본 신뢰저장소로
  검증 실패. (transport 2곳 `audio_ws.py`·`server_ws.py` 모두 `ssl=` 인자 없이 connect.)
- `factory.py` 가 `native_pipe_source` 와 `sounddevice_source` 를 **모듈 최상단에서 eager import**.
  `sounddevice_source → capture → sounddevice·resample(samplerate)·numpy`. 즉 native 경로도 현재는
  무거운 native-lib 의존을 끌고 온다.
- `main.py` 에 `if __name__ == "__main__": run()` 가드 존재 → PyInstaller 진입점으로 `main.py` 직접 지정 가능
  (절대 import `apps.client_sidecar.*` 때문에 `pathex` 에 repo root 포함 필요).
- `main.py` 는 현재 `sidecar audio mode → ... url=<wss://...?key=...>` 를 stdout 에 출력하고, Rust
  `sidecar.rs::spawn_output_forwarder()` 가 이를 app log 로 전달한다. standalone 실측 전 **query key 로그 누출 제거**가 필요하다.
- macOS 지원 범위 불일치가 있다: Tauri app 은 `minimumSystemVersion: "11.0"`, native helper 는
  `Package.swift` 에서 macOS **14.2+**. 이번 slice 는 native-only lean 이므로 14.2 미만은 명확한 preflight failure 또는 app 최소 버전 상향이 필요하다.
- Setup UI 문구는 아직 Mac=BlackHole, `uv`/project folder 중심이다. standalone native-only operator UX 와 맞지 않는다.

## 3. 결정 사항 (2026-05-28 사용자 확정)

| 축 | 결정 | 근거 |
|----|------|------|
| **CA 신뢰** | **`truststore` (OS 신뢰저장소)** | webview 와 **동일한** 신뢰 소스(Keychain) 사용 → 회의실 PC 당 CA 1회 등록(ROADMAP "회의실 PC Root CA 신뢰 등록" 단계가 그대로 소비됨). Mac/Windows 동일 코드, CA 회전에도 무영향. cert 동봉/추적 불필요. |
| **번들 범위** | **Lean native-only** | `numpy·sounddevice·samplerate` 제외 → PortAudio/libsamplerate hook 불필요, 작고 단순. native-only 컷오버 방향과 일치. emergency fallback 은 dev(uv) 에서만. |
| **macOS floor** | **packaged `minimumSystemVersion` 11.0 → 14.2 상향** | helper 가 14.2+(ScreenCaptureKit). **lean 번들엔 sounddevice fallback 이 없어** <14.2 는 동작 오디오 경로가 0 → 설치 차단이 dead-install 보다 낫다. dev(`uv`/`tauri dev`)는 무영향. |
| **PyInstaller 범위** | **runtime module 만 포함** | `--collect-submodules apps.client_sidecar` 는 tests 와 sounddevice 계열을 끌어올 수 있어 lean 의도와 충돌. 기본 import analysis + 필요한 hidden import 만 사용한다. |

대안 비교(기록): CA 는 cert 주입(동봉·회전 추적 부담)·공인 인증서(공인 DNS 필요, LAN 모델과 상충)·평문 ws(암호화 회귀);
번들은 fat(numpy+PortAudio+libsamplerate 전부 동봉, hook 작업↑) — 모두 기각.

## 4. 변경 단위 (논리 커밋 6개 + housekeeping)

> 참고: `apps/desktop/src-tauri/binaries/` 는 **gitignore 대상**(추적 안 됨). externalBin staging 바이너리는
> `beforeBuildCommand` 가 매번 재생성하는 빌드 산출물이므로 **커밋 대상이 아님**.

### ① `feat(sidecar)`: sounddevice lazy import
`factory.py` 의 `from …sounddevice_source import SoundDeviceSource` 를 모듈 최상단에서 제거하고,
이를 사용하는 두 분기(`provider == "sounddevice"`, `auto` 폴백) **안쪽**으로 옮긴다.
`native_pipe_source` import 는 그대로(최상단) 둔다. native 경로가 `numpy·sounddevice·samplerate` 를
더 이상 끌고 오지 않는다. `tests/test_source_factory.py` 를 lazy import 에 맞게 갱신.
**경계:** `SOURCE_FACTORY` 앵커 내부만. import 구조 변경(허락된 범위)은 본 커밋에 한정.

### ② `fix(sidecar)`: startup 로그 secret 제거
`?key=<device key>` 가 app log(→export)로 새는 곳 **3군데**를 한 커밋에서 모두 redact 한다:
`main.py:53`(`audio_main`, standalone 경로)·`main.py:36`(`fixture_main`)·`server_ws.py:26`
(`logger.info("connected to %s", url)`). `path-only + ?key=<redacted>` 로 출력
(`audio_ws._safe_url` 포맷 재사용; 인라인 중복은 최소 패치 의도). `audio_ws` 는 이미 redact 됨 → 무수정.
이 변경은 standalone smoke 전에 선행한다.

### ③ `build(sidecar)`: PyInstaller standalone
- `apps/client_sidecar/pyproject.toml` 에 `pyinstaller` 를 **dev 의존**으로 추가
  (런타임 의존 아님; 정확한 테이블은 repo 의 기존 pyproject 관례 따름 — `[dependency-groups]` / uv dev-deps).
- 신규 `apps/client_sidecar/scripts/build-sidecar.sh` — `native_helper_mac/scripts/build-release.sh` 패턴 미러:
  - repo root 에서 `uv run --project apps/client_sidecar pyinstaller --onefile --name yeson-sidecar --paths .
    --collect-submodules truststore
    --exclude-module sounddevice --exclude-module samplerate --exclude-module numpy
    apps/client_sidecar/main.py` 실행.
  - **금지:** `--collect-submodules apps.client_sidecar` 사용 — `tests/__init__.py` 존재로 tests(+pytest/sounddevice)까지 번들됨.
  - **필요:** `--collect-submodules truststore` — 플랫폼 백엔드(`truststore._macos`)가 런타임 import 라 plain analysis 가 놓칠 수 있음(좁고 tests 없음 → 안전).
  - host arch → triple 매핑(arm64→`aarch64-apple-darwin`, x86_64→`x86_64-apple-darwin`).
  - `dist/yeson-sidecar` → `apps/desktop/src-tauri/binaries/yeson-sidecar-{TRIPLE}` 복사(externalBin staging).
  - 실패 시 비-0 종료(바이너리 부재·PyInstaller 오류).
- `tauri.macos.conf.json`:
  - `externalBin` 에 `"binaries/yeson-sidecar"` 추가(helper 와 병기).
  - `beforeDevCommand`·`beforeBuildCommand` 앞에 `pnpm build:sidecar-mac &&` 추가.
- `apps/desktop/package.json`: `"build:sidecar-mac": "../client_sidecar/scripts/build-sidecar.sh"` 추가.

### ④ `feat(sidecar)`: OS 신뢰저장소 TLS via truststore
- `apps/client_sidecar/pyproject.toml` 에 `truststore` 를 **런타임 의존**으로 추가(순수 Python, 번들 trivial).
- `main.py::run()` 부트스트랩에서 `truststore.inject_into_ssl()` 1회 호출.
  → `websockets` 가 내부적으로 쓰는 `ssl.create_default_context()` 가 OS 신뢰저장소(Keychain) 기반이 됨.
  → `audio_ws.py`·`server_ws.py` **무수정**(최소 패치, 앵커/import 규칙 준수).
- **경계:** `MAIN_RUN` 앵커 내부 + 파일 상단 import 1줄.

### ⑤ `feat(desktop)`: standalone Mac UX / OS gate
- Setup UI 문구에서 Mac 기본 경로를 BlackHole/uv 중심에서 **bundled native sidecar** 중심으로 바꾼다.
- `Sidecar project folder` 는 dev fallback 전용임을 표시하거나 packaged app 에서는 숨긴다.
- `tauri.conf.json` `bundle.macOS.minimumSystemVersion` 을 `14.2` 로 상향(설치 단계 차단). preflight error copy 는 선택적 polish.

### ⑥ housekeeping (커밋 아님): 낡은 Windows placeholder 제거
`apps/desktop/src-tauri/binaries/yeson-sidecar-x86_64-pc-windows-gnu.exe`(0바이트, triple 오류 — msvc 여야 함)
는 **untracked**(binaries/ gitignore). 단순 `rm` 으로 정리 — git 변경 없음, 커밋 불필요.
실제 Windows sidecar 빌드는 Phase 2 WASAPI. Mac 작업을 막지 않음.

### ⑦ `docs(native-audio)`: 문서 sync
ROADMAP β-5 "PyInstaller로 sidecar 단일 실행파일"은 **macOS local/internal beta seam 완료**로 주석 처리한다.
"사내 root CA 인증서 배포 자동화"는 완료 처리하지 않는다. `truststore` 는 CA **소비 경로**를 OS trust store 로 바꾸는 것이며,
회의실 PC Keychain 에 Caddy root CA 를 등록하고 SSL 에 대해 Always Trust 하는 operator 절차는 별도로 남는다.
PRD/ROADMAP/기존 native-audio plan 에 이 범위를 명시한다.

## 5. 에러 처리

- `provider=native` 는 helper 부재 시 이미 `FileNotFoundError`(loud, 무음 격하 없음) — 번들 sidecar 가 그대로 상속.
- truststore 가 Keychain 에서 CA 를 못 찾으면 cert 검증 오류가 난다. 단, 현재 `audio_ws.py` 는 `OSError` 계열을
  reconnect loop 로 처리할 수 있으므로, operator 가 "Root CA 신뢰 등록" 문제로 알아볼 수 있는 로그/테스트를 추가한다.

## 6. 알려진 특성 (숨기지 않고 명시)

- **`--onefile`** 은 매 실행 시 tempdir 로 추출(~0.5–1s 시동 비용). 회의당 1회 기동인 sidecar 엔 무해;
  `externalBin` 이 단일 경로를 요구하므로 onefile 이 자연스러운 선택.
- **arch 종속:** arm64 에서 빌드 시 arm64-only 바이너리. universal(lipo) 은 β-5 codesign 으로 연기 —
  helper 스크립트의 동일 주석과 일치.
- **lean = 번들 내 sounddevice fallback 없음.** dev(uv) 에선 유지. native-only 컷오버 방향과 일치.

## 7. 검증

1. `uv run pytest apps/client_sidecar/tests` — factory lazy-import 동작 보존.
2. PyInstaller 빌드는 clean env 에서 `uv run --project apps/client_sidecar pyinstaller ...` 로 재현 가능해야 한다.
3. 빌드 후 바이너리를 **`/tmp` 에서, PATH 에 `uv` 없이** 실행 → repo/uv 무의존 확인.
4. `pnpm tauri build` → `.app` 의 `Contents/MacOS/yeson-sidecar` 포함 확인.
5. app log 에 `YESON_DEVICE_API_KEY` 또는 `?key=` 가 남지 않는지 확인.
6. macOS 14.2 미만 gate(또는 minimumSystemVersion 상향)가 기대대로 동작하는지 확인.
7. **Operator E2E (진짜 standalone 게이트):** Finder 에서 번들 `.app` 기동 → 첫 자막까지,
   `SSL_CERT_FILE` **없이**, dev project-dir **없이**. operator 실측 단계는 정확한 절차와 함께 핸드오프.

## 8. 영향 / 의존 파일

- 수정: `apps/client_sidecar/audio/sources/factory.py`, `apps/client_sidecar/main.py`,
  `apps/client_sidecar/pyproject.toml`, `apps/client_sidecar/tests/test_source_factory.py`,
  `apps/client_sidecar/tests/test_audio_main_smoke.py`, `apps/client_sidecar/tests/test_tls_bootstrap.py`,
  `apps/desktop/src-tauri/tauri.macos.conf.json`, `apps/desktop/package.json`,
  `apps/desktop/src/setup/platformConfig.ts`, `apps/desktop/src/setup/SidecarRunnerPanel.tsx`,
  `docs/ROADMAP.md`, `docs/PRD.md`, `docs/plans/2026-05-27-native-audio.md`.
- 신규: `apps/client_sidecar/scripts/build-sidecar.sh`.
- 삭제(untracked, 커밋 아님): `apps/desktop/src-tauri/binaries/yeson-sidecar-x86_64-pc-windows-gnu.exe`.
- 빌드 산출물(gitignore, 커밋 아님): `apps/desktop/src-tauri/binaries/yeson-sidecar-{TRIPLE}`.
- **무수정(중요):** `sidecar.rs`(seam 이미 존재), `audio_ws.py`·`server_ws.py`(truststore 전역 주입).
