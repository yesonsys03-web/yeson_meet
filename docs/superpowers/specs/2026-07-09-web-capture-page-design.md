# 웹 캡처 페이지 설계 (2026-07-09)

## 목적

회의 진행자가 **앱 설치 없이** 브라우저만으로 실시간 자막 캡처를 시작할 수 있게 한다.
주 시나리오: 구글밋을 Chrome 탭에서 열고, 캡처 페이지가 탭 오디오를 잡아 서버로 스트리밍 → 기존 파이프라인(Gemini Live → 자막 뷰어)이 그대로 동작.

데스크탑 앱(`apps/desktop`)은 **대체가 아니라 병행 유지**한다. Zoom/Teams 데스크탑 앱 회의 등 브라우저 밖 소리는 계속 데스크탑 앱 담당. 릴리스도 기존대로 계속한다.

## 확정된 요구사항

| 항목 | 결정 |
|---|---|
| 인증 | 기존 운영자 로그인(이메일/비밀번호) 재사용, 디바이스 키는 자가등록(self-enroll) 후 localStorage 보관 |
| UI 범위 | 로그인 · 회의 시작/종료 · 탭 선택 캡처 + **자막 미리보기 · 뷰어 QR/링크 · 오디오 레벨미터** |
| 마이크 | "내 목소리 포함" 혼합 **옵션 토글** 제공 (기본 OFF = 현 데스크탑 앱과 동일 동작) |
| 브라우저 | Chromium 계열 전제. 비Chromium/미지원 감지 시 안내 배너 (사내 브라우저 구성 불확실) |
| 콘솔 기능(지식저장고·과거 회의 등) | **이번 범위 제외**, 후속 "웹 콘솔" 사이클로 (이 스펙의 기반 재사용) |

## 아키텍처 (승인안: 기존 뷰어 SPA에 라우트 추가)

`apps/web` SPA에 `/capture` 라우트를 추가한다. 근거:

- 서버가 이미 이 SPA를 catch-all로 서빙 (`apps/server/main.py:219-252`, `_mount_viewer_spa`) → **서버 서빙 코드 변경 0**
- cloudflared 퀵터널이 서버 origin 전체를 https로 노출 → 캡처 API의 보안 컨텍스트 요건 충족
- 라우팅은 `apps/web/src/App.tsx`의 pathname 분기에 `/capture` 추가 (기존 `/v/<token>`, `/admin/audio-stats` 패턴과 동일)

기각한 대안: 별도 앱 신설(서버 마운트+CI 추가 비용), 데스크탑 콘솔 UI 공유 패키지화(대규모 리팩토링, 웹 콘솔 단계에서 재검토).

## 화면 상태 머신

```
로그인 → 준비(회의 제목 입력, 디바이스 등록 확인) → 캡처 중 → 종료
```

- **로그인**: `POST /api/v1/auth/login {email,password}` → 운영자 토큰(sessionStorage)
- **디바이스 등록**: localStorage에 디바이스 키 없으면 `POST /api/v1/devices/self-enroll` → 키 저장(브라우저 프로필당 1개). 키 무효(WS 1008) 시 재등록 유도
- **회의 시작**: `POST /api/v1/sessions {title, client_label:"web-capture", visibility}` → `session_id`, `viewer_url` 수령 → QR 표시
- **캡처**: getDisplayMedia로 탭 선택("오디오 공유" 체크 안내 오버레이 포함) → 오디오 파이프라인 가동
- **종료**: `audio.stopped` 전송 → `POST /api/v1/sessions/{id}/end`

## 오디오 파이프라인 (신규 핵심 모듈 — 사이드카의 브라우저 등가물)

```
탭 MediaStream ──┐
                 ├→ AudioContext({sampleRate:16000}) → AudioWorklet
마이크(옵션) ─────┘      → Float32→s16le 변환 → 640B(20ms=320샘플) 프레이밍 → WS 바이너리
```

