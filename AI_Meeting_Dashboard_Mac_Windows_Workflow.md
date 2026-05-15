# AI 해외 미팅 실시간 통역 대시보드  
## Windows 1순위 / Mac 2순위 세팅 워크플로우

> 최신 구현 기준은 `docs/PRD.md`, `docs/ARCHITECTURE.md`, `docs/ROADMAP.md`를 따른다.  
> 이 문서는 초기 아이디어를 보존하되, MVP-α 기준에 맞춰 **Windows + Voicemeeter + 서버측 Gemini Live** 흐름으로 정리한다.

## 1. 프로젝트 목적

이 시스템은 “완벽한 자동 동시통역기”가 아니라, 해외 클라이언트와의 미팅에서 참석자들이 회의 흐름을 놓치지 않도록 돕는 **AI 회의 이해 보조 대시보드**입니다.

핵심 목표는 다음과 같습니다.

- 해외 클라이언트의 영어 발화를 실시간으로 이해
- 통역 담당자의 부담 감소
- MVP-β-3 이후 미팅 중 핵심 키워드, 일정, 수정 요청, 승인 여부 표시
- 회의 종료 후 자동 회의록 생성
- Windows 회의실 PC를 1순위로 대응하고, Mac은 2순위 검증 경로로 유지

---

# 2. 전체 공통 구조

```text
Google Meet / Zoom / Teams
↓
회의실 PC 시스템 오디오 캡처
↓
Voicemeeter(Windows 1순위) / BlackHole(Mac 2순위)
↓
Tauri Desktop App + Python sidecar
↓
사내 FastAPI 서버
↓
Gemini Live API (서버에서만 호출)
↓
실시간 한국어 자막 viewer
↓
회의 종료 후 자동 MD 리포트
```

---

# 3. Mac 구성 — 2순위 검증 경로

## 3.1 Mac용 2순위 구조

```text
Google Meet
↓
Mac 시스템 오디오
↓
BlackHole
↓
Python sidecar
↓
사내 FastAPI 서버
↓
Gemini Live API
↓
web viewer
```

## 3.2 필요한 도구

- BlackHole
- Python 3.10+
- sounddevice
- websockets
- PyQt5 또는 PySide6
- Device API Key (Gemini API Key는 서버에만 보관)
- SQLite

## 3.3 Mac 오디오 세팅

### 1) BlackHole 설치

BlackHole은 macOS에서 시스템 오디오를 다른 앱으로 전달하기 위한 가상 오디오 드라이버입니다.

```text
Google Meet 소리
→ BlackHole
→ Python sidecar
```

### 2) Audio MIDI 설정

macOS의 **Audio MIDI 설정**에서 멀티 출력 장치를 만듭니다.

```text
멀티 출력 장치
├─ 실제 스피커 / 헤드셋
└─ BlackHole
```

이렇게 설정하면 다음이 가능합니다.

- 사람은 스피커/헤드셋으로 회의 소리를 들음
- Python sidecar는 BlackHole을 통해 같은 소리를 입력받음

## 3.4 Mac 운영 방식

```text
Google Meet 출력 장치: 멀티 출력 장치
Python 입력 장치: BlackHole
```

---

# 4. Windows 구성 — MVP-α 1순위

## 4.1 Windows용 추천 구조

```text
Google Meet
↓
Windows 시스템 오디오
↓
Voicemeeter Banana
↓
Python sidecar
↓
사내 FastAPI 서버
↓
Gemini Live API
↓
web viewer
```

## 4.2 Windows 가상 오디오 선택지

### A안: Voicemeeter Banana — MVP-α 기본

실무 운영용 기본 선택지입니다.

장점:

- 실제 스피커/헤드셋과 sidecar 입력을 동시에 분리 가능
- 라우팅이 유연함
- 회의실 PC, 스튜디오 PC에서 안정적으로 운영 가능

### B안: VB-Audio Virtual Cable — 대체 후보

간단한 1차 테스트용입니다.

장점:

- 설치와 설정이 비교적 쉬움
- 단순한 구조
- 빠르게 프로토타입 제작 가능

단점:

- 스피커로 들으면서 동시에 sidecar에 보내는 설정이 다소 불편할 수 있음
- 회의실 환경에서는 Voicemeeter보다 유연성이 낮음

## 4.3 Windows 추천 세팅

```text
Google Meet 출력 장치: Voicemeeter Input
Voicemeeter A1: 실제 스피커 / 헤드셋
Voicemeeter B1: Python sidecar 입력용 가상 출력
Python sidecar 입력 장치: Voicemeeter Output
```

## 4.4 Windows 운영 방식

```text
Google Meet
→ Voicemeeter Input
→ A1: 사람이 듣는 스피커
→ B1: Python sidecar가 받는 가상 입력
```

## 4.5 Windows 주의사항

- 마이크 입력과 스피커 출력을 잘못 섞으면 에코가 생길 수 있음
- sidecar는 기본적으로 **상대방 음성만** 받게 구성하는 것이 안전함
- 본인 마이크까지 AI에 넣으면 회의 로그에는 유리하지만, 에코 제어가 필요함

