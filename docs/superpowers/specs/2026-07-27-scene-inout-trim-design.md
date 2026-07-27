# 검수 팝업 In/Out 트림 경계 교정 — 설계

- 날짜: 2026-07-27
- 대상 기능: 자막메이커 씬 분할 검수 팝업(`SceneSplitView` 프리뷰)에 편집 프로그램식 In/Out 경계 교정 추가
- 상태: 설계 승인 완료(2026-07-27)

## 1. 목적

지금 경계 교정은 **숫자를 읽어 옮겨 적는 작업**이다.

1. 팝업 플레이어에서 프레임 스텝(◀/▶)으로 혼입 프레임을 찾는다
2. 프레임 카운터(`프레임 31 / 40`)를 눈으로 읽는다
3. `경계 교정`의 `프레임씩` 입력칸에 그 수를 계산해서 입력한다
4. 방향 버튼 4개 중 맞는 것을 고른다

3번이 사람의 계산·타이핑이라 오입력 위험이 있고 손이 많이 간다. 편집 프로그램의 In/Out 트림처럼 **찾은 프레임을 그 자리에서 찍으면** 앞(또는 뒤) 프레임들이 이웃 씬으로 넘어가게 한다.

## 2. 핵심 결정 (확정)

1. **스크러버는 시킹 전용 유지.** 트랙 클릭에 파괴적 편집을 겹치지 않는다. In/Out은 전용 버튼 2개(+`I`/`O` 키).
2. **머리/꼬리 자동 판단 없음.** "지점이 머리에 가까우면 이전 씬" 같은 근접 규칙은 40프레임 씬에서 머리쪽 30프레임을 넘기려 할 때 반대편으로 오판한다. 사용자가 방향을 명시적으로 고른다.
3. **기존 방식 유지.** `프레임씩 N` + 4버튼은 그대로 둔다. 특히 "이웃 씬 → 머리/꼬리"(가져오기) 두 방향은 In/Out으로 표현할 수 없다 — In/Out은 이 씬 **안의** 프레임만 가리키므로 "주기"만 가능하다.
4. **경계 수식 재사용.** 새 시간 계산을 만들지 않는다. `k → 프레임 델타` 변환만 추가하고 경계 이동은 검증된 `shiftBoundaryMs`/`nudgeBoundary`가 담당한다(익스포트 프레임 정확성 보존).
5. **되돌리기 신설.** 한 클릭으로 쉬워지는 만큼 경계 교정도 되돌릴 수 있어야 한다. 기존 병합 되돌리기 스택과 **하나의 LIFO로 통합**한다.

## 3. UI

팝업 하단, 기존 `경계 교정` 줄 **위**에 한 줄 추가:

```
◀이전  ▶재생  다음▶  [=====●--------]  프레임 31 / 40

현재 프레임 기준  [◀ 여기부터(I) · 앞 30f → 이전 씬]  [여기까지(O) · 뒤 9f → 다음 씬 ▶]  ↩되돌리기
경계 교정  [ 1]프레임씩:  [머리 1f → 이전 씬] [이전 씬 → 머리 1f] | [꼬리 1f → 다음 씬] [다음 씬 → 꼬리 1f]
```

- 버튼 라벨의 `30f`/`9f`는 재생 위치에 따라 실시간 갱신된다. 사용자가 읽어 옮겨 적던 값이 곧 라벨이라 **오입력이 구조적으로 불가능**하다.
- 영상·스크러버 클릭 동작은 변경 없음(재생/일시정지, 시킹).

## 4. 매핑

현재 프레임 `k` / 총 `n`은 이미 `segFrameNumber(previewMs, startMs, endMs, fps)`로 계산돼 카운터에 표시된다. 이 값으로:

| 동작 | 의미 | 호출 |
|---|---|---|
| 여기부터(In) | 이 프레임이 이 씬의 **첫** 프레임 | `nudgeBoundary("head", k-1)` |
| 여기까지(Out) | 이 프레임이 이 씬의 **마지막** 프레임 | `nudgeBoundary("tail", -(n-k))` |

변환은 순수함수로 분리해 테스트한다:

```ts
// sceneSplitLogic.ts
export function trimFrames(k: number, n: number): { inFrames: number; outFrames: number }
// → { inFrames: k-1, outFrames: n-k }
```

