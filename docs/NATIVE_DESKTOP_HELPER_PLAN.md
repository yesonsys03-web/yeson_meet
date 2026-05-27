# 네이티브 데스크톱 헬퍼 전환 기획

> 목적: Voicemeeter(Windows) / BlackHole(macOS) 같은 외부 가상 오디오 장치 없이, yeson-meet 데스크톱 앱이 회의 오디오를 직접 캡처해 실시간 번역 자막을 제공하는 방향을 정리한다.
> 범위: 구현 계획이 아닌 제품/기술 방향 기획. 현재 WebSocket 기반 서버 전송 구조는 유지한다.

---

## 1. 결론 요약

장기 제품 방향은 **네이티브 데스크톱 헬퍼 방식이 더 적합하다.**

현재 Voicemeeter / BlackHole 방식은 MVP 검증에는 빠르지만, 실제 사용자에게는 설치·권한·오디오 라우팅 설정 부담이 크다. 반면 네이티브 헬퍼 방식은 개발 난이도와 배포 복잡도는 증가하지만, 사용자는 “앱 설치 → 권한 허용 → 회의 시작” 흐름만 따르면 되므로 제품화에 유리하다.

추천 방향은 다음과 같다.

1. **MVP-α / 내부 검증**: 현재 방식 유지
2. **MVP-β / 고객용 전환**: 네이티브 오디오 캡처 헬퍼 도입
3. **안정화 이후**: Voicemeeter / BlackHole은 fallback 또는 개발자 모드로 격하

---

## 2. 현재 방식과 네이티브 헬퍼 비교

| 항목 | 현재 방식: Voicemeeter / BlackHole | 전환안: 네이티브 데스크톱 헬퍼 |
|---|---|---|
| 사용자 설치 경험 | 외부 드라이버 설치, 재부팅, 수동 라우팅 필요 | yeson-meet 앱 설치 + OS 권한 허용 중심 |
| 초기 개발 속도 | 빠름 | 느림 |
| 운영 안정성 | 사용자 설정 실수에 취약 | 앱이 상태 감지·복구·안내 가능 |
| 고객 온보딩 | 비기술 사용자에게 어려움 | 제품화에 적합 |
| 장애 진단 | 외부 앱 설정까지 확인해야 함 | yeson-meet 진단 화면에서 통합 확인 가능 |
| OS 업데이트 대응 | 외부 드라이버 의존 | 자체 코드/권한 정책 대응 필요 |
| 배포 복잡도 | 낮음 | 코드 서명, notarization, updater, 권한 안내 필요 |
| 장기 확장성 | 제한적 | 앱별 캡처, 상태 UI, 자동 진단, 정책 제어 가능 |

판단: **실제 고객에게 제공할 제품이라면 네이티브 헬퍼가 더 낫다.** 다만 MVP 단계에서는 지금 방식이 빠르고 검증 비용이 낮으므로 즉시 폐기할 필요는 없다.

---

## 3. 목표 사용자 경험

### 3.1 이상적인 흐름

1. 사용자가 yeson-meet 데스크톱 앱을 실행한다.
2. 앱이 오디오 캡처 가능 여부를 자동 진단한다.
3. 필요한 경우 OS 권한 요청 화면을 안내한다.
4. 사용자가 회의 앱(Zoom, Teams, Google Meet 등)을 실행한다.
5. yeson-meet에서 “회의 시작”을 누른다.
6. 앱이 시스템/앱 오디오를 캡처해 서버로 전송한다.
7. viewer에는 실시간 한국어 번역 자막이 표시된다.

### 3.2 사용자가 몰라도 되는 것

- 가상 오디오 장치 이름
- 시스템 출력 장치 변경
- Multi-Output Device 생성
- Voicemeeter 라우팅 버튼
- BlackHole 설치 여부
- sidecar 실행 명령

---

## 4. 권장 아키텍처 방향

현재 큰 구조는 유지한다.

```text
Desktop App (Tauri)
  ├─ Operator UI
  └─ Python Sidecar  (Tauri externalBin 으로 라이프사이클 관리)
       ├─ Native Audio Helper  (sidecar 가 자식 프로세스로 spawn / stdout pipe 로 PCM 수신)
       │    ├─ Windows: WASAPI loopback 기반 캡처
       │    └─ macOS: ScreenCaptureKit 기반 시스템 오디오 캡처
       └─ Transport
            └─ WSS audio chunks → FastAPI Gateway → Gemini Live → viewer subtitles
```

> Lifecycle 책임: Tauri → Python sidecar → Native helper 의 1-자식 chain. 자세한 결정 근거는 `docs/INTEGRATION_DESIGN.md` §3.2.

핵심은 **서버 전송 방식(WebSocket)이나 viewer 자막 구조를 바꾸는 것이 아니라, 클라이언트의 오디오 입력 계층만 교체하는 것**이다.

### 4.1 Windows 방향

