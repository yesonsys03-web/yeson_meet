# UI_DESIGN_SYSTEM — yeson-meet

> 최종 갱신: 2026-05-15
> 대상: `apps/desktop`(Operator 콘솔), `apps/web`(Viewer), `packages/ui`(공통 컴포넌트)
> **목적**: MVP-α 이후 회의 기록 검색·용어집·통계·키워드/액션 토글·모바일 native·사내 SDK 위젯 등 **확장 기능이 추가될 때 자막 메인 흐름을 깨지 않고 슬롯·토큰·composite·store slice를 추가만으로 끝나는 구조 보장**.

---

## 1. 5가지 확장성 원칙

### 원칙 1 — 레이아웃은 5슬롯 컴포지션
모든 페이지(Operator 라이브 콘솔 / Viewer / Admin / Glossary 등 미래 페이지 포함)는 다음 5개 슬롯으로만 구성한다.

```
┌────────────────────────────────────────────────┐
│ Header         (제목 / LIVE / 상태 인디케이터) │
├──────────────────────────────┬─────────────────┤
│                              │                 │
│ Main                         │  Side           │
│  (자막 / 핵심 콘텐츠 70%+)    │  (폴딩 가능)     │
│                              │  (β-3 키워드/액션) │
│                              │                 │
├──────────────────────────────┴─────────────────┤
│ Footer         (단축키 힌트 / 회의 종료 버튼)   │
└────────────────────────────────────────────────┘
                Floating Overlay (전체 위에 떠 있음)
                  (β-1 Slow-down 카드 / QR 풀스크린 등)
```

- **Main은 절대 안 밀린다.** 새 기능은 Side·Floating·Header·Footer 슬롯에만 들어갈 수 있음.
- 자막 영역은 화면 비율 **최소 60%** 보장 (글자 크기 토글 시에도).
- Side는 기본 폴딩, 토글로 펼침. 폴딩 시 Main 100%.

### 원칙 2 — 디자인 토큰 → CSS 변수
모든 색·spacing·radius·typography는 토큰으로 정의. 하드코딩된 Tailwind 값은 금지하고, 토큰 기반 Tailwind wrapper만 허용한다.

```css
/* packages/ui/src/tokens.css */
:root {
  /* Typography */
  --ys-font-base:           14px;
  --ys-font-subtitle-s:     24px;
  --ys-font-subtitle-m:     32px;
  --ys-font-subtitle-l:     44px;  /* MVP-α 기본 */
  --ys-font-subtitle-xl:    56px;
  --ys-font-subtitle-xxl:   72px;
  --ys-font-subtitle-current: var(--ys-font-subtitle-l);
  --ys-font-viewer-phone-default: 26px;
  --ys-font-viewer-laptop-default: 30px;
  --ys-font-family:         'Pretendard', 'Noto Sans KR', system-ui;
  --ys-font-weight-subtitle: 700;
  --ys-line-height-subtitle: 1.25;

  /* Color (다크 기본) */
  --ys-bg-base:             #0f172a;
  --ys-bg-elevated:         #1e293b;
  --ys-text-primary:        #f1f5f9;
  --ys-text-muted:          #94a3b8;
  --ys-accent:              #38bdf8;
  --ys-danger:              #f43f5e;

  /* Spacing scale (4의 배수) */
  --ys-space-1: 4px;  --ys-space-2: 8px;  --ys-space-3: 12px;
  --ys-space-4: 16px; --ys-space-6: 24px; --ys-space-8: 32px;

  /* Radius */
  --ys-radius-sm: 6px; --ys-radius-md: 10px; --ys-radius-lg: 16px;
}

/* 회의실 모드 (XXL 강제 + 사이드 폴딩) */
[data-mode="meeting-room"] {
  --ys-font-subtitle-current: var(--ys-font-subtitle-xxl);
}

/* 글자 크기 prop */
[data-subtitle-size="s"] { --ys-font-subtitle-current: var(--ys-font-subtitle-s); }
[data-subtitle-size="m"] { --ys-font-subtitle-current: var(--ys-font-subtitle-m); }
[data-subtitle-size="l"] { --ys-font-subtitle-current: var(--ys-font-subtitle-l); }
[data-subtitle-size="xl"] { --ys-font-subtitle-current: var(--ys-font-subtitle-xl); }
[data-subtitle-size="xxl"] { --ys-font-subtitle-current: var(--ys-font-subtitle-xxl); }
```

