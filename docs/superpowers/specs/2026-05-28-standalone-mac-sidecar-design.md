# Standalone Mac sidecar — PyInstaller 번들 + OS 신뢰저장소 TLS

> Design spec · 2026-05-28 · branch `topyeson`
> 선행: [[project_native_only_next_steps]] step 1(packaging seam) 완료. 본 작업은 그 step 1 노트의
> 선결과제 ①(PyInstaller)·②(CA 신뢰)를 해소해 **macOS standalone 배포**를 완성한다.

## 1. 목표 / 비목표

**목표.** 패키지 `.app`가 Python sidecar를 **repo · uv · PATH · 동봉 Caddy CA 에 전혀 의존하지 않고**
실행되는 진짜 standalone 을 만든다. 스모크 때 썼던 두 워크어라운드를 모두 제거한다:
- Setup 의 sidecar project dir = repo 지정 (dev uv+python 폴백)
- `SSL_CERT_FILE=…caddy root.crt` (사설 CA 신뢰)

**비목표 (이번 슬라이스 아님).**
- Windows sidecar 번들링 — Phase 2 WASAPI 묶음. 본 작업은 macOS 만.
- universal(arm64+x86_64) 바이너리 / codesign / notarization — β-5 막판 묶음.
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

## 3. 결정 사항 (2026-05-28 사용자 확정)

| 축 | 결정 | 근거 |
|----|------|------|
| **CA 신뢰** | **`truststore` (OS 신뢰저장소)** | webview 와 **동일한** 신뢰 소스(Keychain) 사용 → 회의실 PC 당 CA 1회 등록(ROADMAP "회의실 PC Root CA 신뢰 등록" 단계가 그대로 소비됨). Mac/Windows 동일 코드, CA 회전에도 무영향. cert 동봉/추적 불필요. |
| **번들 범위** | **Lean native-only** | `numpy·sounddevice·samplerate` 제외 → PortAudio/libsamplerate hook 불필요, 작고 단순. native-only 컷오버 방향과 일치. emergency fallback 은 dev(uv) 에서만. |

대안 비교(기록): CA 는 cert 주입(동봉·회전 추적 부담)·공인 인증서(공인 DNS 필요, LAN 모델과 상충)·평문 ws(암호화 회귀);
번들은 fat(numpy+PortAudio+libsamplerate 전부 동봉, hook 작업↑) — 모두 기각.

## 4. 변경 단위 (논리 커밋 4개 + housekeeping)

> 참고: `apps/desktop/src-tauri/binaries/` 는 **gitignore 대상**(추적 안 됨). externalBin staging 바이너리는
> `beforeBuildCommand` 가 매번 재생성하는 빌드 산출물이므로 **커밋 대상이 아님**.

### ① `feat(sidecar)`: sounddevice lazy import
`factory.py` 의 `from …sounddevice_source import SoundDeviceSource` 를 모듈 최상단에서 제거하고,
이를 사용하는 두 분기(`provider == "sounddevice"`, `auto` 폴백) **안쪽**으로 옮긴다.
`native_pipe_source` import 는 그대로(최상단) 둔다. native 경로가 `numpy·sounddevice·samplerate` 를
더 이상 끌고 오지 않는다. `tests/test_source_factory.py` 를 lazy import 에 맞게 갱신.
**경계:** `SOURCE_FACTORY` 앵커 내부만. import 구조 변경(허락된 범위)은 본 커밋에 한정.

### ② `build(sidecar)`: PyInstaller standalone
- `apps/client_sidecar/pyproject.toml` 에 `pyinstaller` 를 **dev 의존**으로 추가
  (런타임 의존 아님; 정확한 테이블은 repo 의 기존 pyproject 관례 따름 — `[dependency-groups]` / uv dev-deps).
- 신규 `apps/client_sidecar/scripts/build-sidecar.sh` — `native_helper_mac/scripts/build-release.sh` 패턴 미러:
  - repo root 에서 `uv run pyinstaller --onefile --name yeson-sidecar --paths .
    --exclude-module sounddevice --exclude-module samplerate --exclude-module numpy
    apps/client_sidecar/main.py` 실행.
  - host arch → triple 매핑(arm64→`aarch64-apple-darwin`, x86_64→`x86_64-apple-darwin`).
  - `dist/yeson-sidecar` → `apps/desktop/src-tauri/binaries/yeson-sidecar-{TRIPLE}` 복사(externalBin staging).
  - 실패 시 비-0 종료(바이너리 부재·PyInstaller 오류).
- `tauri.macos.conf.json`:
  - `externalBin` 에 `"binaries/yeson-sidecar"` 추가(helper 와 병기).
  - `beforeDevCommand`·`beforeBuildCommand` 앞에 `pnpm build:sidecar-mac &&` 추가.