- 우선 후보: **WASAPI loopback**
- 목표: 시스템 출력 또는 선택한 출력 장치의 loopback 오디오 캡처
- 장점:
  - 별도 Voicemeeter 설치 없이 시스템 오디오 캡처 가능
  - Windows 데스크톱 앱에서 일반적으로 쓰이는 방식
  - 현재 “회의 상대방 음성만 캡처” 요구와 잘 맞음
- 검토 포인트:
  - 특정 앱 오디오만 분리 캡처 가능한지
  - 기본 출력 장치 변경 시 자동 추적
  - 블루투스 헤드셋, USB 오디오 인터페이스, HDMI 출력 대응
  - 샘플레이트 변환 및 mono downmix 안정성

### 4.2 macOS 방향

- 우선 후보: **ScreenCaptureKit 기반 시스템 오디오 캡처**
- 목표: BlackHole 없이 시스템 또는 특정 앱 오디오 캡처
- 장점:
  - 가상 오디오 드라이버 설치 부담 제거
  - macOS 권한 플로우 안에서 안내 가능
  - 장기적으로 앱/윈도우 선택 캡처 UX와 연결 가능
- 최소 지원 macOS: **14.2 (Sonoma)** — 그 미만 버전은 BlackHole compatibility mode로 안내
- 검토 포인트:
  - Screen Recording / System Audio 관련 권한 안내
  - 권한 거부·미부여 상태의 복구 UX
  - Intel Mac / Apple Silicon 차이

---

## 5. 단계적 전환 계획

### Phase 0 — 현재 방식 유지 및 기준선 확보

목표: 네이티브 전환 전, 현재 방식의 품질 기준을 숫자로 남긴다.

산출물:

- Voicemeeter / BlackHole 기준 지연 시간 측정
- 1분 영어 영상 기준 chunks/sec, drop count, 자막 P50/P95 기록
- 사용자 셋업 실패 케이스 목록화
- 현재 방식 fallback 문서 유지

성공 기준:

- 기존 방식으로 최소 1개 Windows PC, 1개 Mac에서 E2E 기준선 확보

### Phase 1 — macOS native capture PoC

목표: BlackHole 없이 macOS 시스템 오디오를 캡처해 기존 파이프라인에 연결 가능함을 검증하고, 이후 Windows 구현이 끼워질 수 있는 공통 인터페이스를 정의한다. dev 환경과 일치해 iteration 비용이 낮고, ScreenCaptureKit이 WASAPI보다 큰 unknown이라 risk-first 검증에 부합한다.

산출물:

- ScreenCaptureKit 기반 오디오 캡처 PoC (macOS 14.2+ 기준)
- **OS-agnostic `AudioCapture` 추상화 인터페이스 정의** — Phase 2의 Windows 구현이 끼워질 자리
- 권한 요청/거부/재시도 UX 초안
- Apple Silicon 동작 검증 (Intel Mac은 비-우선)

성공 기준:

- BlackHole 미설치 Mac에서 회의/영상 오디오가 서버로 안정 전송됨
- 권한 미부여 상태를 앱이 명확히 감지하고 안내함
- 캡처 레이어 밖 공통 코드(샘플레이트 변환·mono downmix·sidecar 전송)가 OS별 분기 없이 재사용되도록 설계됨

### Phase 2 — Windows native capture PoC

목표: Voicemeeter 없이 Windows 시스템 오디오를 캡처해 Phase 1에서 정의한 `AudioCapture` 추상화의 두 번째 구현체로 끼워넣는다.

산출물:

- WASAPI loopback 캡처 PoC (`AudioCapture` 인터페이스 구현)
- 16kHz mono PCM 변환
- 기존 sidecar 전송 포맷과 호환성 확인
- 장치 변경 감지 초안

성공 기준:

- Zoom / Teams / 브라우저 영상 소리가 Voicemeeter 없이 서버에 50 chunks/sec 수준으로 전송됨
- 사용자 수동 오디오 라우팅 없이 자막 생성 가능
- 캡처 레이어 외 공통 코드가 Phase 1과 동일 코드 경로로 재사용됨을 코드 리뷰에서 확인

### Phase 3 — 데스크톱 앱 통합

목표: 현재 Tauri 데스크톱 앱에서 native helper를 실행·상태 관리·진단할 수 있게 한다.

산출물:

- 캡처 상태 표시: 정상 / 권한 필요 / 장치 없음 / 무음 / 전송 실패
- 오디오 레벨 미터
- 캡처 소스 선택 또는 자동 선택
- sidecar lifecycle 관리
- 오류 발생 시 사용자 친화적 복구 안내

성공 기준:

- 사용자가 외부 오디오 도구를 설치하지 않고 앱 UI만 따라 회의 자막을 시작할 수 있음

### Phase 4 — 배포 안정화

목표: 고객 PC에 안전하게 배포 가능한 패키징 체계를 만든다.

산출물:

- Windows installer
- macOS code signing / notarization
- 자동 업데이트 정책
- 권한/보안 안내 문서
- 운영자용 진단 로그 export

성공 기준:

- 비개발자 PC에서 설치부터 첫 자막까지 반복 가능한 절차 확보