- **글자 5단계 · 회의실 모드 · 라이트/다크 토글은 모두 토큰 변수 교체만으로 동작** → β-2 비용 최소화.
- Tailwind는 토큰을 가져다 쓰는 wrapper로만 사용: `text-[length:var(--ys-font-subtitle-current)]`.

### 원칙 3 — 컴포넌트 3층 분리

```
packages/ui/
├── primitive/      ← shadcn/ui 그대로 (Button, Dialog, Tooltip 등)
├── composite/      ← 도메인 합성 컴포넌트
│   ├── SubtitleCard.tsx       (자막 한 줄 + partial→final 갱신)
│   ├── SubtitleStream.tsx     (자막 목록 + 자동 스크롤)
│   ├── SidePanel.tsx          (폴딩 가능 사이드 컨테이너)
│   ├── ToggleBar.tsx          (viewer "더 보기" 토글)
│   ├── KeywordChip.tsx        (β-3)
│   ├── ActionItemRow.tsx      (β-3)
│   ├── StatusIndicator.tsx    (오디오/AI/지연)
│   ├── FloatingCard.tsx       (β-1 Slow-down)
│   └── QrPoster.tsx           (회의 시작 QR 전체화면)
└── layout/         ← 5슬롯 레이아웃 컨테이너
    ├── AppShell.tsx           (Header + Main + Side + Footer + Overlay 슬롯)
    └── ConsoleShell.tsx       (좌측 nav + AppShell 합성)
```

- **새 기능은 composite 추가만**. primitive 안 건드림.
- 같은 composite를 Operator·Viewer·Admin이 재사용.
- composite는 데이터 prop만 받고 store 직접 접근 X (테스트 용이성).

### 원칙 4 — 라우팅 확장 자리 미리 박기

**Operator (`apps/desktop`)** — 좌측 nav를 미리 5칸으로 잡되 MVP-α는 `meet`·`settings`만 활성.

```
/login
/console
  ├── /meet               ← MVP-α 활성 (라이브 콘솔)
  ├── /history            ← placeholder, "준비 중" 화면 (β-1 이후)
  ├── /glossary           ← placeholder (β-1)
  ├── /admin              ← placeholder (β-7 운영 자동화)
  └── /settings           ← MVP-α 시드 (글자 크기·로그아웃만)
```

**Viewer (`apps/web`)** — 토큰 단위 라우트 + 향후 sub-view.

```
/v/<token>                ← MVP-α 자막 풀스크린
/v/<token>/keyword        ← β-3 "더 보기" 키워드 펼침
/v/<token>/action         ← β-3 액션 펼침
/v/<token>/log            ← β-3 회의 로그
/v/?pin=<6자리>           ← β-3 PIN 입력
```

- **MVP-α placeholder는 Operator 비활성 라우트(`/console/{history,glossary,admin}`)에만 둔다.** Viewer β-3 sub-route는 문서상 예약만 하고 MVP-α 구현 범위에서는 제외한다.
- Operator nav 메뉴 항목은 disabled 상태로 표시 (사용자가 미래 기능을 인지).
- β 진입 시 Operator placeholder는 실제 페이지로 교체하고, Viewer sub-route는 β-3에서 예약한 경로대로 추가한다.

### 원칙 5 — WS 단일 store + selector

```typescript
// packages/ui/store/index.ts (또는 apps별)
import { create } from 'zustand';
import type { DomainEvent } from '@yeson-meet/events';

type MeetingStore = {
  // 도메인 slice
  utterances: Utterance[];
  status: { audio_ok: boolean; ai_ok: boolean; latency_ms: number };

  // β에서 추가될 slice는 MVP-α 타입에 넣지 않는다.
  // β-3: keywords: Keyword[] / actions: ActionItem[]
  // β-1: bookmarks: Bookmark[] / notes: Note[]

  // actions
  applyEvent: (e: DomainEvent) => void;
};
```

- **WebSocket 이벤트는 단일 store에 적층**, 컴포넌트는 selector로 구독.
- 새 이벤트(β-3 `keyword.detected` 등) 추가 시 **store slice 1개 + composite 1개 추가만**. 기존 뷰 안 건드림.
- store는 도메인 이벤트 외 UI 상태(폴딩·글자 크기·토글)도 보관하지만 슬라이스 분리: `useMeetingStore` / `useUiStore`.

