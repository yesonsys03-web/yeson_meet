# Windows WASAPI 네이티브 오디오 헬퍼 — 설계 (Phase 2)

> 목적: Voicemeeter 없이 Windows 시스템 오디오를 캡처하는 네이티브 헬퍼를 추가한다.
> macOS ScreenCaptureKit 헬퍼(Phase 1)와 **동일한 stdout PCM 파이프 계약**을 만족시켜,
> 캡처 레이어 밖 공통 코드(`NativePipeSource`, sidecar 전송, 서버/Gemini/viewer)를 그대로 재사용한다.
> 상위 기획: `docs/NATIVE_DESKTOP_HELPER_PLAN.md` §4·§5(Phase 2). 실행 트랙: `docs/plans/2026-05-27-native-audio.md`.

---

## 1. 결정 요약

| 항목 | 결정 | 근거 |
|---|---|---|
| 구현 구조 | **Rust 독립 바이너리** (`apps/native_helper_win`) | Mac 헬퍼와 동일 아키텍처. `NativePipeSource` 무변경 재사용. lean PyInstaller sidecar 유지. Tauri에 Rust 이미 존재 |
| 캡처 API | **WASAPI loopback** (cpal 0.15+) + Task 0 spike gate | plan §4.1 우선 후보. 단, Windows 실기에서 cpal loopback 계약을 먼저 증명해야 구현 리스크가 낮아짐 |
| 리샘플 | `rubato` (임의 비율) | 소스 mix format이 48k/44.1k/96k로 가변 |
| 측정 범위 | 1차: 시스템 기본 출력 loopback. 기본장치 변경 자동 추적은 Phase 2b | Mac PoC 범위와 동일하게 먼저 stdout PCM 계약을 닫고, device restart 정책은 후속으로 분리 |
| 빌드/검증 | 1차: `uv` sidecar + helper.exe E2E. Tauri packaged app wiring은 Phase 2b | 헬퍼 PoC 성공 전 packaged 범위를 섞지 않음 |
| 지원 범위 | Windows 10/11 **x86_64** (`x86_64-pc-windows-msvc`) | Windows arm64/MSIX/MSI/code signing은 별도 트랙 |
| OS-agnostic seam | **stdout PCM 계약** (Swift 프로토콜 공유 아님) | `NATIVE_DESKTOP_HELPER_PLAN.md` §4 정합 노트(2026-05-28) |

핵심: **Windows .exe가 640바이트(16kHz mono s16le) 청크를 stdout으로, JSON 이벤트를 stderr로 내보내면 끝.**
PoC에서는 Python 측은 `config/audio.py`의 dev 기본 경로 분기 외에 변경하지 않는다. Tauri packaged app wiring은 §5에 별도 Phase 2b로 둔다.

---

## 2. 계약 (Mac 헬퍼와 동일)

`apps/native_helper_mac` 의 `IPC` / `AudioContract` 를 그대로 따른다.

- **stdout**: PCM 바이너리 스트림. 640바이트 = 320 samples × 2 bytes, 16kHz mono Int16 **little-endian**, 20ms 프레임.
- **stderr**: 한 줄당 JSON 객체 `{"event": <name>, "payload": {...}}` + `\n`.

소비자(`apps/client_sidecar/audio/sources/native_pipe_source.py`):
- stdout 을 `readexactly(640)` 으로 읽어 청크 yield.
- stderr JSON 을 INFO 로깅, `event == "fatal"` 이면 `payload.reason` 을 `NativeCaptureError(reason)` 으로 surface.

→ **소비자 코드 변경 불필요.**

---

## 3. 이벤트 프로토콜 (Windows 적응)

```
starting       {"version": "<helper version>"}
started        {"device": "<name>", "source_sample_rate": 48000, "source_channels": 2}
               # 캡처 소스(장치 mix format) 정보. stdout 출력은 항상 16k mono(§2 계약)
device_changed {"from": "<old>", "to": "<new>"}      # Phase 2b. 변경 감지 시 stream 재시작 후 emit
fatal          {"reason": "<code>", "detail": "<msg>"}
stopping       {}
```

`fatal.reason` 코드:
- `no_default_render_device` — 기본 출력 장치 없음
- `wasapi_init_failed` — IAudioClient/loopback 초기화 실패
- `unsupported_format` — mix format 을 16k mono 로 변환 불가
- `stream_error` — 캡처 중 스트림 오류