---

# 5. Mac / Windows 비교표

| 항목 | Mac | Windows |
|---|---|---|
| 가상 오디오 장치 | BlackHole | VB-Audio Cable / Voicemeeter |
| 추천 방식 | BlackHole + 멀티 출력 (2순위) | Voicemeeter Banana (1순위) |
| 난이도 | 중간 | 중간 |
| 실무 안정성 | 높음 | Voicemeeter 사용 시 높음 |
| 스피커 동시 청취 | 멀티 출력 장치 | A1 출력 |
| sidecar 입력 | BlackHole | Voicemeeter Output |
| 회의실 운영 | 가능하나 2순위 | MVP-α 기본 |

---

# 6. AI 처리 구조

## 6.1 Gemini Live API 역할

MVP-α에서는 사내 서버가 Gemini Live API에 실시간 오디오 스트림을 보내고, 다음 작업만 수행합니다.

- 영어 음성 인식
- 짧은 한국어 회의용 자막 생성

키워드 추출, 액션 아이템 후보 추출, 상세 회의 로그 패널은 `docs/ROADMAP.md` 기준 **MVP-β-3**에서 확장합니다.

## 6.2 추천 모델

```text
gemini-live-2.5-flash-preview
```

이유:

- 실시간성 우수
- 비용 부담이 상대적으로 낮음
- 회의 이해 보조 용도에 적합

---

# 7. 시스템 프롬프트

```text
You are a real-time meeting assistant.

Rules:
- Translate English speech into concise Korean.
- Preserve technical keywords in English.
- Avoid long sentences.
- Focus on actionable information.
- Prioritize schedules, requests, revisions, approvals, and issues.
- Output subtitle-style Korean text.
- Maximum 2 short lines.
```

---

# 8. 실시간 자막 예시

## 영어 원문

```text
Can we finalize the layout revisions before Thursday?
```

## 대시보드 출력

```text
layout 수정본 목요일 전 확정 가능?
```

---

## 영어 원문

```text
The delivery might slip by one day because of render issues.
```

## 대시보드 출력

```text
render 문제로 delivery 하루 지연 가능성
```

---

# 9. 대시보드 UI 구성

## 9.1 실시간 자막 패널

```text
[CLIENT]

layout retake 재검토 요청
delivery 하루 지연 가능성
BG 수정본 목요일까지 필요
```

## 9.2 핵심 키워드 패널 — MVP-β-3

```text
delivery
retake
layout
deadline
approval
asset
render
issue
schedule
```

## 9.3 액션 아이템 패널 — MVP-β-3

```text
TODO
────────────────
[ ] BG 수정본 전달
[ ] layout 재확인
[ ] render issue 점검
[ ] 금요일 최종 승인 준비
```

## 9.4 실시간 회의 로그 — MVP-β-3

```text
10:42 delivery 하루 지연 가능성
10:45 layout retake 재검토 요청
10:51 BG 수정본 목요일까지 필요
10:55 금요일 최종 승인 예정
```

## 9.5 상태 패널

```text
오디오 연결: 정상
AI 연결: 정상
스트리밍: 정상
지연 시간: 1.2초
```

---

# 10. 회의 종료 후 자동 리포트

회의 종료 후 자동으로 다음 파일을 생성합니다.

```text
Meeting_Report_YYYY_MM_DD.md
```

포함 내용:

- 회의 요약
- 결정사항
- 액션 아이템
- 일정
- 리스크 / 이슈
- 주요 키워드
- 원문 로그
- 한국어 요약 로그

---

# 11. 추천 운영 형태

## 11.1 2모니터 구성

### 메인 모니터

- Google Meet

### 보조 모니터

- AI Meeting Dashboard

## 11.2 회의실 구성

```text
회의실 스피커
+
대형 모니터
+
AI 대시보드
```

회의 참석자는 클라이언트의 말을 들으면서 보조 모니터의 한국어 요약 자막을 함께 확인합니다.

---

# 12. MVP 1차 버전

처음부터 모든 기능을 넣지 말고 MVP-α는 아래 기능만 구현하는 것이 좋습니다.

- Windows + Voicemeeter 오디오 입력 1순위
- Mac + BlackHole 오디오 입력 2순위 검증
- 회의실 PC sidecar → 사내 서버 오디오 청크 전송
- 서버측 Gemini Live 호출
- 실시간 한국어 자막 viewer
- 회의 시작/종료
- 회의 종료 후 MD 리포트 생성

---

# 13. 2차 확장 기능

- 화자 구분
- 위험 키워드 감지
- 일정 자동 감지
- 중요 순간 북마크
- Slack 전송
- Notion 저장
- ShotGrid / FTrack 연동
- 회의록 자동 이메일 발송

---

# 14. 최종 개념

이 시스템은 단순 번역기가 아닙니다.

> 회의 내용을 실시간으로 구조화하고, 참석자들이 회의 흐름을 놓치지 않도록 돕는  
> **해외 미팅 AI 지휘 센터**입니다.

핵심은 다음입니다.

```text
회의를 번역하는 것
보다
회의 흐름을 가시화하는 것
```
