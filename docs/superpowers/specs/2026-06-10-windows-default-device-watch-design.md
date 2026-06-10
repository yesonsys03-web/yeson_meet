# Windows 기본 출력장치 변경 추적 (device_watch) — 설계 (Phase 2b ②)

> 목적: 회의 *도중* Windows 기본 출력장치가 바뀌어도(스피커→헤드폰/블루투스/HDMI) 자막이
> 끊기지 않도록, 네이티브 헬퍼가 **새 기본장치 루프백으로 인프로세스 자동 전환**한다.
> 상위 기획: `docs/NATIVE_DESKTOP_HELPER_PLAN.md` Phase 2b. 선행 설계:
> `docs/superpowers/specs/2026-05-28-windows-wasapi-helper-design.md`(§3·§4·§7에서 `device_watch.rs`를
> **폴링 기반**으로 예고). 본 문서는 그 예고를 구체화·확정한다.
> 선행 완료: Phase 2b ①(Job Object 고아정리) 테스터 실증 PASS(2026-06-10).

> **감지 방식 결정(2026-06-10)**: **폴링(A)** 채택. 대안 IMMNotificationClient(이벤트, B)를 검토했으나,
> 콜백을 wake 신호로만 쓰고 실제 판단은 `cpal::default_output_device().name()` 재조회로 하면 B의 판단
> 로직이 A와 **동일**해진다(콜백 device-id를 직접 비교하면 cpal과 ID 포맷 불일치). 그러면 B가 사는 건
> 감지 지연(~1초)뿐인데, 본 용도는 전환 시 teardown+rebuild 공백이 어차피 sub-second~수초라 체감이
> 작다. 반대로 B는 **등록 실패/콜백객체 조기drop/COM 모드충돌 시 알림이 안 와도 fallback 없이 영구
> stuck**이라는, 폴링엔 없는 조용한 실패모드를 들인다. 자막 끊김을 고치는 슬라이스가 같은 "조용한
> 끊김" 모드를 새로 만드는 건 자기모순. 폴링은 **자가 보정**(놓침 개념 없음)이라 궁극적 안전성·단순성·
> 검증가능성 모두 우위. → COM·전용스레드·`windows` 크레이트 전부 불필요.

---

## 0. 동기 — 정확한 버그 구분

현재 헬퍼(`capture.rs::start()`)는 **시작 시점에 한 번** `host.default_output_device()`로 기본
출력장치를 잡고, 그 장치의 loopback만 캡처한다. 회의 중 기본장치가 바뀌면 두 시나리오가 갈린다:

| 시나리오 | OS 동작 | 현재 헬퍼 결과 | 본 슬라이스 |
|---|---|---|---|
| **①장치 제거** (활성 기본장치를 물리적으로 뽑음) | WASAPI가 스트림 무효화(`AUDCLNT_E_DEVICE_INVALIDATED`) | cpal stream error → `fatal:stream_error` → exit(4). **조용히 죽지 않음**(부모/데스크톱 재시작이 흡수). 기존 §7 명시 동작 | **무회귀** (변경 안 함, (b) 선택확장에서만 재고) |
| **②장치 강등** (헤드폰/BT 꽂음 → 그게 새 기본, 기존 스피커는 *여전히 유효*하나 기본 아님) | 옛 스피커 loopback 스트림은 **유효한 채로 무음만 수신** | `source`/main 루프 `Timeout=>continue`로 **영원히 대기** → **자막이 조용히 끊김. 에러도 경고도 없음.** | **이게 해결 대상 (핵심 갭)** |

**핵심**: 보고된 증상("회의 중 출력장치 바꾸면 자막 끊김")은 시나리오 ②이고, ②는 **에러 경로를
전혀 타지 않는다**. 따라서 본 슬라이스의 핵심(a)은 `stream_error` 처리를 건드리지 않고 ②만 메운다.
①의 "fatal 후 재시작 대신 인프로세스 자가치유"는 기존 §7 결정을 뒤집으므로 **(b) 선택적 확장**으로
명시 분리한다(§7).