**빈 씬 불가(구조적)**: In은 `n-(k-1) = n-k+1 ≥ 1` 프레임을, Out은 `k ≥ 1` 프레임을 이 씬에 남긴다. `nudgeBoundary`의 클램프에 의존하지 않는다.

**비활성 조건**

| 버튼 | 비활성 |
|---|---|
| 여기부터(In) | `segIndex === 0` (이전 씬 없음) 또는 `k === 1` (0f 무동작) |
| 여기까지(Out) | `segIndex === segments.length-1` (다음 씬 없음) 또는 `k === n` (0f 무동작) |

**적용 직후** — 기존 `nudgeBoundary`가 새 경계 프레임으로 시킹하므로, In을 찍은 프레임이 그 자리에서 `프레임 1 / 10`(40프레임 씬에서 k=31)로 다시 매겨진다. 결과가 카운터에 즉시 읽혀 별도 확인 UI가 필요 없다.

## 5. 되돌리기 통합

현재 `mergeUndo`는 병합 전용이고, 항목마다 `survivor`(되돌리기 버튼을 렌더할 리스트 줄)를 들고 있다. 여기에 종류를 붙인다:

```ts
{ kind: "merge" | "boundary"; segs; issues; survivor }
```

- 경계 교정(`nudgeBoundary`, In/Out 포함)은 편집 전 `segments`·`boundary_issues`를 `kind:"boundary"`, `survivor = segIndex`로 push한다.
- 되돌리기는 언제나 **스택 top 하나를 pop**한다(엄격 LIFO). 병합·경계 교정을 섞어도 순서가 뒤엉키지 않는다.
- 렌더 위치: 리스트 줄의 `↩되돌리기`는 top이 `merge`일 때만(= `undoIndex` prop에 `merge`일 때만 인덱스를 넘김), 팝업의 `↩되돌리기`는 top이 `boundary`이고 `survivor === preview.segIndex`일 때만.
- 기존 초기화 조건(모드 전환·저장·일괄교정 시 스택 비움)은 그대로 적용된다.

`SceneFilmstrip`은 변경하지 않는다 — 부모가 `undoIndex`에 `null`을 넘기면 리스트 버튼은 뜨지 않는다.

## 6. 키보드

팝업이 열려 있을 때 `I` = 여기부터, `O` = 여기까지. `window` keydown 리스너를 팝업 마운트 동안만 걸고, `event.target`이 `INPUT`/`TEXTAREA`면 무시한다(프레임 수 입력칸·라벨 편집칸에서 타이핑이 편집을 트리거하면 안 된다). 비활성 조건이면 아무 동작 안 한다.

## 7. 변경 파일

| 파일 | 변경 |
|---|---|
| `apps/desktop/src/console/sceneSplitLogic.ts` | `trimFrames` 추가 |
| `apps/desktop/src/console/sceneSplitLogic.test.ts` | `trimFrames` 케이스 |
| `apps/desktop/src/console/SceneSplitView.tsx` | In/Out 줄, `trimAt`, `I`/`O` 키, `nudgeBoundary` undo push, undo 스택 `kind`, 팝업 되돌리기 버튼 |
| `apps/desktop/src/help/helpManualContent.ts` | 도움말 8번(씬 분할)에 In/Out 트림 사용법 한 문단 |

`SceneSplitView.tsx`는 1359줄로 이미 크다(ESLint `max-lines` 미설정이라 차단은 아님). 이 패치로 ~130줄 늘어난다. 팝업 플레이어 컴포넌트 분리는 경계 수식 회귀 위험이 있어 **이 작업과 섞지 않고 별도 작업으로 남긴다.**

## 8. 검증

- 단위: `trimFrames` — `k=1`(in 0), `k=n`(out 0), `k=31/n=40` → in 30 / out 9, 범위 밖 k 클램프.
- 되돌리기 LIFO는 **단위 테스트하지 않는다** — `apps/desktop`에는 jsdom/RTL이 없고(순수 로직 vitest만) 이 기능 하나를 위해 컴포넌트 테스트 환경을 들이지 않는다. 타입체크 + 실기 검증으로 확인한다.
- 타입/빌드: `npx tsc --noEmit`, `npx vitest run` (apps/desktop).
- 실기: 40프레임 씬에서 프레임 31로 이동 → `여기부터` → 카운터가 `1 / 10`, 이전 씬이 30프레임 늘어남 → `↩되돌리기`로 복원 → `수정사항 저장` 후 익스포트 클립 프레임 수 확인.
