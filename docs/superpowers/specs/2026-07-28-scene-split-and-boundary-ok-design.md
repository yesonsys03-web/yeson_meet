# 씬 분할 + 경계오류 "문제없음" — 설계

- 날짜: 2026-07-28
- 대상 기능: 자막메이커 씬 검수에서 ①한 씬을 두 씬으로 나누기 ②확인을 마친 씬을 경계오류 목록에서 빼기
- 상태: 설계 승인 완료(2026-07-28)
- 관련: [In/Out 트림 경계 교정](2026-07-27-scene-inout-trim-design.md) · [개별 씬 익스포트](2026-07-27-scene-partial-export-design.md) · [슬레이트 씬 분할](2026-07-16-slate-scene-split-design.md)

## 1. 목적

실기에서 나온 두 가지 결손이다.

**① 한 씬 안에 두 씬이 들어 있다.** `HH0305_140_0290` 한 줄에 `0280`과 `0290` 두 씬이 붙어 있다(스캔이 그 컷을 못 잡았다). 지금 목록에서 할 수 있는 편집은 **병합·이름수정·In/Out 트림**뿐이라 **나누는 수단이 없다**. 유일한 우회는 재스캔인데 25분짜리 번들이면 25분이고 수동 정렬이 초기화된다.

**② 경계오류가 거짓 양성을 낸다.** 디졸브처럼 두 슬레이트가 겹쳐 보이는 구간은 검사가 혼입으로 플래그하지만 실제로는 경계가 맞다. 사용자가 눈으로 확인해도 **목록에서 뺄 방법이 없어** 검수를 마쳤는지 알 수 없고, 다시 검사하면 같은 줄이 또 올라온다.

## 2. 핵심 결정 (확정)

1. **분할 지점은 팝업의 현재 프레임.** 사용자는 이미 팝업에서 ←/→로 프레임을 훑으며 혼입을 찾는다. 그 자리에 `✂ 여기서 나누기`(단축키 `S`)를 둔다. 지금 보는 프레임이 **뒤 구간의 첫 프레임**이 된다 — In 트림("여기부터")과 같은 약속이라 새로 배울 게 없다.

2. **자를 시각은 In 트림과 같은 계산을 쓴다.** `shiftBoundaryMs(cur.start_ms, fps, k - 1)`. 즉 프레임 k에서 나눈 결과는 그 프레임에 In 트림을 건 것과 경계가 **정확히 일치**한다. 익스포트 `-ss` snap-up 규칙과 어긋나지 않는다(그 함수가 이미 그 규칙으로 쓰였다).

3. **뒤 구간이 원래 이름을 유지하고, 앞 구간에 새 이름이 붙는다.** 실기 사례가 그 모양이다 — 저장된 라벨(`0290`)은 뒤쪽 슬레이트다.

4. **앞 구간 이름은 슬레이트를 읽어 제안한다.** 기존 미리읽기 라우트(`POST /scenes/ocr-test`)를 **그대로** 쓴다(서버 변경 없음). 앞 구간 **한가운데** 프레임을 읽는다 — 머리·꼬리는 디졸브에 걸릴 확률이 높다. 응답 `tokens`에 현재 모드의 토큰 인덱스로 `previewLabel`을 적용해 라벨을 만든다. 실패하면 라벨을 그대로 두고 "직접 입력하세요"로 안내한다(이름칸은 이미 있다).

5. **첫 프레임에서는 분할 금지.** k=1이면 앞 구간이 0프레임이 된다. 버튼을 잠근다 — 빈 구간이 생기면 익스포트가 0바이트 클립을 만든다.

6. **"문제없음"은 저장하되, 경계가 바뀌면 자동으로 풀린다.** 확인 당시의 `start_ms`/`end_ms`를 함께 저장하고, 현재 세그먼트의 값과 다르면 확인표시를 무시한다. 400씬 검수는 여러 세션에 걸치므로 세션 한정으로는 쓸모가 없고, 그렇다고 영구히 숨기면 **나중에 바뀐 경계를 못 보고 지나친다**.

7. **경계오류 목록은 지금도 라벨로 대상을 찾는다**(`SceneSplitView.tsx:858-864` — 저장된 인덱스가 아니라 현재 세그먼트 라벨로 재조회). 분할로 배열이 길어져도 어긋나지 않는다. 이 계산에 "문제없음" 필터만 얹는다.

## 3. 씬 분할

### 순수 함수 (`sceneSplitLogic.ts`)