---

## 1. 결정 요약

| 항목 | 결정 | 근거 |
|---|---|---|
| 감지 방식 | **폴링** — 주기적으로 `cpal::default_output_device().name()` 재조회 후 활성 `device_name`과 비교 | 자가 보정(영구 stuck 불가), 단순, 신규 크레이트 0. cpal이 "기본장치" 단일 진실원 |
| 폴링 위치 | **main 루프 인라인**(별도 스레드·채널 없음) | 루프가 이미 ≤250ms마다 깸(audio block 또는 timeout). clock 게이트로 ~1.5s마다만 재조회 |
| 폴링 주기 | **~1.5초** | 감지 지연과 비용의 절충. 재조회는 WASAPI enumerate 1회(수 ms), audio 콜백과 무관 |
| 전환 방식 | **인프로세스 재빌드**(헬퍼 프로세스 유지, stdout 열린 채 cpal 스트림만 교체) | §2 불변식. 헬퍼 respawn 시 sidecar `readexactly`가 EOF→세션 전체 붕괴 |
| 핵심 슬라이스 (a) | 기본장치 **변경 시 전환**(시나리오 ②) | 보고된 갭. 기존 ① fatal 동작 무회귀 |
| 선택 확장 (b) | 장치 **제거 시 fatal 대신 인프로세스 자가치유** | 기존 §7 뒤집음 → 별도 플래그·백오프. 본 슬라이스 비범위 |
| 결정 로직 위치 | `device_watch.rs` = **순수 결정 상태기계**(cpal/windows 타입 미import, clock 주입) | Mac `cargo test` 가능(모듈 격리 규칙) |
| 비교 키 | 장치 **이름**(`Device::name()`) | cpal이 노출하는 안정적 식별자. 동일이름 2장치 엣지는 드묾·수용(§8) |
| 이벤트 추가 | `device_changed {from, to, source_sample_rate, source_channels}` (stderr JSON) | 원 spec §3에 예약됨. Python은 미지 이벤트 INFO 로깅만 → **소비자 무변경** |
| 지원 범위 | Windows 10/11 x86_64. 출력(render) 기본장치만 | 입력/role별 분리는 비범위 |

---

## 2. 불변식 (Load-bearing) — 왜 인프로세스 재빌드인가

> **재빌드는 헬퍼 내부에서 일어나고, stdout은 계속 열려 있으며, sidecar/server/WS는 무변경이다.**

- `NativePipeSource.chunks()`는 동일 파이프를 `readexactly(640)`으로 계속 읽는다. 헬퍼를
  kill+respawn하면 그 파이프가 **EOF**→`IncompleteReadError`→소스 종료→**세션 전체가 무너진다**.
  따라서 새 장치 반영은 반드시 **같은 프로세스·같은 stdout** 안에서 cpal 스트림만 교체해야 한다.
- sidecar↔server WebSocket, 서버 세션, viewer 연결은 **전혀 건드리지 않는다**. sidecar가 보는
  유일한 흔적은 stderr의 `device_changed` 한 줄뿐(파싱 안 함, INFO 로깅).
- 전환 중 **짧은 오디오 공백**(teardown+reinit, 통상 sub-second; BT 깨어남은 더 길 수 있음)이
  생기나 경계가 있고 서버 타임아웃보다 한참 짧다. 출력 계약(16k mono s16le 640B)은 불변.

이 불변식이 본 설계 전체의 아키텍처 정당화다.

---

## 3. 동작 흐름 (핵심 슬라이스 a)

