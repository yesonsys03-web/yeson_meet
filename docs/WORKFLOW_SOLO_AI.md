# WORKFLOW — 1인 + AI 병렬 작업

> 개발자 **혼자** 진행하되 **Claude(AI)를 팀처럼 운영**해서 병렬로 lane을 돌리는 프로세스.  
> "사람 1명 + AI 여러 에이전트 동시" 모델.

---

## 0. 전제 / 역할 분담

| 역할 | 책임 |
|---|---|
| **너 (개발자 1명)** | 의사결정, 검토, 머지, 시뮬레이션 검증, 시크릿 보관, 외부 협조 (시스템 부원 / Gemini Key 등) |
| **Claude (AI 에이전트들)** | 코드 작성, 테스트, 1차 리뷰, 문서 동기화, 외부 SDK 조사 |

→ **너의 시간은 "결정과 검증"에만 쓰고, 코드 작성·테스트는 AI가 한다.**

---

## 1. 슬라이스 단위 사이클 (반복)

```
[1] 슬라이스 시작
     ↓
[2] Plan ────── planner 에이전트가 lane별 분해 + 의존성 분석
     ↓
[3] Parallel Execute ── executor 에이전트 N개 single-message로 동시 dispatch
     ├─ Lane A (server)   executor agent
     ├─ Lane B (sidecar)  executor agent
     ├─ Lane C (desktop)  executor agent
     └─ Lane D (web)      executor agent
     ↓
[4] Review ──── code-reviewer 에이전트 + 너 빠른 검토
     ↓
[5] Verify ──── verifier 에이전트가 완료 기준 자동 검증
     ↓
[6] Merge & Tag — 너가 머지 + git tag mvp-alpha-s<N>
     ↓
[7] 다음 슬라이스
```

**각 슬라이스 1~3.5일.**

---

## 2. AI 에이전트 카탈로그 (어디서 어떻게 쓰는가)

OMC 기준 사용 가능한 에이전트:

| 에이전트 | 모델 | 언제 호출 | 입력 |
|---|---|---|---|
| `planner` | Opus | 슬라이스 시작 시 1회 | "Slice 1을 5 lane으로 분해" |
| `executor` | Sonnet (간단) / Opus (복잡) | Lane별 1개씩, 병렬 | "Lane A: WS Hub + Auth + 5 테이블 마이그" |
| `explore` | Sonnet | API 변경 영향 분석 | "X 모델 수정의 호출자 목록" |
| `code-reviewer` | Sonnet | 머지 전 1차 리뷰 | 생성된 PR 또는 diff |
| `verifier` | Opus | 슬라이스 종료 시 | "Slice 1 완료 기준 6개 모두 충족 검증" |
| `debugger` | Sonnet | 테스트 실패 / 버그 | 스택 트레이스 + 재현 시나리오 |
| `document-specialist` | Sonnet | 새 SDK 도입 | "google-genai Live API 사용법" |
| `security-reviewer` | Opus | 인증 / 시크릿 / 외부 노출 변경 | 해당 PR |
| `writer` | Haiku | 문서 동기화 | "PRD/ARCH에 Slice 3 변경 반영" |

---

## 3. Lane 병렬 dispatch — 핵심 패턴

### 단일 메시지에 multiple Task 호출 = 동시 실행

```
너: "Slice 1 시작해줘"

Claude:
1) planner 에이전트 1회 호출 → 의존성 분해
2) 동일 응답에 executor 3개 동시 dispatch:
   - executor("Lane A: WS Hub + Auth + 5 테이블 마이그", model=opus)
   - executor("Lane B: 가짜 자막 발생기", model=sonnet)
   - executor("Lane D: viewer WS 구독 + 자막 1줄", model=sonnet)
3) 결과 수집 → code-reviewer로 통과 검증
4) verifier로 완료 기준 검증
5) 너에게 머지 요청
```

### 병렬 가능 vs 순차 규칙

| 조합 | 병렬? | 이유 |
|---|---|---|
| Lane A + B + D (Slice 1) | ✅ | 서로 다른 디렉토리 |
| Lane A + E (Alembic + Docker) | ❌ | 둘 다 `apps/server/db/`와 `deploy/` 접점 |
| Lane B + C (사이드카 + Tauri) | ⚠️ | Tauri sidecar IPC 인터페이스 합의 후 분리 |
| 같은 디렉토리 두 lane | ❌ | 순차 |

→ **planner 에이전트가 의존성 표를 먼저 만든다.** 그 표대로 dispatch.

---