```ts
export function splitSegment(
  segs: SceneSegment[], i: number, k: number, fps: number,
): SceneSegment[]
```

- `cutMs = shiftBoundaryMs(cur.start_ms, fps, k - 1)`
- 앞 구간 = `{...cur, end_ms: cutMs}`, 뒤 구간 = `{...cur, start_ms: cutMs}` — **둘 다 원래 라벨을 유지한다.**
- 무효면 `segs`를 그대로 돌려준다(변화 없음): `i` 범위 밖 · `k <= 1` · `cutMs <= start_ms` · `cutMs >= end_ms`.

라벨 변경은 이 함수가 하지 않는다. OCR 제안은 비동기라 분할과 시점이 다르고, 이름 변경은 이미 `renameSegment`가 한다 — 한 함수에 두 책임을 넣지 않는다.

### 화면 흐름 (`SceneSplitView.tsx`)

1. 팝업에서 `✂ 여기서 나누기`(또는 `S`) → 현재 프레임 번호 `k`는 팝업이 이미 표시 중인 값(`segFrameNumber`).
2. `editUndo`에 `{ kind: "split", segs, issues, survivor: i }`를 쌓는다(기존 병합·경계교정과 같은 스택, 엄격 LIFO).
3. `setSegments(splitSegment(...))` → dirty 표시(저장 전 익스포트는 기존 가드가 막는다).
4. `clearBoundaryFlags([원래 라벨])` — 혼입을 방금 해결했으므로 그 씬의 경계오류 표시를 뺀다(병합과 동일한 처리).
5. 선택을 앞 구간(`i`)으로 옮기고 팝업은 앞 구간 기준으로 다시 만든다 — 화면과 데이터가 어긋나면 사용자가 방금 한 편집을 또 한다(`undoEdit`이 같은 이유로 팝업을 다시 만든다).
6. 앞 구간 한가운데 시각(`midMs = frameSeekMs((start_ms + cutMs) / 2, fps)` — 프레임 중앙으로 맞춰야 추출이 경계 프레임을 집지 않는다)으로 **기존** `testOcrRegion(jobId, midMs, ocrRegion)` 호출 → `previewLabel(tokens, uptoIndex)` → 값이 있고 현재 라벨과 다르면 `renameSeg(i, 제안)` + 알림 `"앞 구간 이름을 HH0305_140_0280으로 읽었습니다 — 다르면 이름칸에서 고치세요."`
   - 저장된 구역(`ocr_region`)을 그대로 넘긴다 — 스캔이 읽은 것과 같은 상자를 읽어야 같은 라벨이 나온다.
   - 못 읽으면: `"앞 구간 슬레이트를 읽지 못했습니다 — 이름을 직접 입력하세요."` 라벨은 그대로 둔다.
   - OCR 실패가 분할을 되돌리지는 않는다. 경계는 이미 맞았고 이름만 남은 문제다.
7. 씬 모드·시퀀스 모드 둘 다 동작한다. 토큰 인덱스만 모드에 따라 다르다(`scene_upto` / `seq_upto`).

### 알아둘 것

- 분할 직후 잠깐 같은 라벨이 인접한다(OCR 응답 전, 또는 OCR 실패 시). 이때 목록의 **"인접 중복 병합" 버튼 카운트가 1 올라간다** — 누르면 방금 나눈 것이 도로 합쳐진다. 파괴적이지 않고(되돌리기 가능) 이름을 붙이면 사라지므로 별도 조치는 하지 않는다.
- `mergeAdjacentSameLabel`은 저장 경로가 아니라 **사용자가 누르는 버튼**에서만 돈다(`SceneSplitView.tsx:1023,1035,1392`). 저장이 분할을 조용히 되돌리는 일은 없다.

## 4. 경계오류 "문제없음"

### 저장 형태

`scenes.json`에 `boundary_ok: Array<{ label: string; start_ms: number; end_ms: number }>`.

### 서버 API

`POST /api/v1/video-jobs/{external_id}/scenes/boundary-ok`

```jsonc
{ "items": [{ "label": "HH0305_140_0290", "start_ms": 1359000, "end_ms": 1366000 }] }
// 응답: { "count": 1 }
```

- **목록 전체를 교체한다.** 추가·삭제를 따로 두지 않는다 — 클라가 목록의 주인이고, 전체 교체는 멱등이라 부분 상태가 어긋날 여지가 없다("모두 해제"는 빈 배열).
- `GET /scenes`는 화이트리스트로 응답을 만든다(`video_jobs.py:678-710`) — `boundary_ok`를 **명시적으로 추가해야** 클라가 받는다.
- 옛 데이터에는 키가 없다 → 기본값 `[]`.