```
capture::start() → device_name=A
emit started{device:A,...}

worker 루프(같은 stdout):
  loop {
    // 1) 평소: audio block 수신 → pcm → stdout (cadence ~20ms)
    //    무음: recv_timeout 250ms Timeout → continue
    // 2) device poll (clock 게이트: 마지막 폴 이후 ≥1.5s일 때만)
    if now - last_poll >= 1.5s {
        last_poll = now
        let polled = cpal::default_output_device().map(.name())   // 매 호출 fresh GetDefaultAudioEndpoint
        match device_watch::decide(active=A, polled, now) {
            Ignore  => {}                          // 동일장치 or throttle 내
            Rebuild => {
                // settle delay 후 옛 Capture drop → 새 capture::start()
                //   init 실패 시 retry(짧은 백오프 N회) → 모두 실패면 fatal:wasapi_init_failed
                // epoch++; 옛 rx/err_rx의 예상된 Disconnected/err는 폐기(§4)
                // rx/err_rx/Capture/PcmConverter 원자 교체(새 소스 포맷으로 conv 재생성)
                emit device_changed{from:A, to:B, source_sample_rate, source_channels}
                active = B
            }
        }
    }
  }
```

- 폴링은 **audio 콜백이 아니라 worker 루프**에서 돈다 → 캡처 cadence에 영향 없음.
- `decide`는 순수: `polled != active`면 Rebuild, 단 **min-rebuild-interval throttle**(예: 직전 재빌드
  후 5s 이내면 Ignore)로 두 장치가 기본을 빠르게 주고받는 flapping을 억제(clock 주입으로 판정).

---

## 4. main.rs 통합 — 재빌드 시 옛 스트림의 "예상된 죽음" 폐기

(이 처리는 폴링/이벤트 무관하게 인프로세스 재빌드에 공통으로 필요하다.)

- 재빌드를 위해 옛 `Capture`를 drop하면 옛 `SyncSender`가 끊겨 `rx.recv_timeout`이
  **`Disconnected`**를 반환한다. 현재 main.rs(`main.rs:95-101`)는 이를 `fatal:stream_error`
  exit(4)로 처리한다. → **의도된 teardown을 fatal로 오판하면 안 된다.**
- 해결: 재빌드 시 `rx`/`err_rx`/`Capture`를 **원자적으로 교체**하고, 교체 직전 옛 스트림에서
  오는 `Disconnected`/지연 `err_rx`는 **"예상된 죽음"으로 폐기**(rebuild **epoch/generation**
  카운터로 superseded 스트림 식별). 새 `rx`만 worker가 소비.
- **자발적 `stream_error`(시나리오 ①)는 여전히 fatal**이다. 바뀌는 건 "재빌드가 유발한 의도적
  Disconnected를 fatal로 보지 않는 것"뿐. ①을 reconnect로 재해석하는 건 (b)(§7) 별도 범위.

---

## 5. 모듈 구조 — `apps/native_helper_win/`

기존 모듈 격리 규칙(순수 로직은 Mac `cargo test` 가능, WASAPI는 Windows 전용 셸)을 그대로 따른다.
**폴링 채택으로 신규 Windows 전용 모듈·COM 코드·`windows` 크레이트가 전혀 없다.**

```
apps/native_helper_win/src/
  main.rs          # (수정) 인라인 device poll(clock 게이트) + rebuild epoch(옛 스트림 Disconnected/err 폐기) + 원자 교체 + device_changed emit + settle/retry
  capture.rs       # (무변경) capture::start() 재사용. CaptureFormat.device_name 이미 존재
  device_watch.rs  # (신규, 순수) decide(active_name, polled_default_name, now) → Rebuild|Ignore. min-rebuild-interval throttle. cpal/windows 미import
  ipc.rs / pcm.rs / source.rs  # 무변경
```

| 모듈 | 책임 | 테스트 |
|---|---|---|
| `device_watch.rs` | 순수 결정. 입력=(활성장치명, 재조회된 기본장치명, now). 출력=Rebuild/Ignore. 동일장치 무시 + min-interval throttle | **Mac `cargo test`**: clock 주입으로 결정·throttle 단위테스트 |
| `main.rs` | 인라인 폴 cadence → `cpal::default_output_device()` 재조회 → `decide` → 재빌드(epoch 교체) → `device_changed` emit → settle/retry | Windows E2E |