---

## 2. 슬라이스별 UI 도입 범위

| Slice | Operator | Viewer | packages/ui |
|---|---|---|---|
| **0** | (없음) | "Hello yeson-meet" | primitive 빈 패키지 |
| **1** | (없음) | `SubtitleStream` 1줄 표시 | `SubtitleCard`, `SubtitleStream` composite + `AppShell` 레이아웃 + 토큰 1차 정의 |
| **2** | (없음) | 동일 | 오디오 상태 인디케이터 추가 |
| **3** | (없음) | partial→final 갱신, `StatusIndicator` | 토큰 5단계 정의(사용은 β-2) |
| **4** | `ConsoleShell` + `/console/meet` (자막 + Footer 종료 버튼), `QrPoster` | 종료 화면 | `ConsoleShell`, `QrPoster`, placeholder 페이지 4개 |
| **5** | 다중 세션 안정화 | viewer backfill | (없음, 안정화만) |
| **β-1** | 단축키 + `FloatingCard`(Slow-down) + 인라인 메모 | (없음) | `FloatingCard`, `NoteInline`, `Glossary*` |
| **β-2** | 글자 5단계 토글, 회의실 모드 | 글자 크기 토글 | 토큰 변수 prop만 변경 (composite 변경 X) |
| **β-3** | Side에 키워드/액션/로그 패널 | `ToggleBar` + `/v/<t>/keyword` 등 sub-route | `ToggleBar`, `KeywordChip`, `ActionItemRow` |
| **β-4** | (사내 SDK 위젯 슬롯 가능) | (동일) | `SdkWidgetSlot` 인터페이스 |

---

## 3. 디자인 토큰 네이밍 규칙

- 접두사 `--ys-` (yeson)
- 분류: `font`, `bg`, `text`, `border`, `space`, `radius`, `shadow`, `accent`, `danger`, `success`
- 단위는 항상 토큰 정의 안에 포함 (`px`, `ms` 등). 사용 시 단위 X.
- 상태/모드는 data-attribute로 토글: `[data-mode="meeting-room"]`, `[data-subtitle-size="xxl"]`, `[data-theme="dark"]`.

금지 사항:
- composite 안에 **하드코딩 색·spacing·폰트 크기** 작성 금지
- Tailwind `text-[40px]` 같은 임의 값 사용 금지 — 토큰 변수만
- `useEffect`로 DOM에 직접 style 주입 금지 — data-attribute로

---

## 4. 접근성 / 가독성 강제 사항

- 모든 텍스트 **최소 14px**. 12px 사용 자체 금지 (lint 룰 검토).
- 자막은 항상 **Bold 700+**.
- 대비비 **WCAG AAA (7:1) 이상** — 색 토큰 정의 시점에 검증.
- 한 줄 글자 수 캡: 운영자 콘솔 ≤ 28자, 폰 ≤ 18자.
- focus ring 항상 보이게 (운영자가 키보드만으로 운영 가능해야 함, PRD §1.1).

---

## 5. 디자이너 핸드오프 컨벤션

(MVP-α 동안 디자이너 없이 개발자 1명 진행. β 이후 디자이너 합류 시.)

- Figma 또는 Penpot 파일은 `docs/design/` 디렉토리에 export.
- 토큰을 Figma Variables로 정의 → 코드 토큰과 1:1 매핑 (이름 동일).
- composite는 Figma Components와 1:1 매핑 (이름 동일).
- 새 기능 디자인 시 5슬롯 어디에 들어가는지 먼저 명시.

---

## 6. 변경 정책

- 토큰 추가: 자유 (semver patch).
- 토큰 이름 변경: 전체 grep + 코드/디자인 동시 변경 (semver minor).
- 슬롯 추가: 매우 신중. 5슬롯으로 안 풀리는 케이스가 나오면 먼저 ARCH 회의 (semver major).
- composite 시그니처 변경: 호출자 모두 업데이트 (semver minor).

---

## 참고

- PRD §10 결정 로그 — UI 확장성 정책 행
- ARCH §2.1 / §2.6 — Operator / Viewer 슬롯 적용
- WORKFLOW_SOLO_AI §4 — Slice 4 락 항목
- ROADMAP Slice 4 — `ConsoleShell` + 토큰 1차 정의 산출물