### 순수 함수 (`sceneSplitLogic.ts`)

```ts
export type BoundaryOk = { label: string; start_ms: number; end_ms: number };

export function boundaryIssueIndices(
  issues: Array<{ label: string }>, segs: SceneSegment[], ok: BoundaryOk[],
): number[]
```

- 라벨 → 현재 인덱스로 재조회(기존 `curLabelToIdx` 로직을 이 함수로 옮긴다).
- 그 라벨의 `ok` 항목이 있고 **`start_ms`·`end_ms`가 지금과 같으면** 제외한다. 하나라도 다르면 확인표시를 무시하고 목록에 남긴다.
- 현재 세그먼트에 없는 라벨(병합·이름수정으로 사라짐)은 지금처럼 자동 제외.

### 화면

- **경계오류 탭에서만** 각 줄에 `✓ 문제없음`을 보인다. 다른 탭에는 안 보인다 — 줄에는 이미 병합 2개·익스포트가 있어 더 늘리면 좁다.
- 누르면 그 줄이 목록에서 사라진다(그 자리에서 서버에 저장).
- 탭 머리에 확인함이 1건 이상이면 `확인함 N건 · 모두 해제`.
- 저장 실패는 알림으로 띄우고 화면 상태를 되돌린다 — "뺐다고 봤는데 다음에 다시 뜨는" 상황을 만들지 않는다.

## 5. 변경 파일

| 파일 | 변경 |
|---|---|
| `apps/desktop/src/console/sceneSplitLogic.ts(.test.ts)` | `splitSegment`, `boundaryIssueIndices`, `BoundaryOk` + 테스트 |
| `apps/desktop/src/console/videoApi.ts` | `ScenesData.boundary_ok`, `saveBoundaryOk()` |
| `apps/desktop/src/console/SceneSplitView.tsx` | 분할 버튼·단축키·OCR 제안, `boundaryIdx`를 새 함수로 교체, `✓ 문제없음`·`모두 해제` |
| `apps/desktop/src/console/SceneFilmstrip.tsx` | 경계오류 탭 전용 `✓ 문제없음` 버튼 |
| `apps/server/api/v1/video_jobs.py` | `BoundaryOkIn` + `POST .../scenes/boundary-ok`, `get_scenes`에 `boundary_ok` |
| `apps/server/tests/test_api_video_jobs.py` | 저장·교체·응답 노출 |
| `apps/desktop/src/help/helpManualContent.ts` | 씬 분할·문제없음 한 문장씩 |

**굽기·스캔·경계계산 파이프라인은 변경하지 않는다.** 분할은 세그먼트 편집이고, 익스포트는 저장된 세그먼트를 그대로 자른다.

## 6. 검증

**단위(클라)**
- `splitSegment`: 프레임 k에서 나눈 경계가 **In 트림과 같은 값**인지(`shiftBoundaryMs` 대조) · 두 구간의 라벨이 원래 값 유지 · 앞+뒤 시간이 원본과 연속(빈틈·겹침 없음) · `k=1`·범위 밖·`k>n`은 변화 없음.
- `boundaryIssueIndices`: 확인표시가 있으면 제외 · **경계가 달라지면 다시 나타남** · 라벨이 사라지면 제외 · 확인표시가 없으면 기존과 동일.

**단위(서버)**: 저장 후 `GET /scenes`가 `boundary_ok`를 돌려주는지 · 전체 교체가 옛 목록을 대체하는지 · 빈 배열로 모두 해제되는지 · 스캔 전 잡에서도 409가 아니라 정상 저장인지(검수는 스캔 뒤지만 라우트가 스캔 상태에 의존할 이유가 없다).

> 테스트 DB: `TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db"`를 앞에 붙이면 Docker 없이 돈다(`conftest.py:6-13`).

**실기**: 실제 번들에서 `HH0305_140_0290`을 나눠 ①앞 구간 이름이 `..._0280`으로 채워지는지 ②익스포트한 두 mp4의 프레임 수 합이 원본 클립과 같은지 ③경계오류 줄에서 `✓ 문제없음`을 누르고 앱을 다시 켰을 때 안 뜨는지 ④그 씬 경계를 트림한 뒤에는 다시 뜨는지.
