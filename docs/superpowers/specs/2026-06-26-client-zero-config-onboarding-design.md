# 클라이언트 무설정(zero-config) 온보딩 설계

- 날짜: 2026-06-26
- 상태: 설계 승인됨 (구현 계획 작성 전)
- 대상: `apps/desktop`(클라이언트), `apps/server_desktop`(서버 콘솔), `apps/server`(백엔드 API)

## 1. 목표

초심자 사용자가 클라이언트 앱을 설정할 때 가장 어려워하는 두 가지 수동 입력을 제거한다.

1. **서버 IP 타이핑** — `ws://192.168.x.x:8000` 같은 주소를 손으로 입력.
2. **device key 수동 전달** — 서버 콘솔에 가서 키를 발급·복사한 뒤 클라이언트에 붙여넣기.

설정 화면을 현재 4칸(서버주소·이메일·비번·device key)에서 **2칸(operator 이메일·비번)** 으로 줄인다. 나머지(서버주소·device key)는 자동으로 채운다.

## 2. 범위

### 포함 (In scope)
- 서버 자기 LAN IP 감지 + 서버 콘솔에 주소 표시.
- 서버 mDNS 광고 + 클라이언트 자동발견.
- 클라이언트 로컬(127.0.0.1) 서버 자동 감지.
- 클라이언트 device key **self-enroll**(저장된 operator 로그인으로 서버에서 자기 키 1개 자동 발급).

### 제외 (Out of scope — 명시적으로 안 함)
- operator 비번을 클라에서 제거 / 토큰 기반 인증으로 전환(B·C안). **사용자 체감 변화 없음 + 서버 인증 모델 변경 필요 → 보류.** 자격증명은 현재처럼 키체인에 저장한다.
- device key를 QR/페어링 코드에 싣는 방식. 무TTL 베어러 키를 QR에 노출하는 것은 기존 보안결정과 충돌하므로 채택하지 않음.
- 서버 다중 인스턴스 지원. 서버는 LAN에 **1대**라는 전제(다중서버 선택 UI 없음).
- operator 로그인 자체의 자동화. operator 이메일/비번은 최초 1회 입력(현행 유지).

## 3. 현재 상태 (조사 결과)

- 클라이언트 온보딩: `apps/desktop/src/setup/MeetingQuickStartPanel.tsx` — 4개 텍스트 필드(`serverWsBase`, operator email/password, `deviceApiKey`)를 `saveCredentials`로 키체인에 저장. QR/페어링/자동발견 없음.
- 자격증명 저장: OS 키체인이 권위, localStorage는 파생 캐시. Rust 커맨드 `save_credentials`, `update_server_ws_base`(부분병합), `credentials_meta`. (`apps/desktop/src-tauri/src/credentials.rs`)
- 회의 시작: `apps/desktop/src/console/oneClickStart.ts` — `login(email,password)→operatorToken→createSession(operatorToken)`. 매 회의마다 operator 인증.
- 세션 생성/종료: `apps/server/api/v1/sessions.py:152,307` = `require_operator`. device key로는 세션 생성 불가(캡처 전용).
- device key 발급: 서버 콘솔의 `apps/server_desktop/src/DevicePanel.tsx` → `POST /api/v1/devices`(operator 인증). 무TTL 베어러 키.
- 서버 바인딩: `HOST=0.0.0.0`(`apps/server_desktop/src-tauri/src/server_process.rs`). **자기 LAN IP 감지/표시 코드 없음.**
- QR 인프라: `apps/desktop/src/console/ViewerQrPanel.tsx`(뷰어 URL용, `qrcode` 라이브러리). device key와는 분리.
- mDNS/zeroconf: 없음(의존성 미존재).

## 4. 동작 설계 — 클라이언트 자동 경로 (우선순위)

클라이언트 첫 실행 또는 `serverWsBase` 미설정 시, 서버 주소를 다음 순서로 자동 결정한다.

1. **로컬 프로브**: `http://127.0.0.1:8000/api/v1/health` 요청 성공 → 같은‑PC 구성 → `ws://127.0.0.1:8000` 자동 설정.
2. **mDNS 자동발견**: 로컬에 없으면 LAN에서 `_yeson-meet._tcp` 광고 탐색 → 발견 시 `ws://<server-ip>:<port>` 자동 설정.
3. **수동 폴백**: 둘 다 실패(mDNS 차단 사내망 등) → 서버 콘솔에 표시된 주소를 사용자가 복사·붙여넣기. "다시 찾기" 버튼 제공.

주소가 정해진 뒤:
4. 사용자가 **operator 이메일·비번 입력**(최초 1회) → "연결".
5. 클라가 서버에 로그인 → **device key self-enroll 호출** → 반환된 키를 키체인에 저장.
6. 이후 재실행/회의 시작은 현행과 동일(저장된 자격증명 + device key).

## 5. 컴포넌트