## 4. API contract / 이벤트 스키마 — 단일 소스 정책

**Claude가 임의 결정하면 lane 간 drift 발생.** 그래서 슬라이스 시작 시 너가 1차 락:

| 슬라이스 | 락 인할 것 |
|---|---|
| S0 | 모노레포 구조, lint/CI, 환경변수 이름, **사이드카 ↔ 데스크톱 통신 방식**(Tauri IPC vs 127.0.0.1 WS), **사이드카 배포 방식**(uv dev / PyInstaller / Tauri externalBin) |
| S1 | `events.py` DomainEvent 5종 (시작 시 1개만), 5 테이블 DDL, REST 3 엔드포인트, viewer GET `?since=<seq>` 시그니처 미리 박기 |
| S2 | 오디오 청크 binary 포맷 (16kHz/mono/20ms) |
| S3 | Gemini 응답 → DomainEvent 매핑, **`STTProvider` / `TranslationProvider` 인터페이스 시그니처**(구현체는 `GeminiLiveProvider` 1개), latency budget 4구간 분해 |
| S4 | MD 리포트 포맷, 회의 종료 흐름, **5슬롯 레이아웃 컨테이너**(`AppShell` / `ConsoleShell`) + **디자인 토큰 1차 정의**(`packages/ui/src/tokens.css`) + **라우팅 placeholder**(`/console/{history,glossary,admin}` 빈 페이지). 상세 `docs/UI_DESIGN_SYSTEM.md` |
| S5 | 부서/visibility 스키마, 오프라인 큐 재전송 프로토콜 |

→ **이 락이 곧 lane별 executor 에이전트에게 주는 input.**

---

## 5. 너의 시간 보호 — 검증 자동화

```
자동 (Claude가 처리)
├─ Lint (Ruff / ESLint / Prettier)
├─ 타입 체크 (mypy / tsc --noEmit)
├─ 단위 테스트 (pytest / vitest)
├─ 통합 테스트 (Slice별)
├─ code-reviewer 1차 리뷰
└─ verifier 완료 기준 자동 검증

너만 할 수 있는 것
├─ 슬라이스 시작 시 의사결정 (5~15분)
├─ 슬라이스 종료 시 PR/diff 검토 (10~30분)
├─ 실제 회의 시뮬레이션 (Windows Voicemeeter 켜고 영어 영상 재생)
└─ 막힘 시 결정 응답 (15분 이내)
```

→ **슬라이스 1~3.5일 중 너의 실제 시간은 총 1~3시간.**

---

## 6. Git 워크플로 (Solo)

### 브랜치 (단순화)
- `main` — 모든 작업, 또는 짧은 단명 브랜치
- 브랜치 안 만들어도 됨 (Solo니까)

### 태그 / 마일스톤
```
mvp-alpha-s0   ← Slice 0 완료
mvp-alpha-s1   ← Slice 1 완료
...
mvp-alpha      ← Slice 5 완료
mvp-beta-1     ← β-1 완료
```

### 슬라이스 시작/종료 의식
- 시작: working tree clean 확인
- 종료: `git add . && git commit && git tag mvp-alpha-s<N> && git push --tags`
- 실패 시 롤백: `git reset --hard mvp-alpha-s<N-1>`

---

## 7. AI 병렬 작업에서 자주 발생하는 함정

| 함정 | 예방 |
|---|---|
| 동일 파일 동시 수정 → 마지막 lane이 다른 lane 덮어씀 | planner의 의존성 표 먼저. 같은 파일 건드리는 lane은 순차로. |
| API contract drift (server는 v1, client는 v0) | events.py 1곳에서 정의 → TypeScript 자동 export. 수동 동기화 금지. |
| 테스트 누락 (executor가 시간 절약하려고) | executor 호출 시 "단위 테스트 + 통합 테스트 포함" 명시 |
| Claude가 너에게 안 물어보고 결정 | 슬라이스 시작 시 "모호하면 물어볼 것" 명시 + 모호 항목 미리 답해두기 |
| 너의 검토 병목 | 슬라이스를 작게(1~3.5일). 검토 시간 30분 안 넘게. |
| 시크릿 leak (Claude가 .env에 실제 키 쓸 수도) | `.env`는 .gitignore + Claude에게 명시 "실제 키는 절대 적지 말 것" |
| 임의 라이브러리 추가 | executor 호출 시 의존성 목록 사전 명시. 새 의존성은 너 승인 후 |
| 사라진 컨텍스트 (대화 압축) | 결정 사항은 즉시 PRD/ARCH/ROADMAP에 박힘. 대화에만 두지 말 것 |