**권한 이벤트 없음**: WASAPI loopback 은 권한이 불필요하다. Mac 의 `permission_required` / `permission_status` / `fatal:permission_denied` 는 Windows 에서 발생하지 않는다. 헬퍼는 `starting` → `started` 로 바로 진행한다. Python 은 `fatal.reason` 을 opaque 하게 다루므로 호환되며, PoC UI 는 raw reason 표시를 허용한다. 제품화용 Windows 전용 문구 매핑은 Phase 3 산출물이다.

종료코드: `0` 정상, `4` start_failed 계열(start 실패 시 `fatal` emit 후 exit). 종료코드는 정보성이며 Python 판정은 `fatal.reason` 문자열만 사용한다.

**fatal 계약은 필수**: start/init 실패, stream error, format 변환 실패는 모두 `fatal` JSON 을 stderr 에 flush 한 뒤 non-zero exit 해야 한다. fatal 없이 stdout 이 닫히면 `NativePipeSource` 가 정상 종료처럼 해석한다(`native_pipe_source.py` 는 `_failure_reason` 이 없으면 `IncompleteReadError` 를 정상 EOF 로 처리). 따라서 모든 실패 경로가 fatal 로 수렴해야 한다.

**panic 전파**: worker thread 의 panic 은 기본값에선 해당 스레드만 죽이고 프로세스를 종료하지 않는다(stdout 정지 → fatal 누락). 따라서 release 프로파일에 **`panic = "abort"`** 를 설정해 panic hook(= `fatal` JSON flush) 실행 후 프로세스를 non-zero 로 abort 시킨다. (대안: main 이 worker join 을 감시해 죽으면 fatal+exit. 1차는 `panic = "abort"` 채택.)

**broken pipe = 부모 종료 → 즉시 exit**: stdout write 가 `ERROR_BROKEN_PIPE`(부모 sidecar 가 사라짐)를 받으면 fatal 로 시끄럽게 내지 않고(읽을 부모가 없음) 헬퍼도 즉시 종료한다. 이는 고아 프로세스(§8)의 최저비용 방지책이며 **1차 범위에 포함**한다.

---

## 4. 모듈 구조 — `apps/native_helper_win/`

Mac 헬퍼(Kit lib + exec 분리, App.swift `@main` 유지)를 미러링한다. 순수 로직(리샘플·프레이밍·이벤트 직렬화·장치변경 결정)은 라이브러리 모듈로 분리해 **Mac 에서도 `cargo test` 로 검증 가능**하게 하고, WASAPI 캡처만 Windows 전용 얇은 셸로 둔다.

```
apps/native_helper_win/
  Cargo.toml
  src/
    main.rs          # 진입: starting → capture init → started → worker 루프 → 종료(stopping/fatal)
    ipc.rs           # stdout PCM writer + stderr JSON 이벤트 emitter (Swift IPC 미러)
    pcm.rs           # interleaved f32/i16 @소스레이트/채널 → 16k mono i16le → 640B 프레이밍 (rubato)
    capture.rs       # cpal WASAPI loopback: 기본 출력 장치를 loopback 입력으로 열어 raw frame 콜백
    device_watch.rs  # Phase 2b: default_output_device() id/name 폴링 → 변경 시 재시작 + 이벤트
  scripts/
    build-release.ps1  # cargo build --release → src-tauri/binaries 로 target-triple 접미사 복사
```

| 모듈 | 책임 | 테스트 |
|---|---|---|
| `main.rs` | 라이프사이클 오케스트레이션, capture callback channel drain, worker thread, 종료 처리 | — |
| `ipc.rs` | 바이트 싱크(stdout/stderr) 추상화 + JSON 직렬화 | Mac: 버퍼 싱크로 출력 형태 검증 |
| `pcm.rs` | 다운믹스(stereo→mono 평균), 리샘플(소스→16k), 640B 프레이밍, 잔여 버퍼 carry-over | Mac: 합성 입력으로 길이/프레이밍/값 검증 |
| `capture.rs` | cpal 기본 출력 loopback 스트림, callback 에서 raw frame 을 bounded channel 에 enqueue | Windows: 스모크(콜백 ≥1회) |
| `device_watch.rs` | Phase 2b 변경 감지 결정 로직(이전 id/name vs 현재) | Mac: 결정 함수 단위테스트 |