**크레이트 추가 없음.** cpal·serde_json·rubato는 이미 의존. `cpal::default_output_device()` 호출 시
COM 초기화는 **cpal이 내부에서 처리**(thread-local) → 우리 쪽 COM 코드 0. Mac `cargo check`는 기존대로
통과(신규 cfg 코드 없음).

> 📌 **모듈 격리 규칙 재확인**: `device_watch.rs`는 cpal·windows 타입을 import하지 않는다. 문자열
> (장치명)과 주입된 `now`만 받는다. cpal 호출은 `capture.rs`·`main.rs`의 `cfg(windows)` 진입부에만
> 가둔다(비-Windows 타깃에선 `main.rs`가 cpal을 안 들이는 기존 패턴 유지). 깨지면 Mac `cargo test` 불가.

> 📌 **폴링은 worker 루프 인라인**이 별도 스레드보다 단순하다. 루프가 이미 audio block마다(연속재생,
> ~20ms) 또는 timeout마다(무음, 250ms) 깨므로, `now - last_poll >= 1.5s` 게이트만 추가하면 두 경우 모두
> 일정 cadence로 폴된다. 별도 스레드·채널·동기화 불필요.

---

## 6. 폴링 비용·전제

- `cpal::default_output_device()`는 매 호출 WASAPI `GetDefaultAudioEndpoint`를 타 **현재** 기본장치를
  fresh 반환한다(캐시 아님). ~1.5s마다 1회는 무시할 비용이고, 활성 cpal 스트림(콜백 스레드)과 무관.
- **전제(하드웨어 1회 확증)**: 위 "매 호출 fresh 반환"이 성립해야 폴링이 변경을 감지한다. 이는 어떤
  감지방식이든(이벤트 B도 동일 재조회 의존) 필요한 전제다. Task에서 실장치 전환으로 확인한다.

---

## 7. 명시적 비범위 / 선택 확장

- **(b) 장치 제거 시 인프로세스 자가치유** — 시나리오 ①을 fatal 대신 reconnect로. **기존
  `windows-wasapi-helper-design.md` §7 결정을 뒤집음**. 별도 백오프·재시도 카운터·카운터리셋
  규칙 필요. 본 슬라이스 **비범위**(원하면 후속 슬라이스). 핵심(a)는 ①을 그대로 fatal 유지.
- **무음 워치독**(N초 무패킷 시 `no_audio` 경고 이벤트) — 별개 디버깅성 개선. device_watch가 ②를
  메우면 "무음=실제 무음"으로 의미가 명확해지므로 후속에서 함께 검토. 본 슬라이스 제외.
- **데스크톱 UI**: `device_changed`를 운영자 배너로 표면화("🎧 출력장치가 B로 전환됨") — Phase 3 UX.
  본 슬라이스는 **헬퍼 stderr 이벤트까지만**.
- **입력장치(마이크) 추적** — 본 헬퍼는 출력 loopback 전용. 비범위.
- **Mac/Linux** — Mac은 ScreenCaptureKit 헬퍼가 자체 처리. 본 폴링 로직은 Windows 캡처 경로에만 적용.

---

## 8. 주요 리스크