### 5.1 서버 백엔드 (`apps/server`)
- **device self-enroll 엔드포인트**(신규): `POST /api/v1/devices/self-enroll`
  - 인증: `require_operator`.
  - 동작: 요청 클라이언트용 device를 자동 명명(예: 호스트명)으로 1개 발급하고 `api_key`를 반환. 기존 `POST /api/v1/devices`(device-admin)와 **분리**하여, 클라이언트에는 "자기 키 1개 발급"만 노출하고 목록/폐기 등 admin 면은 주지 않는다.
  - 기존 보안결정 준수: 키 발급 주체는 여전히 서버, 클라는 자기 키만 보유.

### 5.2 서버 콘솔 (`apps/server_desktop`)
- **자기 IP 감지**(신규, Rust): `local-ip-address`(또는 `if-addrs`)로 주 LAN IPv4 추출. NIC 다수면 후보 목록 → 콘솔에서 선택.
- **mDNS 광고**(신규, Rust): `mdns-sd`로 `_yeson-meet._tcp` 등록(포트 + 호스트명 TXT). 서버 프로세스 기동 후 등록, 종료 시 해제.
- **주소 표시 UI**(신규): "내 서버 주소: `ws://<ip>:<port>`" 배너 + 복사 버튼. (mDNS 차단 환경의 수동 폴백용. QR은 불필요 — 데스크톱↔데스크톱은 텍스트 복사.)

### 5.3 클라이언트 (`apps/desktop`)
- **서버 자동발견**(신규, Rust): `discover_server` invoke 커맨드 — `mdns-sd`로 `_yeson-meet._tcp` 브라우즈 → 단일 서버 `{ip, port}` 반환(타임아웃 포함).
- **로컬 프로브**(TS): `apps/desktop/src/setup` 내 순수 함수 — health fetch로 localhost 서버 확인.
- **self-enroll 호출**(TS): operator 로그인 토큰으로 `POST /api/v1/devices/self-enroll` → `deviceApiKey` 저장(`save_credentials`/`update`).
- **온보딩 UI 변경**: `MeetingQuickStartPanel`/`SetupAssistant` — 서버주소·device key 필드를 폼에서 제거하고, 자동발견 결과 표시 + "서버 자동 찾기/다시 찾기" + (폴백) 주소 붙여넣기. 입력은 operator 이메일·비번 2칸.

## 6. 데이터 흐름

```
[서버 시동] → IP 감지 → (mDNS 광고 등록 + 콘솔 주소 배너)
[클라 시동, 주소 없음]
  → 로컬 프로브(127.0.0.1) ──성공→ serverWsBase=ws://127.0.0.1:8000
  → 실패 시 mDNS discover ─발견→ serverWsBase=ws://<ip>:<port>
  → 실패 시 수동 붙여넣기(서버 콘솔 주소)
  → operator 이메일/비번 1회 입력 → login → operatorToken
  → POST /devices/self-enroll(operatorToken) → deviceApiKey 저장(키체인)
[이후] 재실행 → 저장된 주소+자격증명+device key로 회의 시작(현행과 동일)
```

## 7. 에러 처리 / 폴백

- 로컬 프로브 실패(서버 미기동/타 PC): 조용히 다음 단계(mDNS)로.
- mDNS 타임아웃/차단: 수동 주소 입력 폴백 + 명확한 안내("서버 콘솔의 '내 서버 주소'를 붙여넣으세요").
- 서버 NIC 다수: 콘솔에서 후보 IP 선택. 클라 발견 시 첫 응답 사용(서버 1대 전제).
- self-enroll 실패(로그인 실패/권한): operator 자격증명 재확인 메시지. 세션은 생성하지 않음.
- 자동발견으로 주소가 바뀌면 `update_server_ws_base`(부분병합)로 키체인 write-through, localStorage 동기.

## 8. 테스트

- **순수 함수(vitest)**: 로컬 프로브 결과→`serverWsBase` 매핑, 발견 결과(`{ip,port}`)→`ws://` 조립, 폴백 분기 로직.
- **서버(pytest)**: `POST /api/v1/devices/self-enroll` — operator 인증 필요, 자기 키 1개 발급, admin 면 미노출.
- **Rust(통합/수동)**: `local-ip-address` 감지, `mdns-sd` 광고/브라우즈 — 자동화 어려워 실기기 LAN 검증.
- **수동 E2E**: (a) 같은 PC 구성 — 입력 0으로 주소 자동, (b) LAN 2대 — mDNS 자동발견, (c) mDNS 차단 — 수동 폴백.

## 9. 보류/후속 (기록용)

- operator 비번 비저장 + 토큰/단일 device-key 인증(B·C안): 사용자 체감 변화 없고 서버 인증 변경 필요 → 보류.
- device key를 QR/페어링 코드로 전달: 무TTL 키 노출 위험 → 채택 안 함.
- mDNS가 막힌 환경 비율이 높으면, 서버 콘솔 주소에 QR 추가 검토.