크레이트: `cpal` 0.15+ (WASAPI loopback), `rubato` (리샘플), `serde_json`(이벤트). cpal loopback 이 부족하면(예: 무음 무패킷 외 제약 발견) raw `windows` 또는 `wasapi` 크레이트로 폴백한다. 이 판단은 Task 0 spike 결과로 확정하며, spike 실패 후 본 구현을 계속 진행하지 않는다.

> 📌 **독립 Cargo 프로젝트**: Mac 헬퍼가 SwiftPM 독립 패키지인 것과 동일하게, `apps/native_helper_win` 은 자체 `Cargo.toml`/`Cargo.lock` 을 가진 **독립 크레이트**(src-tauri 워크스페이스 멤버 아님). 헬퍼는 Windows 전용이고 src-tauri 는 host 빌드라 의존성을 분리한다.

> 📌 **모듈 격리 규칙 (Mac 테스트 가능성 전제)**: `pcm.rs` / `ipc.rs` / `device_watch.rs` 는 **cpal 타입을 import 하지 않는다.** cpal 사용은 `capture.rs` 와 Windows 진입부에만 가둔다. `pcm.rs` 는 `&[f32]`/`&[i16]` 슬라이스 + sample_rate/channels 정수만 받고, `device_watch.rs` 결정 로직은 장치 id/name 문자열만 받는다. `main.rs` 는 `cfg(windows)` 진입부와 비-Windows 테스트용 stub 을 분리한다. 이 규칙이 깨지면 Mac `cargo test` 가 불가능해진다(WASAPI-gated 코드가 비-Windows 타겟에서 빌드 실패).

> 📌 **cpal loopback 메커니즘**: cpal 0.15+ 에서 **출력 장치에 대해 입력 스트림을 열면** WASAPI loopback 이 된다(`Device::build_input_stream` on output device). 장치 열거·기본장치 조회도 cpal 로 처리.

> 📌 **callback 안전 규칙**: cpal/WASAPI callback 안에서는 resample, JSON emit, stdout write/flush 를 하지 않는다. callback 은 raw frames + timestamp/format metadata 를 bounded channel 로 넘기고 즉시 반환한다. 별도 worker thread 가 channel 을 drain 하면서 `pcm.rs` 변환, 640B framing, stdout write/flush 를 수행한다. channel overflow 는 `stream_error` fatal 로 surface 하거나 drop 정책을 명시해야 하며, 조용히 무시하지 않는다.

---

## 5. 패키징 / 와이어링 (Tauri, Phase 2b)

본 섹션은 **PoC 성공 후 packaged Windows app 으로 승격할 때** 적용한다. 1차 PoC/E2E 는 §6처럼 `uv` sidecar + `YESON_NATIVE_HELPER_BIN=<helper.exe>` 로 검증하며, `tauri.windows.conf.json` / `externalBin` 변경은 성공 조건에 포함하지 않는다.

`sidecar.rs:236-237` 이 이미 "Windows WASAPI helper (Phase 2) will hook into the same dispatch" 라고 예고한 지점에 끼워넣는다.

1. **`apps/desktop/src-tauri/src/sidecar.rs::locate_bundled_native_helper()`**
   현재 macOS arm 만 존재(arch별 triple). Windows x86_64 추가:
   - `("yeson-win-audio-helper", "x86_64-pc-windows-msvc")`
   - `.exe` 접미사 처리 추가 (`locate_bundled_sidecar()` 패턴 차용).
   `add_native_helper_env()` 는 무변경 — 헬퍼를 찾으면 `YESON_NATIVE_HELPER_BIN` + `YESON_AUDIO_PROVIDER=native` 주입(이미 OS 무관).

2. **`apps/desktop/src-tauri/tauri.windows.conf.json`**
   - `externalBin` 에 `binaries/yeson-win-audio-helper` 추가 (현재 `binaries/yeson-sidecar` 만 존재).
   - `beforeBuildCommand` / `beforeDevCommand` 에 헬퍼 빌드 단계 추가(`pnpm build:native-helper-win`). Mac conf 패턴 미러.

3. **`package.json`**
   - `build:native-helper-win` 스크립트: cargo build --release → `src-tauri/binaries/yeson-win-audio-helper-x86_64-pc-windows-msvc.exe` 복사. Mac `apps/native_helper_mac/scripts/build-release.sh` 미러.