| 리스크 | 영향 | 대응 |
|---|---|---|
| **재빌드 race**: 옛 스트림 Disconnected를 fatal로 오판 | 전환 순간 헬퍼 죽음 → 세션 붕괴 | rebuild epoch/generation으로 superseded 스트림 신호 폐기(§4). 핵심 슬라이스 1순위 테스트 |
| **전환 직후 새 장치 미준비**(BT 깨어남) | 재빌드 init 실패 | settle delay + **init 실패 시 retry**(짧은 백오프 N회) → 모두 실패면 fatal |
| **flapping**(두 장치가 기본을 빠르게 교대) | 재빌드 폭주 | `device_watch` **min-rebuild-interval throttle**(예: 5s). clock 주입 |
| **감지 지연 ~1.5s** | 전환 후 자막 재개까지 최대 폴주기만큼 추가 지연 | 수용(②의 무한대기 대비 큰 개선). 필요 시 폴주기 단축(비용 미미) |
| **동일이름 2장치** | 이름 비교가 전환을 놓침 | 드묾. 수용. 필요 시 후속에서 cpal device 식별 보강 |
| **새 장치 포맷 상이**(48k→44.1k, 채널 변동) | 고정 가정 시 깨짐 | 재빌드 시 `PcmConverter`를 새 소스 포맷으로 재생성. 출력은 항상 16k mono(불변) |
| **null 기본장치**(전부 제거) | 재조회 None | (a)에선 **재빌드 안 함**(전환할 새 장치 없음). 옛 스트림 살아있으면 그대로. 모두 무효면 cpal 에러로 ① 경로(fatal). 영구 대기/경고 정책은 (b)와 함께 후속 |
| **`default_output_device()` 캐시 가정 오류** | 변경 미감지 | §6 전제 — 하드웨어 1회 확증(B도 동일 의존) |

> COM 스레딩·콜백객체 수명·모드충돌 등 이벤트(B) 고유 리스크는 폴링 채택으로 **전부 소거**됨.

---

## 9. 검증 전략

- **Rust 단위테스트**(Mac + Windows, `cargo test`):
  - `device_watch::decide`: (활성=A, 재조회=B, now) → Rebuild; (A, A) → Ignore; 직전 재빌드 후
    throttle 윈도 내 재변경 → Ignore; throttle 경과 후 → Rebuild. clock 주입으로 결정적.
- **Windows 하드웨어 E2E**(실장치, Mac 불가):
  - 회의 시작(자막 정상) → **재생 중 출력장치 변경**(스피커→헤드폰/BT) → ~1.5s 내 stderr
    `device_changed{from,to}` 1회 → **자막 재개**(server `audio_stats` chunks 재개) → 공백 확인.
  - 반대 전환(헤드폰→스피커)도 대칭 확인.
  - **무회귀**: 장치 *제거*(활성 기본 뽑기)는 여전히 `fatal:stream_error` exit(시나리오 ①, §7 불변).
  - **고아 무회귀**: 전환 후에도 무음 하드킬 시 헬퍼 사라짐(Job Object Phase 2b ①) 재확인.
- **Mac 사전 타입검증**: `cargo check`(기존대로 통과, 신규 cfg/크레이트 없음).

---

## 10. 산출물 체크리스트

- [ ] `device_watch.rs`(신규, 순수): `decide(active, polled, now)` + min-rebuild-interval throttle + clock 주입
- [ ] `device_watch` 단위테스트(Mac `cargo test` 통과): Rebuild/Ignore + throttle
- [ ] `main.rs` 수정: 인라인 device poll(~1.5s clock 게이트) + rebuild epoch(superseded 스트림 신호 폐기) + 원자 rx/err_rx/Capture/conv 교체 + `device_changed` emit + settle/retry
- [ ] 이벤트 계약: `device_changed{from,to,source_sample_rate,source_channels}` — Python 소비자 무변경 확인
- [ ] Mac `cargo check` 통과(무회귀, 신규 크레이트 0)
- [ ] Windows E2E: 전환→`device_changed`→자막 재개, 대칭 전환
- [ ] 무회귀 실측: 장치 제거 시 여전히 fatal(시나리오 ①, §7), 무음 하드킬 고아정리(Phase 2b ①) 유지
- [ ] (b) 선택확장 결정 기록: 제거 시 자가치유로 갈지 §7 유지할지 — 본 슬라이스 후 별도 판단
- [ ] 문서 동기화: `NATIVE_DESKTOP_HELPER_PLAN.md` Phase 2b / `ROADMAP.md` Native track 체크박스