---

## 8. 첫 2.5~3.5주 시퀀스 (Solo + AI 가정)

```
Day 1~2 — Slice 0 부트스트랩 (2~2.5일)
  너: planner agent로 lane 분해 + 산출물 확인
  너: 사이드카 통신·배포 결정 2건 PRD §10에 박기
  Claude: executor 3개 dispatch (모노레포 / Compose / Tauri+Vite 빈 골격)
  너: docker compose up + tauri dev + vite dev 모두 확인
  → tag s0

Day 3~4 — Slice 1 (2일)
  너: events.py 1개(utterance.transcribed) + 5 테이블 DDL 락 + `?since=<seq>` 시그니처
  Claude: executor 3개 (Lane A: WS Hub+Auth+마이그 / Lane B: 가짜 발생기 / Lane D: viewer 1줄)
  Claude: code-reviewer + verifier 자동
  너: 가짜 자막이 viewer에 1초 안에 뜨는지 확인 → tag s1

Day 5~6 — Slice 2 (1~2일)
  Claude: document-specialist로 sounddevice 확인
  Claude: executor 1개 (Lane B 단독)
  너: Windows에서 Voicemeeter로 영어 영상 재생 → 서버 청크 카운트 확인 → tag s2

Day 7~10 — Slice 3 (2.5~3.5일)
  Claude: document-specialist로 google-genai 확인
  Claude: executor 1개 (Lane A AI 모듈) — `STTProvider`/`TranslationProvider` 인터페이스 + `GeminiLiveProvider` 구현체
  Claude: security-reviewer로 Gemini key 처리 검토
  너: latency budget 4구간 분해 + 영어 영상 → 한국어 자막 viewer 확인 (P50 ≤ 2초) → tag s3

Day 11~14 — Slice 4 (2.5~3.5일)
  Claude: executor 3개 (Lane A: lifecycle API + MD / Lane C: operator UI + 5슬롯·토큰·placeholder / Lane D: 종료 화면)
  너: 30분 모의 회의 완주 + 리포트 확인 → tag s4

Day 15~17 — Slice 5 (2~3일)
  Claude: executor 2개 (Lane A: 부서/visibility / Lane B: 오프라인 큐 영구)
  Claude: verifier 부하 테스트 (30 viewer 시뮬)
  너: 실제 끊김 시나리오 검증 → tag s5 == mvp-alpha
```

**총합: 약 2.5~3.5주.** 너의 실제 작업 시간(검토+시뮬레이션) ≈ 15~25시간.

---

## 9. 슬라이스별 너만 할 수 있는 일 체크리스트

| Slice | 너가 직접 챙길 것 |
|---|---|
| S0 | Gemini API Key 발급 / 사내 git 저장소 생성 / 서버 IP 결정 |
| S1 | events.py + DDL 락 결정 / 시드 어드민 비밀번호 |
| S2 | Voicemeeter(Windows) 본인 PC 셋업 / 실제 오디오로 동작 확인. BlackHole(Mac)은 2순위 검증 |
| S3 | Gemini Live 모델/프롬프트 검토 / 비용 모니터 / 실제 영어 영상으로 자막 품질 검증 |
| S4 | MD 리포트 포맷 검수 / 30분 모의 회의 직접 진행 |
| S5 | 회의실 PC 2대 + viewer 30 conn 부하 시뮬(Locust 또는 `websockets`) + 실 viewer 5~8대 검증 |
| 시스템 부원 협조 | SETUP_SERVER / SETUP_MEETING_PC 진행 코디네이션 |

---

## 10. 막힘 / 실패 시 절차

```
1) Claude가 1차 디버깅 (debugger 에이전트)
   ├─ 스택 트레이스 + 재현 시나리오 분석
   └─ 가설 + 수정안 제시
2) 너 검토 후 승인 → executor가 수정
3) verifier 재실행
4) 3회 이상 실패 시 → 너에게 직접 질문 (전제 의심 / 범위 축소 검토)
5) 슬라이스 범위 축소가 답이면 ROADMAP에 즉시 박음 (β로 이동)
```

→ **3회 실패 = 사람이 개입할 신호. AI가 무한 시도 안 함.**

---

## 11. 한 줄 요약

> **너는 결정·검토·시뮬레이션만 한다. 나머지는 Claude가 lane별 에이전트로 병렬 실행.**  
> 슬라이스를 작게 자르고, 시작 시 락하고, 끝낼 때 태그하는 의식을 지키면 흔들림 없음.