4. **`apps/client_sidecar/config/audio.py` — `NATIVE_HELPER_BIN_PATH`**
   현재 기본값이 macOS 경로 하드코딩. dev CLI 실행용으로 `sys.platform` 분기 추가(앵커 `AUDIO_PROVIDER` 내 최소 수정). 릴리스는 Tauri 주입이 우선이라 영향 없음.

> Python `NativePipeSource` / `factory.make_source()` 는 **변경 없음** — 이미 OS 무관.

---

## 6. 검증 전략

- **Task 0 cpal loopback spike** (Windows 실기, 본 구현 전 gate):
  - default output device 에 `build_input_stream` 을 열어 loopback callback 이 호출되는지 확인.
  - 브라우저/플레이어 오디오 재생 중 첫 640B-equivalent raw data 확보, source sample rate/channels/sample format 기록.
  - 무음/재생 중단 시 callback cadence 를 관찰하고, PoC 허용 범위(무음 무패킷 허용)를 확인.
  - 실패 시 `cpal` 본 구현 중단 후 raw `windows`/`wasapi` 크레이트 설계로 전환.
- **Rust 단위테스트** (Mac + Windows, `cargo test`):
  - `pcm`: 합성 사인파/임펄스로 44.1k/48k/96k, f32/i16/u16, mono/stereo/multi-channel → 16k mono 변환 길이·프레이밍(640B 정확), carry-over, endian, 장시간 cadence/drift 정확성.
  - `ipc`: 이벤트 JSON 형태(`{"event":...,"payload":...}` + 개행), PCM 바이트 패스스루.
  - `device_watch`(Phase 2b): 기본장치 id/name 변경 → 재시작 트리거 결정.
- **Rust 스모크** (Windows): 기본 출력 loopback 스트림 열어 콜백 ≥1회, 첫 640B 청크 확보.
- **E2E** (Windows, sidecar 는 `uv` 직접 실행):
  - `YESON_AUDIO_PROVIDER=native` + `YESON_NATIVE_HELPER_BIN=<helper.exe>` 로 sidecar 기동.
  - Zoom/Teams/브라우저 영상 재생 → sidecar/server `audio_stats` 또는 raw receive log 기준 **~50 chunks/sec** 수신 → viewer 한국어 자막.
  - sidecar 정상 종료/강제 종료 후 `yeson-win-audio-helper.exe` 잔존 없음 확인. 잔존하면 PoC 통과는 가능하되 Phase 2b Job Object/cleanup blocker 로 기록.
  - **Phase 2 성공기준 그대로**(plan §5): Voicemeeter 없이 자막 생성, 수동 라우팅 불필요, 공통 코드 동일 경로 재사용.
- **(선택) 사전 타입 검증** (Mac): `cargo check --target x86_64-pc-windows-msvc` (cargo-xwin) 로 Windows 없이 컴파일 오류 조기 포착.

---

## 7. 명시적 비범위 (이번 슬라이스 제외)

- **PyInstaller Windows sidecar 번들** — 별도 패키징 슬라이스. 본 E2E 는 `uv` sidecar 로 검증(헬퍼 PoC 에 번들 불필요). ROADMAP "Windows sidecar ⏳ Phase 2" 는 후속.
- **Tauri packaged Windows app wiring** — §5에 Phase 2b 체크리스트로 보존하되, 1차 PoC 성공 조건에서는 제외.
- **기본장치 변경 자동 추적** — stream restart/gap/중복 이벤트 정책이 필요하므로 Phase 2b. 1차는 프로세스 재시작으로 새 기본장치를 반영한다.
- **Windows 코드사인 / 인스톨러(MSI)** — Phase 4.
- **데스크톱 UI**: 장치 선택기, 레벨 미터, Windows 전용 실패 배너 문구 — Phase 3. (배너는 현재 `fatal.reason` 을 일반적으로 표시.)
- **앱별(프로세스) 오디오 분리 캡처** — 향후. 1차는 전체 시스템 오디오.
- **Windows 고아 프로세스 정리(Job Object)** — 견고성 트랙. 본 슬라이스 E2E 에서 동작/잔존 여부 확인하고 리스크로 플래그.

---

## 8. 주요 리스크