---

## 6. 제품 정책 제안

### 6.1 기본 모드

네이티브 캡처가 안정화되면 기본 모드는 다음으로 전환한다.

```text
기본: Native Audio Capture
대체: Voicemeeter / BlackHole Compatibility Mode
개발: Manual device selection
```

### 6.2 fallback 정책

네이티브 캡처가 실패하는 환경을 위해 기존 방식을 즉시 제거하지 않는다.

- Windows: Voicemeeter compatibility mode 유지
- macOS: BlackHole compatibility mode 유지
- 앱 진단 화면에서 “네이티브 캡처 실패 → 호환 모드 안내” 제공

### 6.3 지원 우선순위

1. Windows 회의실 PC
2. macOS 회의실 PC
3. 브라우저 탭 전용 캡처 / Chrome Extension 방식은 별도 후보로 보류

이유: 현재 제품의 핵심은 회의실 PC에서 전체 회의 오디오를 안정적으로 받아 자막을 제공하는 것이므로, 특정 브라우저 탭에 묶이는 방식보다 OS 레벨 캡처가 더 범용적이다.

---

## 7. 주요 리스크

### 7.1 OS 권한과 보안 정책

macOS는 권한 정책 변화에 민감하다. ScreenCaptureKit 기반 접근은 사용자 권한 안내와 실패 복구 UX가 중요하다.

대응:

- 앱 첫 실행 진단 플로우 제공
- 권한 미부여 상태를 명확히 표시
- 설정 앱으로 이동하는 안내 제공

### 7.2 다양한 오디오 장치

회의실 PC는 HDMI, USB 오디오 인터페이스, 블루투스 헤드셋, 캡처보드 등 장치 구성이 다양할 수 있다.

대응:

- 자동 선택 + 수동 선택 모두 제공
- 장치 변경 이벤트 감지
- 레벨 미터로 “지금 소리가 들어오는지” 즉시 확인

### 7.3 앱별 오디오 분리

전체 시스템 오디오를 캡처하면 알림음, 다른 영상, 시스템 효과음이 섞일 수 있다.

대응:

- 1차는 전체 시스템 오디오 캡처로 단순화
- 2차에서 앱별 캡처 또는 제외 목록 검토
- 회의 전 “알림 끄기” 체크리스트 제공

### 7.4 배포와 신뢰

네이티브 캡처 앱은 보안 경고, 코드 서명, 백신 오탐 이슈가 생길 수 있다.

대응:

- 코드 서명 필수
- 설치 파일 출처 명확화
- 진단 로그에 민감정보 미포함
- Gemini API Key는 기존처럼 서버에만 보관

---

## 8. 성공 지표

네이티브 헬퍼 전환의 성공 여부는 기능 구현 여부보다 사용자 설치 성공률과 운영 안정성으로 판단한다.

권장 지표:

- 첫 설치 후 첫 자막까지 걸리는 시간
- 비개발자 사용자의 셋업 성공률
- 회의 시작 전 오디오 진단 통과율
- 30분 회의 중 캡처 중단 횟수
- chunks/sec 안정성
- 자막 표시 지연 P50 / P95
- 지원 요청 중 “오디오 설정 문제” 비율

목표 예시:

- 첫 자막까지 5분 이내
- 30분 회의 중 캡처 중단 0회
- 정상 환경에서 약 50 chunks/sec 유지
- 실시간 자막 P50 2초 이하 유지

---

## 9. 의사결정

현재 시점의 권장 의사결정은 다음과 같다.

1. **지금 당장 WebRTC로 전환하지 않는다.** 실시간 번역 자막 목적에는 현재 WebSocket audio chunk 구조가 충분히 현실적이다.
2. **오디오 캡처 계층만 네이티브 헬퍼로 전환한다.** 서버, Gemini 연동, viewer fan-out 구조는 유지한다.
3. **Voicemeeter / BlackHole은 단기 MVP와 fallback으로 유지한다.** 안정화 전까지 완전히 제거하지 않는다.
4. **macOS native capture를 먼저 한다.** dev 머신 환경(macOS Tahoe 26.x)과 일치해 iteration 비용이 낮고, ScreenCaptureKit이 더 큰 unknown이므로 risk-first 검증에 부합한다. 최소 지원 OS는 **macOS 14.2 (Sonoma)**.
5. **Windows native capture는 두 번째로 진행한다.** Phase 1에서 정의한 `AudioCapture` 추상화 인터페이스의 두 번째 구현체로 끼워넣어 캡처 레이어 외 공통 코드 재사용을 보장한다. WASAPI loopback은 검증된 패턴이라 unknown 위험이 낮다.

---

## 10. 한 줄 요약

yeson-meet의 제품화 방향은 **WebSocket 유지 + 네이티브 오디오 캡처 헬퍼 도입**이 가장 현실적이다. 현재 방식은 MVP 검증용으로 유지하고, 고객용 경험은 Voicemeeter / BlackHole 없는 네이티브 캡처로 전환하는 것이 좋다.
