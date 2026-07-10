# 웹 캡처 원격 지원 + 선행 보안강화 — 설계

- 날짜: 2026-07-10
- 상태: 사용자 승인(대화)
- 관련: `2026-07-09-web-capture-page-design.md`(웹 캡처 v1), `docs/web-capture-operator-guide.md`, `apps/server_desktop/src-tauri/src/tunnel_proxy.rs`(뷰어 전용 프록시)

## 1. 목표

원격 진행자가 `https://<터널>/capture`에서 **완전 자립**(스스로 로그인 → 회의 생성 → 탭 캡처)으로
회의를 진행할 수 있게 한다. 현재는 터널 프록시가 `/capture`와 그 API를 404로 막아
localhost 전용이다(PR#25 문서 정정 참조).

인증 모델은 **"로그인 공개 + 보강"**(사용자 결정): 운영자 로그인을 터널에 노출하되,
아래 보안 4종을 선행한다. "콘솔 발급 캡처 링크" 방식은 담당자·진행자가 동일 인물이고
외부에 있는 시나리오를 커버하지 못해 기각.

## 2. 비범위 (Non-goals)

- 퀵터널 URL 고정(재시작마다 바뀜 — 최신 URL 공유는 기존 운영 방식 유지)
- Safari/Firefox 지원(Chromium 전용 유지), Zoom/Teams 데스크탑 앱 캡처
- LAN `http://<IP>` 보안 컨텍스트화(인증서 배포) — 터널 https가 해법
- 데스크탑 사이드카의 인증 방식 변경(`/ws/sidecar?key=` 영구 디바이스키는 LAN 전용으로 존속)
- 로그인 계정 관리 UI(비밀번호 정책 등)

## 3. 위협 모델 (왜 보강이 필요한가)

터널 URL은 참석자 QR로 배포되는 순간 비밀이 아니다. 로그인·캡처 표면을 터널에
열면 생기는 위협과 대응:

| 위협 | 대응 |
|---|---|
| 비밀번호 온라인 브루트포스 | 로그인 rate-limit (§4.1) |
| URL 쿼리의 크리덴셜이 에지 로그·히스토리에 잔존 | 어떤 토큰도 URL에 싣지 않음 (§4.2, §4.4) |
| 영구 디바이스키 유출 시 무기한 악용 | 웹 캡처를 세션 스코프 단기 토큰으로 전환, self-enroll 터널 비노출 (§4.2~4.3) |
| 경로 스머글링으로 비노출 표면 접근 | 기존 정규화·deny-by-default 유지 + 신규 경로에 우회 테스트 확장 (§4.5) |

잔여 위험(수용): 계정 비밀번호가 약하면 rate-limit로도 한계 — 비밀번호 품질이 마지노선.
로그인 성공 = 운영자 권한 전체(회의 생성·오디오 주입·해당 세션 자막 열람).

## 4. 설계

### 4.1 로그인 rate-limit (서버, `apps/server/api/v1/auth.py`)

- 인메모리 카운터(프로세스 단일 — 번들 uvicorn 단일 프로세스 전제).
- **계정(email) 기준 주력**: 연속 실패 5회 → 5분 잠금(잠금 중 시도는 429 + `Retry-After`).
  성공 시 카운터 리셋. 퀵터널 경유는 원 IP가 에지로 가려질 수 있어 IP 기준은 보조
  (IP당 분당 20회 상한, 가능할 때만).
- 실패 응답에 고정 지연(~300ms)을 두어 온라인 시도 속도 자체를 낮춘다.
- LAN/데스크탑 로그인에도 동일 적용(트래픽 규모상 무해).

### 4.2 세션 스코프 캡처 토큰 (서버)

- 신규: `POST /api/v1/sessions/{id}/capture-token` (운영자 JWT, Authorization 헤더).
- 발급: `secrets.token_urlsafe(32)` → **해시**를 인메모리 TTL 저장소에 세션 바인딩으로 보관.
  응답 `{ token, expires_at }`. 재발급 시 이전 토큰 대체(세션당 활성 1개).
- 만료: 세션 종료 시 즉시 폐기 + 안전상한 12시간.
- **DB 스키마 무변경**(인메모리) — 번들 create_all/ALTER 추가 함정 회피
  (`project_bundle_additive_migration`). 서버 재시작 시 토큰 소실은 수용
  (회의 자체가 끊기므로 클라가 JWT로 재발급).

### 4.3 웹 캡처에서 self-enroll 제거 (웹)

- `apps/web/src/capture/captureApi.ts`의 self-enroll 호출·localStorage 디바이스키
  저장 삭제. localhost 포함 전면 캡처 토큰으로 전환(코드 경로 단일화).
- `/api/v1/devices/self-enroll` 엔드포인트 자체는 데스크탑 클라 온보딩용 존속,
  터널 허용리스트에는 **불포함**.

### 4.4 오디오 WS: 신규 `/ws/capture` + 첫 메시지 인증 (서버+웹)

- 신규 WS 라우트 `/ws/capture`. **URL 쿼리 없음.** 연결 후 5초 내 첫 텍스트 메시지
  `{"type":"auth","token":...,"session":...}`로만 인증. 실패/타임아웃 = close(1008).
- 인증 후에는 기존 `/ws/sidecar` 오디오 계약(audio.started/chunk_meta/audio.stopped,
  16kHz mono s16le, seq 재접속 누적) 로직을 그대로 재사용.
- 세션-디바이스 바인딩 의미론("최초 연결이 세션을 점유")은 토큰 파생 합성 식별자로 보존.
- `/ws/sidecar`는 무변경, 터널에서 계속 차단.
- 웹 `audioWsClient.ts`: 접속 대상 `/ws/capture`로 변경 + auth 핸드셰이크 선행.
  open-less 연속 거부 표면화(unreachable) 로직 유지.

### 4.5 터널 허용리스트 확장 (`tunnel_proxy.rs`)

`decide()`를 **메서드 인지형**으로 확장(`decide(method, path)`) — 같은 경로의
GET 목록/조회를 막고 필요한 동사만 연다. deny-by-default·정규화 파이프라인은 무변경.

추가 ALLOW (전부 정규화 후 매칭):

| 메서드 | 경로 | 용도 |
|---|---|---|
| GET | `/capture` (정확히) | 캡처 SPA 라우트 |
| POST | `/api/v1/auth/login` | 운영자 로그인 |
| POST | `/api/v1/sessions` | 회의 생성 (GET 목록은 계속 deny) |
| POST | `/api/v1/sessions/<id>/end` | 회의 종료 |
| GET | `/api/v1/sessions/<id>/utterances` | 자막 미리보기 폴링 |
| POST | `/api/v1/sessions/<id>/capture-token` | 캡처 토큰 발급 |
| (WS) | `/ws/capture` | 오디오 스트림 |

`<id>`는 단일 경로 세그먼트 와일드카드(빈 세그먼트·중첩 불가). 계속 DENY:
`/ws/sidecar`, `/ws/operator`, `/api/v1/devices*`(self-enroll 포함),
`/api/v1/sessions` GET, `/api/v1/sessions/<id>` GET(회의기록 상세),
그 외 전부. 기존 우회 테스트(인코딩·대소문자·`..`·슬래시)를 신규 경로에 확장.

### 4.6 자막 미리보기 = REST 폴링 통일 (웹)

- 현행 `/ws/operator?access=<JWT>`는 JWT가 URL에 실려 터널 노출 부적합 → 캡처
  페이지의 미리보기는 기존 `GET /api/v1/sessions/{id}/utterances`(Authorization 헤더)
  **2.5초 폴링으로 통일**(localhost 포함). operator WS는 터널 비노출·서버 무변경.
- 참석자 뷰어(실시간 WS)는 무변경. 진행자 미리보기만 1~2초 지연(수용).

### 4.7 UX 정리 (웹)

- 비보안 컨텍스트(`getDisplayMedia` 부재) 시 "탭 선택하고 캡처 시작" 버튼
  **비활성화** + 사람 말 안내. 원시 JS 에러(`Cannot read properties of undefined`)
  노출 금지.
- 상단 배너 문구를 실제 지원 범위와 일치시킴(이 트랙 완성 후 터널 https 안내가 참이 됨).

### 4.8 문서

- `docs/web-capture-operator-guide.md`: 원격 사용법(터널 URL, 자동 공개 전제,
  URL 로테이션 주의) 갱신.
- `docs/auto-update-release-checklist.md` 상시 항목의 이중 클라이언트 검증 문구 유지.

## 5. 테스트 전략

- **서버(pytest)**: rate-limit(연속 실패→429→시간 경과→해제, 성공 리셋),
  캡처 토큰(발급/JWT 없이 401/만료/세션 종료 시 폐기/재발급 대체),
  `/ws/capture`(정상 인증→오디오 계약 동작, 오토큰/타임아웃/종료세션 → 1008,
  seq 누적 재접속).
- **프록시(Rust 단위테스트)**: 신규 ALLOW 각 항목 + 메서드 불일치 deny +
  기존 우회 패턴(%2e, 대소문자, `..`, `//`, `\`)을 신규 경로에 적용 + `<id>`
  와일드카드 경계(빈 세그먼트, 중첩, `utterances` 뒤 추가 세그먼트 deny).
- **웹(vitest)**: captureApi(자가발급 제거, capture-token 흐름), audioWsClient
  (auth 선행 핸드셰이크, 거부 표면화), 미지원 컨텍스트 버튼 게이팅.
- **E2E(수동)**: ① localhost 기존 흐름 회귀 ② 터널 열고 **다른 컴퓨터**(Windows
  Chrome)에서 로그인→회의→캡처→자막 전 과정 ③ 차단 확인: 터널에서
  `/ws/sidecar`·`/api/v1/devices/self-enroll`·`/api/v1/sessions` GET이 404.
- **배포 주의**: apps/server 변경 = **재동결(build-server.sh) + 서버앱 재시작 필수**,
  tauri:dev 중 재동결 금지(`project_server_frozen_bundle_rebuild`).

## 6. 공사 순서 (구현 계획의 뼈대)

1. 로그인 rate-limit (서버, 독립)
2. 캡처 토큰 발급·저장소 (서버)
3. `/ws/capture` 첫 메시지 인증 + 핸들러 재사용 (서버)
4. 웹 캡처 전환: self-enroll 제거·토큰 흐름·`/ws/capture`·미리보기 폴링 (웹)
5. 터널 허용리스트 메서드 인지형 확장 + 우회 테스트 (Rust)
6. UX 정리 + 가이드 문서 (웹+문서)
7. 재동결 → localhost 회귀 → 터널 E2E