- `apps/desktop/package.json`: `"build:sidecar-mac": "../client_sidecar/scripts/build-sidecar.sh"` 추가.

### ③ `feat(sidecar)`: OS 신뢰저장소 TLS via truststore
- `apps/client_sidecar/pyproject.toml` 에 `truststore` 를 **런타임 의존**으로 추가(순수 Python, 번들 trivial).
- `main.py::run()` 부트스트랩에서 `truststore.inject_into_ssl()` 1회 호출.
  → `websockets` 가 내부적으로 쓰는 `ssl.create_default_context()` 가 OS 신뢰저장소(Keychain) 기반이 됨.
  → `audio_ws.py`·`server_ws.py` **무수정**(최소 패치, 앵커/import 규칙 준수).
- **경계:** `MAIN_RUN` 앵커 내부 + 파일 상단 import 1줄.

### ④ housekeeping (커밋 아님): 낡은 Windows placeholder 제거
`apps/desktop/src-tauri/binaries/yeson-sidecar-x86_64-pc-windows-gnu.exe`(0바이트, triple 오류 — msvc 여야 함)
는 **untracked**(binaries/ gitignore). 단순 `rm` 으로 정리 — git 변경 없음, 커밋 불필요.
실제 Windows sidecar 빌드는 Phase 2 WASAPI. Mac 작업을 막지 않음.

### ④ `docs(native-audio)`: 문서 sync
ROADMAP β-5 "PyInstaller로 sidecar 단일 실행파일"·"사내 root CA 인증서 배포 자동화"(truststore 로 충족) 및
PRD 해당 항목 체크/주석. [[feedback_docs_after_slice]] 규칙(슬라이스 완료 → 같은 단계에서 문서 갱신).

## 5. 에러 처리

- `provider=native` 는 helper 부재 시 이미 `FileNotFoundError`(loud, 무음 격하 없음) — 번들 sidecar 가 그대로 상속.
- truststore 가 Keychain 에서 CA 를 못 찾으면 정상적인 cert 검증 오류로 TLS 실패 →
  기존 `sidecar:stderr` 로그 포워더(`sidecar.rs::spawn_output_forwarder`)에 표면화 →
  operator 를 "Root CA 신뢰 등록" 단계로 안내. (무음 실패 아님.)

## 6. 알려진 특성 (숨기지 않고 명시)

- **`--onefile`** 은 매 실행 시 tempdir 로 추출(~0.5–1s 시동 비용). 회의당 1회 기동인 sidecar 엔 무해;
  `externalBin` 이 단일 경로를 요구하므로 onefile 이 자연스러운 선택.
- **arch 종속:** arm64 에서 빌드 시 arm64-only 바이너리. universal(lipo) 은 β-5 codesign 으로 연기 —
  helper 스크립트의 동일 주석과 일치.
- **lean = 번들 내 sounddevice fallback 없음.** dev(uv) 에선 유지. native-only 컷오버 방향과 일치.

## 7. 검증

1. `uv run pytest apps/client_sidecar/tests` — factory lazy-import 동작 보존.
2. 빌드 후 바이너리를 **`/tmp` 에서, PATH 에 `uv` 없이** 실행 → repo/uv 무의존 확인.
3. `pnpm tauri build` → `.app` 의 `Contents/MacOS/yeson-sidecar` 포함 확인.
4. **Operator E2E (진짜 standalone 게이트):** Finder 에서 번들 `.app` 기동 → 첫 자막까지,
   `SSL_CERT_FILE` **없이**, dev project-dir **없이**. operator 실측 단계는 정확한 절차와 함께 핸드오프.

## 8. 영향 / 의존 파일

- 수정: `apps/client_sidecar/audio/sources/factory.py`, `apps/client_sidecar/main.py`,
  `apps/client_sidecar/pyproject.toml`, `apps/client_sidecar/tests/test_source_factory.py`,
  `apps/desktop/src-tauri/tauri.macos.conf.json`, `apps/desktop/package.json`,
  `docs/ROADMAP.md`, `docs/PRD.md`.
- 신규: `apps/client_sidecar/scripts/build-sidecar.sh`.
- 삭제(untracked, 커밋 아님): `apps/desktop/src-tauri/binaries/yeson-sidecar-x86_64-pc-windows-gnu.exe`.
- 빌드 산출물(gitignore, 커밋 아님): `apps/desktop/src-tauri/binaries/yeson-sidecar-{TRIPLE}`.
- **무수정(중요):** `sidecar.rs`(seam 이미 존재), `audio_ws.py`·`server_ws.py`(truststore 전역 주입).