| 리스크 | 영향 | 대응 |
|---|---|---|
| **Rust stdout 버퍼링** (Windows 파이프) | 헬퍼는 살아있는데(CPU 활성) sidecar 가 청크를 못 받아 cadence 붕괴 — Windows 전용 증상(Mac `FileHandle.write` 는 무버퍼) | 매 640B write 후 `stdout().lock()` + 명시적 `flush()`, 또는 `STD_OUTPUT_HANDLE` raw 핸들 사용. (텍스트모드 CRLF 변환은 Rust 파이프 write 에 영향 없음 — 버퍼링만 문제) |
| **callback 에서 무거운 작업 수행** | WASAPI callback 지연 → underrun/drop/deadlock, stdout backpressure 시 캡처 정지 | callback 은 bounded channel enqueue 만 수행. worker thread 가 resample/framing/write/flush 처리. overflow 정책 테스트 |
| **fatal 없이 종료** | Python 이 `NativeCaptureError` 대신 정상 종료처럼 처리할 수 있음 | 모든 start/stream/format 실패는 `fatal` JSON flush 후 non-zero exit. worker panic 은 release `panic = "abort"` 로 panic hook(fatal emit) 실행 후 abort 보장 |
| **출력 장치 exclusive 점유** (일부 DAW/스트리밍 드라이버) | loopback init 실패 | `wasapi_init_failed` 로 surface. 회의실 PC 에선 드물지만 가능 |
| cpal WASAPI loopback 은 **무음 시 무패킷** | 재생 중단 구간 청크 없음 | Mac 도 동일(RMS 게이팅과 같음). PoC 수용. 회의는 거의 연속 오디오 |
| **소스 mix format 가변** (48k/44.1k/96k, f32/i16, interleaved) | 고정 가정 시 깨짐 | `pcm.rs` 가 `GetMixFormat`(cpal `default_input_config`) 결과를 동적 처리. Mac 의 planar 가정 이식 불가 |
| **종료 graceful성**: Windows `terminate()` = TerminateProcess(하드킬) | `stopping` 미발생 가능 | OS 가 프로세스 종료 시 stream/COM 정리. PoC 수용 |
| **고아 프로세스**: 앱/sidecar 종료 시 헬퍼 잔존 | 리소스 누수 | **1차: broken-pipe(부모 종료) 감지 시 헬퍼 즉시 exit** — 최저비용 방지책. Job Object 기반 견고 정리는 Phase 2b. E2E 에서 잔존 확인, 잔존 시 Phase 2b blocker 로 승격 |
| cpal loopback 제약이 PoC 막음 | 일정 리스크 | raw `windows`/`wasapi` 크레이트 폴백 경로 확보 |

---

## 9. 산출물 체크리스트

- [ ] Task 0 cpal loopback spike 결과 기록: callback, sample format, 첫 청크, 무음 cadence, go/no-go
- [ ] `apps/native_helper_win/` 크레이트 (Cargo.toml, src/{main,ipc,pcm,capture}.rs; `device_watch.rs` 는 Phase 2b)
- [ ] Rust 단위테스트 (pcm/ipc; `device_watch` 는 Phase 2b) — Mac+Windows 통과
- [ ] `scripts/build-release.ps1`
- [ ] callback→bounded channel→worker thread 구조 검증(콜백 안에서 stdout write/resample 금지)
- [ ] fatal 계약 검증: 모든 start/stream/format 실패가 fatal JSON flush 후 non-zero exit
- [ ] panic 전파 검증: worker panic → release `panic = "abort"` 로 panic hook(fatal) 후 프로세스 abort
- [ ] broken-pipe 종료 검증: 부모 종료 시 헬퍼 stdout write 실패 → 즉시 exit(고아 없음)
- [ ] `package.json` `build:native-helper-win` (Phase 2b packaged app)
- [ ] `sidecar.rs::locate_bundled_native_helper()` Windows x86_64 (Phase 2b packaged app)
- [ ] `tauri.windows.conf.json` externalBin + before*Command (Phase 2b packaged app)
- [ ] `config/audio.py` `NATIVE_HELPER_BIN_PATH` Windows dev 분기
- [ ] Windows 스모크 + E2E 실측(~50 chunks/sec, viewer 자막, helper 잔존 확인)
- [ ] 문서 동기화: `NATIVE_DESKTOP_HELPER_PLAN.md` Phase 2 / `ROADMAP.md` Native track 체크박스