- `AudioContext({sampleRate:16000})`로 브라우저 내장 리샘플링 사용 (수동 리샘플러 작성 금지 — 불필요)
- 마이크 혼합: `getUserMedia({audio:{echoCancellation:true}})` → GainNode로 믹싱. 캡처 중 on/off 가능
- 레벨미터: 전송 직전 청크에서 dBFS 계산 (`apps/client_sidecar/audio/rms.py`의 `pcm16_dbfs` JS 포팅)
- getDisplayMedia는 `{video:true, audio:true}`로 호출(오디오 단독 요청 불가). 비디오 트랙은 렌더링하지 않고 유지만.

## 서버 프로토콜 계약 (탐사로 확정, 서버 변경 없음)

- 접속: `wss://<host>/ws/sidecar?key=<디바이스키>&session=<session_id>` (`apps/server/ws/sidecar.py:253-315`) — 인증은 쿼리 파라미터뿐, 헤더 불필요 → 브라우저 WebSocket으로 가능
- 오디오 형식(고정): **16000Hz · mono · pcm_s16le**, 관례 청크 640B/20ms (`apps/client_sidecar/config/audio.py:8-12`)
- 전송 순서(필수): ① `{"type":"audio.started","sample_rate":16000,"channels":1,"format":"pcm_s16le","started_at":<ISO8601>}` JSON을 **바이너리보다 먼저** (안 지키면 서버가 오디오를 조용히 폐기, `sidecar.py:456-457`) ② 바이너리 PCM 청크 ③ 50청크(~1s)마다 `{"type":"chunk_meta","seq":n,"started_at":<ISO>}` ④ 종료 시 `{"type":"audio.stopped","reason":<str|null>}`
- 텍스트 프레임은 위 3종 JSON만 보낼 것 (그 외는 서버가 1008로 끊음)
- `CAPTURE_STATUS`·RMS 마커는 데스크톱 로컬 전용 — 웹에서는 불필요
- 세션-디바이스 바인딩: 최초 접속 디바이스가 세션을 점유, 타 디바이스 재접속은 1008
- 자막 미리보기: 기존 `GET /ws/operator?session=<id>&access=<운영자토큰>` 재사용, 최근 자막 몇 줄만 표시(풀 페이싱 미적용). 뷰어 QR은 데스크탑 콘솔과 동일한 `qrcode` 패키지 사용(`apps/web`에 의존성 추가)

## 에러 처리

| 상황 | 처리 |
|---|---|
| 비Chromium / getDisplayMedia 미지원 | 시작 전 감지 → "Chrome/Edge로 접속하세요" 배너 |
| 비보안 컨텍스트(`http://<LAN IP>`) | `window.isSecureContext` 검사 → "https(터널) 주소로 접속하세요" 배너 |
| 탭 오디오 공유 체크 누락(최빈 실수) | 오디오 트랙 부재 즉시 감지 → "'탭 오디오 공유' 체크 후 다시 선택" 재시도 유도. 캡처 중 무음 지속 시(레벨미터) 동일 안내 |
| WS 1008 닫힘 | 사유별 한국어 메시지: 다른 기기 캡처 중 / 세션 종료됨 / 키 무효(→재등록 흐름) / 최대 회의시간 초과 |
| WS 끊김 | 백오프 재접속(2s/10s/30s, 사이드카와 동일), 재접속 시 `audio.started` 재전송 |
| 공유 중지·탭 닫힘 | video/audio track `ended` 이벤트 감지 → 회의 유지한 채 "캡처 끊김 — 다시 탭 선택" 상태 |

## 테스트

- 단위: PCM 변환·640B 프레이밍·백오프·상태 머신·dBFS (기존 콘솔 `.test.ts` 패턴 준수)
- E2E: 이 Mac Chrome + 실제 구글밋 회의 → 뷰어 자막 확인 (1차 검증). Windows 실기기는 릴리스 검증 목록에 추가
- 회귀 방지: "오디오 수신 프로토콜 변경 시 사이드카·웹 두 클라이언트 동시 검증" 항목을 릴리스 체크리스트에 명시

## 범위 밖

- 지식저장고·과거 회의 등 콘솔 기능의 웹 이식 (후속 사이클)
- Safari/Firefox 지원 (안내 배너만)
- 수동 리샘플러, 서버 프로토콜 변경, 뷰어 변경
