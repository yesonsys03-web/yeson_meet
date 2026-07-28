# 씬 분할 + 경계오류 "문제없음" 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 씬 검수에서 ①한 씬 안에 붙어 있는 두 씬을 팝업 현재 프레임에서 나누고(앞 구간 이름은 슬레이트를 읽어 제안) ②눈으로 확인해 문제없는 씬을 경계오류 목록에서 뺀다(저장되지만 그 씬 경계가 바뀌면 자동 해제).

**Architecture:** 두 기능 모두 **세그먼트 편집** 계층에만 얹는다. 분할은 In 트림과 같은 `shiftBoundaryMs` 계산으로 세그먼트 하나를 둘로 쪼개고, 이름 제안은 기존 미리읽기 라우트(`POST /scenes/ocr-test`)를 그대로 쓴다. "문제없음"은 `scenes.json`의 새 키 `boundary_ok`에 저장하고, 경계오류 탭의 인덱스 계산(이미 라벨 기준)에 필터로 얹는다.

**Tech Stack:** React + TypeScript(vitest) · FastAPI + Pydantic(pytest)

## Global Constraints

- **자를 시각은 In 트림과 같은 함수를 쓴다** — `shiftBoundaryMs(cur.start_ms, fps, k - 1)`. 다른 수식을 쓰면 익스포트 `-ss` snap-up 규칙과 어긋나 프레임이 하나 밀린다.
- **뒤 구간이 원래 라벨을 유지하고, 앞 구간에 새 이름이 붙는다.**
- **k=1(첫 프레임)에서는 분할하지 않는다** — 앞 구간이 0프레임이면 익스포트가 0바이트 클립을 만든다.
- **`boundary_ok` 저장은 목록 전체 교체.** 빈 배열이 "모두 해제". 추가·삭제를 나누지 않는다.
- **확인표시는 경계가 그대로일 때만 유효하다** — `start_ms`·`end_ms`가 확인 당시와 다르면 무시하고 목록에 다시 띄운다.
- **굽기·스캔·경계계산 파이프라인은 변경하지 않는다.**
- 기존 파일만 수정한다(새 소스 파일 없음). 프로젝트 규칙: 가능한 가장 작은 패치.

## 파일 구조

| 파일 | 책임 |
|---|---|
| `apps/desktop/src/console/sceneSplitLogic.ts` | 순수 계산 — `splitSegment`(경계 산술), `boundaryIssueIndices`(라벨 재조회 + 확인표시 필터), `scenePopupAction`에 `S` 추가 |
| `apps/server/api/v1/video_jobs.py` | `boundary_ok` 저장 라우트 + `GET /scenes` 응답 노출 |
| `apps/desktop/src/console/videoApi.ts` | `BoundaryOk` 타입, `ScenesData.boundary_ok`, `saveBoundaryOk()` |
| `apps/desktop/src/console/SceneSplitView.tsx` | 상태·콜백 배선 — `splitAt`, `markBoundaryOk`, 되돌리기 종류 `split` |
| `apps/desktop/src/console/SceneFilmstrip.tsx` | 줄 버튼 `✓ 문제없음`(경계오류 탭에서만 렌더) |
| `apps/desktop/src/help/helpManualContent.ts` | 사용자 도움말 |

---

### Task 1: 순수 계산 — 분할과 경계오류 필터

**Files:**
- Modify: `apps/desktop/src/console/videoApi.ts` (`SceneSegment` 정의 옆 = `:326` 부근)
- Modify: `apps/desktop/src/console/sceneSplitLogic.ts` (`mergeSegment` 아래 `:250`, `filterIndices` 아래 `:451`)
- Test: `apps/desktop/src/console/sceneSplitLogic.test.ts`

**Interfaces:**
- Consumes: 기존 `shiftBoundaryMs(boundaryMs, fps, deltaFrames)`, `type SceneSegment = { label: string; start_ms: number; end_ms: number }`
- Produces:
  - `type BoundaryOk = { label: string; start_ms: number; end_ms: number }` (videoApi에서 export)
  - `splitSegment(segs: SceneSegment[], i: number, k: number, fps: number): SceneSegment[]`
  - `boundaryIssueIndices(issues: Array<{ label: string }>, segs: SceneSegment[], ok: BoundaryOk[]): number[]`

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`apps/desktop/src/console/sceneSplitLogic.test.ts` 끝에 추가:

```ts
describe("splitSegment", () => {
  const fps = 24;
  const segs = [
    { label: "A", start_ms: 0, end_ms: 10000 },
    { label: "B", start_ms: 10000, end_ms: 20000 },
  ];

  it("cuts where the In trim would put the boundary", () => {
    // 분할과 트림이 다른 수식을 쓰면 익스포트 -ss snap-up과 어긋나 프레임이 밀린다.
    const out = splitSegment(segs, 1, 5, fps);
    const cut = shiftBoundaryMs(10000, fps, 4);
    expect(out).toHaveLength(3);
    expect(out[1]).toEqual({ label: "B", start_ms: 10000, end_ms: cut });
    expect(out[2]).toEqual({ label: "B", start_ms: cut, end_ms: 20000 });
  });

  it("keeps both parts under the original label — naming is a separate step", () => {
    const out = splitSegment(segs, 1, 5, fps);
    expect(out[1]!.label).toBe("B");
    expect(out[2]!.label).toBe("B");
  });

  it("leaves the timeline continuous — no gap, no overlap, same total span", () => {
    const out = splitSegment(segs, 1, 5, fps);
    expect(out[0]!.end_ms).toBe(out[1]!.start_ms);
    expect(out[1]!.end_ms).toBe(out[2]!.start_ms);
    expect(out[0]!.start_ms).toBe(0);
    expect(out.at(-1)!.end_ms).toBe(20000);
  });

  it("refuses the first frame — a 0-frame part exports a 0-byte clip", () => {
    expect(splitSegment(segs, 1, 1, fps)).toBe(segs);
  });

  it("refuses an out-of-range index or a frame past the end", () => {
    expect(splitSegment(segs, 5, 3, fps)).toBe(segs);
    expect(splitSegment(segs, 1, 100000, fps)).toBe(segs);
  });
});

describe("boundaryIssueIndices", () => {
  const segs = [
    { label: "A", start_ms: 0, end_ms: 1000 },
    { label: "B", start_ms: 1000, end_ms: 2000 },
  ];

  it("resolves each issue by current label, not a stored index", () => {
    // 병합·분할로 목록 길이가 바뀌어도 엉뚱한 줄을 가리키면 안 된다.
    expect(boundaryIssueIndices([{ label: "B" }], segs, [])).toEqual([1]);
    expect(boundaryIssueIndices([{ label: "gone" }], segs, [])).toEqual([]);
  });

  it("hides a scene the user confirmed is fine", () => {
    const ok = [{ label: "B", start_ms: 1000, end_ms: 2000 }];
    expect(boundaryIssueIndices([{ label: "B" }], segs, ok)).toEqual([]);
  });

  it("brings it back once that boundary moves — the new cut was never reviewed", () => {
    const ok = [{ label: "B", start_ms: 1000, end_ms: 2000 }];
    const moved = [segs[0]!, { label: "B", start_ms: 1200, end_ms: 2000 }];
    expect(boundaryIssueIndices([{ label: "B" }], moved, ok)).toEqual([1]);
  });
});
```

같은 파일 첫 줄의 import 목록에 `, splitSegment, boundaryIssueIndices`를 추가한다(`shiftBoundaryMs`는 이미 들어 있다).

- [ ] **Step 2: 실패를 확인한다**

Run: `pnpm -C apps/desktop test -- sceneSplitLogic`
Expected: FAIL — `splitSegment is not a function` (또는 import 해석 실패)

- [ ] **Step 3: `BoundaryOk` 타입을 videoApi에 추가한다**

`apps/desktop/src/console/videoApi.ts`의 `export type SceneSegment = ...`(`:326`) 바로 아래에 추가:

```ts
// 사용자가 눈으로 확인해 "문제없음"으로 표시한 경계오류 구간. 확인 당시의 경계를
// 함께 들고 다닌다 — 나중에 그 씬 경계가 바뀌면 확인표시를 무시해야 하기 때문이다
// (바뀐 경계를 안 본 채로 숨기지 않는다).
export type BoundaryOk = { label: string; start_ms: number; end_ms: number };
```

- [ ] **Step 4: 두 순수 함수를 구현한다**

`apps/desktop/src/console/sceneSplitLogic.ts` `:230`의 타입 import를 확장:

```ts
import type { BoundaryOk, SceneSegment } from "./videoApi";
```

`mergeSegment` 함수(`:236-250`) 바로 아래에 추가:

```ts
// 한 씬 안에 두 씬이 붙어 있을 때(스캔이 그 컷을 못 잡은 경우) 나눈다. k는 팝업
// 카운터의 '프레임 k / n' — 지금 보는 프레임이 **뒤 구간의 첫 프레임**이 된다
// (In 트림 "여기부터"와 같은 약속이라 새로 배울 게 없다). 자를 시각도 In 트림과
// 같은 shiftBoundaryMs를 쓰므로 나눈 경계는 그 프레임에 In 트림을 건 것과 정확히
// 같다 — 다른 수식을 쓰면 익스포트 -ss snap-up과 어긋나 프레임이 하나 밀린다.
//
// 두 구간 모두 원래 라벨을 유지한다. 앞 구간 이름은 슬레이트를 읽어 붙이는 별도
// 단계(renameSegment)의 몫이다 — OCR은 비동기라 시점이 다르고, 한 함수에 경계
// 산술과 이름 짓기를 같이 넣지 않는다.
//
// k<=1이면 앞 구간이 0프레임이라 아무것도 하지 않는다(빈 구간은 익스포트가 0바이트
// 클립을 만든다).
export function splitSegment(
  segs: SceneSegment[], i: number, k: number, fps: number,
): SceneSegment[] {
  const cur = segs[i];
  if (!cur || k <= 1) return segs;
  const cutMs = shiftBoundaryMs(cur.start_ms, fps, Math.floor(k) - 1);
  if (cutMs <= cur.start_ms || cutMs >= cur.end_ms) return segs;
  const out = segs.slice();
  out.splice(i, 1, { ...cur, end_ms: cutMs }, { ...cur, start_ms: cutMs });
  return out;
}
```

`filterIndices` 함수(`:444-451`) 바로 아래에 추가:

```ts
// 경계오류 탭에 보일 구간 인덱스.
//
// 저장된 인덱스가 아니라 '현재 세그먼트의 라벨'로 다시 찾는다 — 병합·분할·이름수정
// 으로 목록이 바뀌어도 어긋나지 않고, 라벨이 사라진 구간은 자동으로 빠진다.
//
// 사용자가 '문제없음'으로 확인한 구간은 뺀다. 검사는 디졸브처럼 두 슬레이트가 겹쳐
// 보이는 구간을 혼입으로 잡는데 실제로는 경계가 맞는 경우가 있고, 400씬 검수는 여러
// 세션에 걸치므로 확인 결과가 남아야 한다. 단 확인 당시의 시작·끝과 지금이 다르면
// 확인표시를 무시한다 — 그 뒤에 경계를 고쳤다는 뜻이고, 바뀐 경계를 안 본 채로
// 숨기면 안 된다.
export function boundaryIssueIndices(
  issues: Array<{ label: string }>, segs: SceneSegment[], ok: BoundaryOk[],
): number[] {
  const idxOf = new Map(segs.map((s, i) => [s.label, i] as const));
  const okOf = new Map(ok.map((o) => [o.label, o] as const));
  const out: number[] = [];
  for (const issue of issues) {
    const i = idxOf.get(issue.label);
    if (i == null) continue;
    const seg = segs[i] as SceneSegment;
    const cleared = okOf.get(issue.label);
    if (cleared && cleared.start_ms === seg.start_ms
        && cleared.end_ms === seg.end_ms) continue;
    out.push(i);
  }
  return out;
}
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `pnpm -C apps/desktop test -- sceneSplitLogic`
Expected: PASS (기존 테스트 포함 전량)

- [ ] **Step 6: 타입 검사**

Run: `pnpm -C apps/desktop build:vite`
Expected: `tsc --noEmit` 통과 후 vite 빌드 성공

- [ ] **Step 7: 커밋**

```bash
git add apps/desktop/src/console/sceneSplitLogic.ts \
        apps/desktop/src/console/sceneSplitLogic.test.ts \
        apps/desktop/src/console/videoApi.ts
git commit -m "feat(desktop): 씬 분할·경계오류 확인표시 순수 계산"
```

---

### Task 2: 서버 — 확인 목록 저장

**Files:**
- Modify: `apps/server/api/v1/video_jobs.py` (모델은 `SceneExportProbeIn` 아래, 라우트는 `scene_boundary_status` 뒤, 응답은 `get_scenes` `:670-710`)
- Test: `apps/server/tests/test_api_video_jobs.py` (파일 끝)

**Interfaces:**
- Consumes: 기존 `load_scenes`/`save_scenes`, `_get_job_or_404`
- Produces:
  - `POST /api/v1/video-jobs/{external_id}/scenes/boundary-ok`, 본문 `{"items": [{"label": str, "start_ms": int, "end_ms": int}]}` → `{"count": int}`
  - `GET /api/v1/video-jobs/{external_id}/scenes` 응답에 `boundary_ok: list` (없으면 `[]`)

- [ ] **Step 1: 실패하는 테스트를 쓴다**

`apps/server/tests/test_api_video_jobs.py` 끝에 추가:

```python
async def test_boundary_ok_saved_and_returned(client, db_session, admin_user):
    """사용자가 '문제없음'으로 확인한 목록이 저장되고 다시 읽힌다.

    검사는 디졸브처럼 두 슬레이트가 겹쳐 보이는 구간을 혼입으로 잡는데 실제로는
    경계가 맞는 경우가 있다. 400씬 검수는 여러 세션에 걸치므로 확인 결과가 남지
    않으면 같은 줄을 매번 다시 본다.
    """
    job = await _new_scene_job(db_session, admin_user, status="done")
    pl.save_scenes(job.external_id, {"frames": [], "segments_scene": [],
                                     "segments_sequence": []})
    items = [{"label": "HH0305_140_0290", "start_ms": 1359000,
              "end_ms": 1366000}]

    r = await client.post(
        f"/api/v1/video-jobs/{job.external_id}/scenes/boundary-ok",
        json={"items": items})
    assert r.status_code == 200
    assert r.json() == {"count": 1}

    got = await client.get(f"/api/v1/video-jobs/{job.external_id}/scenes")
    assert got.json()["boundary_ok"] == items


async def test_boundary_ok_replaces_the_previous_list(client, db_session,
                                                      admin_user):
    """추가가 아니라 '전체 교체' — 클라가 목록의 주인이고 빈 배열이 모두 해제다.
    추가·삭제를 나누면 부분 상태가 어긋난다."""
    job = await _new_scene_job(db_session, admin_user, status="done")
    pl.save_scenes(job.external_id, {"frames": [], "segments_scene": [],
                                     "segments_sequence": []})
    url = f"/api/v1/video-jobs/{job.external_id}/scenes/boundary-ok"

    await client.post(url, json={"items": [
        {"label": "A", "start_ms": 0, "end_ms": 100},
        {"label": "B", "start_ms": 100, "end_ms": 200}]})
    await client.post(url, json={"items": [
        {"label": "B", "start_ms": 100, "end_ms": 200}]})
    got = await client.get(f"/api/v1/video-jobs/{job.external_id}/scenes")
    assert [o["label"] for o in got.json()["boundary_ok"]] == ["B"]

    await client.post(url, json={"items": []})
    got = await client.get(f"/api/v1/video-jobs/{job.external_id}/scenes")
    assert got.json()["boundary_ok"] == []


async def test_boundary_ok_defaults_to_empty(client, db_session, admin_user):
    """이 키가 없던 시절의 scenes.json도, 아예 스캔 전인 잡도 []를 준다 —
    클라가 응답 모양을 갈래 없이 읽을 수 있어야 한다."""
    job = await _new_scene_job(db_session, admin_user, status="done")
    pl.save_scenes(job.external_id, {"frames": [], "segments_scene": [],
                                     "segments_sequence": []})
    got = await client.get(f"/api/v1/video-jobs/{job.external_id}/scenes")
    assert got.json()["boundary_ok"] == []

    fresh = await _new_scene_job(db_session, admin_user, status="done")
    got = await client.get(f"/api/v1/video-jobs/{fresh.external_id}/scenes")
    assert got.json()["boundary_ok"] == []
```

- [ ] **Step 2: 실패를 확인한다**

Run:
```bash
TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/pytest \
  apps/server/tests/test_api_video_jobs.py -k boundary_ok -v
```
Expected: 3개 FAIL — 라우트가 없어 404/405이고, `boundary_ok` 키도 없어 KeyError

- [ ] **Step 3: 입력 모델을 추가한다**

`apps/server/api/v1/video_jobs.py`의 `SceneExportProbeIn` 클래스 바로 아래에 추가:

```python
class BoundaryOkItem(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)


class BoundaryOkIn(BaseModel):
    items: list[BoundaryOkItem]
```

- [ ] **Step 4: 라우트를 추가한다**

같은 파일, `scene_boundary_status`(경계 검사 상태 조회) 라우트 바로 뒤에 추가:

```python
@router.post("/{external_id}/scenes/boundary-ok")
async def save_boundary_ok(
    external_id: UUID,
    body: BoundaryOkIn,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """사용자가 눈으로 확인해 '문제없음'으로 표시한 경계오류 구간 목록을 저장한다.

    경계 검사는 디졸브처럼 두 슬레이트가 겹쳐 보이는 구간을 혼입으로 플래그하는데
    실제로는 경계가 맞는 경우가 있다. 400씬 검수는 여러 세션에 걸치므로 확인 결과가
    남지 않으면 같은 줄을 매번 다시 본다.

    목록 '전체'를 교체한다 — 추가·삭제를 나누면 부분 상태가 어긋난다. 빈 배열이
    '모두 해제'다.

    확인 당시의 start_ms/end_ms를 함께 저장한다. 나중에 그 씬의 경계가 바뀌면
    클라가 이 확인표시를 무시하고 목록에 다시 띄운다 — 바뀐 경계를 안 본 채로
    숨기지 않기 위해서다.
    """
    await _get_job_or_404(db, external_id)
    data = load_scenes(external_id) or {}
    data["boundary_ok"] = [item.model_dump() for item in body.items]
    save_scenes(external_id, data)
    return {"count": len(body.items)}
```

- [ ] **Step 5: 응답에 노출한다**

같은 파일 `get_scenes`(`:663-710`) 두 곳을 고친다.

스캔 전 조기 반환(`:670-673`)에 키를 더한다 — 클라가 갈래 없이 읽게:

```python
    if not data:
        return {"scanned": False, "scanning": False, "error": None,
                "ocr_done": 0, "total_frames": 0, "frames": [],
                "segments_scene": [], "segments_sequence": [], "rule": None,
                "boundary_ok": []}
```

본 응답의 `"boundary_issues"` 줄 바로 아래에 더한다:

```python
        # 사용자가 '문제없음'으로 확인한 구간(라벨 + 확인 당시 경계). 프론트가
        # 경계오류 탭에서 제외하되, 경계가 그 뒤에 바뀌었으면 무시한다.
        "boundary_ok": data.get("boundary_ok", []),
```

- [ ] **Step 6: 테스트 통과 확인**

Run:
```bash
TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/pytest \
  apps/server/tests/test_api_video_jobs.py -k boundary_ok -v
```
Expected: 3 passed

- [ ] **Step 7: 무회귀 확인**

Run:
```bash
TEST_DATABASE_URL="sqlite+aiosqlite:///$(mktemp -d)/t.db" .venv/bin/pytest \
  apps/server/tests/test_api_video_jobs.py -q
```
Expected: 실패는 **6개뿐**이고 전부 `test_create_youtube_job*` / `test_upload_job_saves_file` — 이들은 SQLite 환경 고유 실패로 이 브랜치 이전에도 동일하다. 다른 실패가 나오면 회귀다.

- [ ] **Step 8: 커밋**

```bash
git add apps/server/api/v1/video_jobs.py apps/server/tests/test_api_video_jobs.py
git commit -m "feat(server): 경계오류 '문제없음' 확인 목록 저장"
```

---

### Task 3: 경계오류 "문제없음" 배선

**Files:**
- Modify: `apps/desktop/src/console/videoApi.ts` (`ScenesData`(`:332` 부근)와 `startBoundaryCheck` 옆)
- Modify: `apps/desktop/src/console/SceneSplitView.tsx` (`:857-865`, 탭 안내문 `:1453-1459`, `<SceneFilmstrip>` props `:1460-1475`)
- Modify: `apps/desktop/src/console/SceneFilmstrip.tsx` (`Props` `:5-49`, 인자 분해 `:56-59`, 익스포트 버튼 옆 `:375-383`)

**Interfaces:**
- Consumes: Task 1의 `boundaryIssueIndices(issues, segs, ok)`·`BoundaryOk`, Task 2의 `POST .../scenes/boundary-ok`
- Produces: `saveBoundaryOk(jobId: string, items: BoundaryOk[]): Promise<{ count: number }>`, `SceneFilmstrip`의 `onBoundaryOk?: (i: number) => void`

- [ ] **Step 1: API 래퍼와 타입을 추가한다**

`apps/desktop/src/console/videoApi.ts`의 `ScenesData` 타입에서 `boundary_issues?: ...` 줄 바로 아래에 추가:

```ts
  // 사용자가 '문제없음'으로 확인한 구간. 경계가 그대로면 경계오류 탭에서 빠지고,
  // 그 씬 경계를 고치면 다시 나타난다(boundaryIssueIndices).
  boundary_ok?: BoundaryOk[];
```

`startBoundaryCheck` 함수 바로 아래에 추가:

```ts
// 확인 목록 '전체'를 교체한다(빈 배열 = 모두 해제) — 추가·삭제를 나누면 부분 상태가
// 어긋난다. 클라가 목록의 주인이다.
export async function saveBoundaryOk(
  jobId: string, items: BoundaryOk[],
): Promise<{ count: number }> {
  return request(`${apiBase()}/api/v1/video-jobs/${jobId}/scenes/boundary-ok`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items }),
  });
}
```

- [ ] **Step 2: 필름스트립에 줄 버튼을 단다**

`apps/desktop/src/console/SceneFilmstrip.tsx`의 `Props`에서 `onStepSegment?: ...` 위에 추가:

```ts
  // 경계오류 탭에서만 넘어온다 — 이 줄을 '확인했고 문제없음'으로 표시해 목록에서
  // 뺀다. 다른 탭에는 안 넘기므로 버튼도 안 보인다(줄에 이미 병합 2개·익스포트가
  // 있어 항상 띄우면 좁다).
  onBoundaryOk?: (i: number) => void;
```

인자 분해(`:56-59`)의 `onExportOne, exportingIndex, exportDisabled, onStepSegment` 를 `onExportOne, exportingIndex, exportDisabled, onBoundaryOk, onStepSegment` 로 바꾼다.

익스포트 버튼 블록(`:375-383`)의 닫는 `) : null}` 바로 아래에 추가:

```tsx
            {/* 확인했고 문제없다 — 경계오류 목록에서 뺀다. 검사가 디졸브를 혼입으로
                잡는 거짓 양성이 있어, 눈으로 확인한 줄을 지울 수단이 필요하다. */}
            {onBoundaryOk ? (
              <button type="button" style={{ ...miniBtn, flexShrink: 0 }}
                title="이 씬의 경계를 확인했고 문제가 없습니다 — 경계 오류 목록에서 뺍니다(저장됨). 나중에 이 씬 경계를 고치면 다시 나타납니다."
                onClick={(e) => { e.stopPropagation(); onBoundaryOk(i); }}>
                ✓ 문제없음</button>
            ) : null}
```

- [ ] **Step 3: 뷰의 인덱스 계산을 새 함수로 바꾼다**

`apps/desktop/src/console/SceneSplitView.tsx` `:857-865`의 아래 블록을

```ts
  const boundaryIssues = mode === "scene" ? (data?.boundary_issues ?? []) : [];
  // 플래그를 저장된 인덱스가 아니라 '현재 세그먼트의 라벨'로 다시 찾는다 — 병합·
  // 이름수정으로 라벨이 사라지면 자동 제외되고, 편집으로 인덱스가 밀려도 어긋나지
  // 않는다(편집 콜백의 clearBoundaryFlags가 고친 씬을 즉시 빼는 것과 합쳐 이중 안전).
  const curLabelToIdx = new Map(segments.map((s, i) => [s.label, i] as const));
  const boundaryIdx = boundaryIssues
    .map((b) => curLabelToIdx.get(b.label))
    .filter((i): i is number => i != null);
  const boundaryCount = boundaryIdx.length;
```

아래로 교체한다:

```ts
  const boundaryIssues = mode === "scene" ? (data?.boundary_issues ?? []) : [];
  const boundaryOk = mode === "scene" ? (data?.boundary_ok ?? []) : [];
  // 라벨로 다시 찾고(인덱스가 밀려도 안전), 사용자가 '문제없음'으로 확인한 구간은
  // 뺀다 — 단 그 뒤에 경계가 바뀌었으면 확인표시를 무시한다(boundaryIssueIndices).
  const boundaryIdx = boundaryIssueIndices(boundaryIssues, segments, boundaryOk);
  const boundaryCount = boundaryIdx.length;
  // 현재 목록에 남아 실제로 무언가를 숨기고 있는 확인표시 수 — '모두 해제' 안내용.
  const boundaryOkCount = boundaryIssueIndices(boundaryIssues, segments, []).length
    - boundaryCount;
```

같은 파일 상단 import에서 sceneSplitLogic 목록에 `boundaryIssueIndices`를, videoApi 목록에 `saveBoundaryOk`와 `type BoundaryOk`를 추가한다.

- [ ] **Step 4: 표시·해제 콜백을 추가한다**

`clearBoundaryFlags` 함수(`:534-540`) 바로 아래에 추가:

```ts
  // 확인 목록을 통째로 저장한다(전체 교체 — 서버도 같은 약속). 실패하면 화면 상태를
  // 되돌린다: 저장에 실패했는데 화면에서만 빼면 "뺐다고 봤는데 다음에 또 뜨는" 상태가
  // 된다. 낙관적 갱신 → 실패 시 롤백.
  const putBoundaryOk = async (next: BoundaryOk[]) => {
    if (!data) return;
    const before = data.boundary_ok ?? [];
    setData({ ...data, boundary_ok: next });
    try {
      await saveBoundaryOk(jobId, next);
    } catch (e) {
      setData((prev) => (prev ? { ...prev, boundary_ok: before } : prev));
      setError("확인 표시를 저장하지 못했습니다: "
        + (e instanceof Error ? e.message : String(e)));
    }
  };
  // 이 씬은 눈으로 확인했고 경계가 맞다 — 경계오류 목록에서 뺀다. 확인 당시의
  // 경계를 함께 남겨, 나중에 이 씬 경계를 고치면 다시 뜨게 한다.
  const markBoundaryOk = (i: number) => {
    const seg = segments[i];
    if (!seg) return;
    const rest = (data?.boundary_ok ?? []).filter((o) => o.label !== seg.label);
    void putBoundaryOk([...rest,
      { label: seg.label, start_ms: seg.start_ms, end_ms: seg.end_ms }]);
  };
```

- [ ] **Step 5: 탭 안내문에 "모두 해제"를 붙인다**

같은 파일 `:1453-1459`의 경계오류 안내 문단을 아래로 교체:

```tsx
          {onlyBoundaryErrors ? (
            <p style={{ fontSize: 12, opacity: 0.7, margin: 0 }}>
              경계(머리·꼬리) 프레임에 이웃 씬의 슬레이트가 잡힌 구간입니다 —
              익스포트 시 앞뒤 씬이 한두 프레임 섞일 수 있습니다. 썸네일을 눌러 실제
              경계 프레임을 확인하고, 필요하면 병합하거나 경계를 조정하세요.
              확인했는데 문제가 없으면 <b>✓ 문제없음</b>으로 목록에서 뺄 수 있습니다.
              {boundaryOkCount > 0 ? (
                <>
                  {"  "}확인함 {boundaryOkCount}건 ·{" "}
                  <button type="button" style={consoleStyles.mutedAction}
                    title="확인 표시를 전부 지우고 처음부터 다시 봅니다"
                    onClick={() => void putBoundaryOk([])}>모두 해제</button>
                </>
              ) : null}
            </p>
          ) : null}
```

- [ ] **Step 6: 필름스트립에 콜백을 넘긴다**

같은 파일 `<SceneFilmstrip ...>`(`:1460-1475`)의 `exportDisabled={...}` 줄 바로 아래에 추가:

```tsx
            // 경계오류 탭에서만 '✓ 문제없음'을 띄운다 — 다른 탭 줄에는 필요 없다.
            onBoundaryOk={onlyBoundaryErrors ? markBoundaryOk : undefined}
```

- [ ] **Step 7: 타입 검사와 테스트**

Run: `pnpm -C apps/desktop build:vite && pnpm -C apps/desktop test`
Expected: tsc 통과 + vitest 전량 통과

- [ ] **Step 8: 커밋**

```bash
git add apps/desktop/src/console/videoApi.ts \
        apps/desktop/src/console/SceneFilmstrip.tsx \
        apps/desktop/src/console/SceneSplitView.tsx
git commit -m "feat(desktop): 경계오류 '문제없음' 표시와 모두 해제"
```

---

### Task 4: 씬 분할 배선

**Files:**
- Modify: `apps/desktop/src/console/sceneSplitLogic.ts` (`ScenePopupAction` `:597-611`)
- Modify: `apps/desktop/src/console/SceneSplitView.tsx` (되돌리기 타입 `:548-551`, `undoEdit` `:584`, `trimAt` 뒤 `:779`, 키 처리 `:917`, 팝업 버튼 `:1708-1730`)
- Test: `apps/desktop/src/console/sceneSplitLogic.test.ts`

**Interfaces:**
- Consumes: Task 1의 `splitSegment(segs, i, k, fps)`, 기존 `segFrameNumber(ms, startMs, endMs, fps)`·`frameSeekMs(ms, fps)`·`previewLabel(tokens, uptoIndex)`·`testOcrRegion(jobId, tMs, region)`·`buildSegPreview(seg, segIndex, seekMs, side)`·`renameSeg(i, label)`·`clearBoundaryFlags(labels)`
- Produces: 없음(최종 배선)

- [ ] **Step 1: 단축키 테스트를 쓴다**

`apps/desktop/src/console/sceneSplitLogic.test.ts`의 기존 `describe("scenePopupAction", …)` 블록(`:474`) **안에** 케이스를 추가한다:

```ts
  it("maps S to split, by code and by key (한글 입력 상태 포함)", () => {
    // 한글 IME에서는 key가 'ㄴ'으로 오므로 code를 함께 본다(기존 키들과 동일 정책).
    expect(scenePopupAction({ code: "KeyS", key: "ㄴ" })).toBe("split");
    expect(scenePopupAction({ key: "s" })).toBe("split");
    expect(scenePopupAction({ key: "S" })).toBe("split");
  });
```

- [ ] **Step 2: 실패를 확인한다**

Run: `pnpm -C apps/desktop test -- sceneSplitLogic`
Expected: FAIL — `expected null to be "split"`

- [ ] **Step 3: 단축키를 추가한다**

`apps/desktop/src/console/sceneSplitLogic.ts` `:597-598`의 타입에 `"split"`을 더하고, `:604` 위에 분기를 추가:

```ts
export type ScenePopupAction =
  | "trimIn" | "trimOut" | "split" | "prevScene" | "nextScene" | "toHead" | "toTail";

export function scenePopupAction(
  ev: { code?: string; key?: string },
): ScenePopupAction | null {
  const key = (ev.key ?? "").toLowerCase();
  if (ev.code === "KeyI" || key === "i") return "trimIn";
  if (ev.code === "KeyO" || key === "o") return "trimOut";
  if (ev.code === "KeyS" || key === "s") return "split";
  ...
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `pnpm -C apps/desktop test -- sceneSplitLogic`
Expected: PASS

- [ ] **Step 5: 되돌리기 스택에 split을 더한다**

`apps/desktop/src/console/SceneSplitView.tsx` `:549`의

```ts
    { kind: "merge" | "boundary"; segs: SceneSegment[];
```

를 아래로 바꾼다:

```ts
    { kind: "merge" | "boundary" | "split"; segs: SceneSegment[];
```

`undoEdit`의 팝업 복원 조건(`:584`)

```ts
    if (top.kind === "boundary" && p?.segIndex === top.survivor && restored) {
```

를 아래로 바꾼다(분할도 팝업이 옛 경계를 들고 있어 다시 만들어야 한다):

```ts
    if ((top.kind === "boundary" || top.kind === "split")
        && p?.segIndex === top.survivor && restored) {
```

- [ ] **Step 6: `splitAt`을 추가한다**

`trimAt` 함수(`:765-779`) 바로 아래에 추가:

```ts
  // 한 씬 안에 두 씬이 붙어 있을 때(스캔이 그 컷을 못 잡은 경우) 지금 보는 프레임에서
  // 나눈다. 지금까지 할 수 있는 편집은 병합·이름수정·트림뿐이라 나눌 수단이 재스캔밖에
  // 없었다(25분 + 수동 정렬 초기화).
  //
  // 지금 보는 프레임이 뒤 구간의 첫 프레임이 된다 — In 트림과 같은 약속이고 자르는
  // 계산도 같다. 뒤 구간이 원래 이름을 유지하고, 앞 구간 이름은 슬레이트를 읽어 채운다.
  const splitAt = async (ms?: number) => {
    const p = previewRef.current;
    if (!p || p.segIndex == null || p.startMs == null || p.endMs == null) return;
    const i = p.segIndex;
    const cur = segments[i];
    if (!cur) return;
    const at = ms ?? (previewVideoRef.current
      ? previewVideoRef.current.currentTime * 1000 : p.seekMs);
    const { k } = segFrameNumber(at, p.startMs, p.endMs, p.fps);
    const next = splitSegment(segments, i, k, p.fps);
    if (next === segments) {
      setNotice("첫 프레임에서는 나눌 수 없습니다 — 뒤 씬이 시작되는 프레임으로 옮기세요.");
      return;
    }
    setEditUndo((prev) => [...prev,
      { kind: "split", segs: segments, issues: data?.boundary_issues, survivor: i }]);
    setSegments(next);
    // 혼입을 방금 해결했으므로 그 씬의 경계오류 표시를 뺀다(병합과 동일한 처리).
    clearBoundaryFlags([cur.label]);
    // 팝업이 옛 경계를 들고 있으면 화면과 데이터가 어긋나 사용자가 방금 한 편집을
    // 또 한다 — 앞 구간 기준으로 다시 만들고 그 머리 프레임으로 시킹한다.
    const head = next[i] as SceneSegment;
    const focusMs = frameSeekMs(head.start_ms, p.fps);
    setSelectedSeg(i);
    setPreview(buildSegPreview(head, i, focusMs, "head"));
    const v = previewVideoRef.current;
    if (v) { v.pause(); v.currentTime = focusMs / 1000; }
    setNotice("씬을 나눴습니다 — 앞 구간 이름을 읽는 중…");
    // 앞 구간 한가운데 프레임의 슬레이트를 읽어 이름을 제안한다. 머리·꼬리는 디졸브에
    // 걸릴 확률이 높아 한가운데를 읽는다. 저장된 구역을 그대로 넘겨야 스캔과 같은
    // 상자를 읽어 같은 라벨이 나온다. 실패해도 분할은 유지한다 — 경계는 이미 맞았고
    // 남은 건 이름뿐이다.
    try {
      const midMs = frameSeekMs((head.start_ms + head.end_ms) / 2, p.fps);
      const res = await testOcrRegion(jobId, Math.round(midMs), ocrRegion);
      const upto = mode === "sequence"
        ? Math.max(-1, ...seqIdx)
        : Math.max(-1, ...seqIdx, ...sceneIdx);
      const proposed = previewLabel(res.tokens, upto);
      if (proposed && proposed !== head.label) {
        renameSeg(i, proposed);
        setNotice(`앞 구간 이름을 ${proposed}으로 읽었습니다 — 다르면 이름칸에서 고치세요.`);
      } else {
        setNotice("앞 구간 슬레이트를 읽지 못했습니다 — 이름을 직접 입력하세요.");
      }
    } catch {
      setNotice("앞 구간 슬레이트를 읽지 못했습니다 — 이름을 직접 입력하세요.");
    }
  };
```

같은 파일 상단 import에서 sceneSplitLogic 목록에 `splitSegment`를, videoApi 목록에 `testOcrRegion`을 추가한다(`previewLabel`·`frameSeekMs`·`segFrameNumber`는 이미 있다).

- [ ] **Step 7: 단축키를 배선한다**

`:917-919`의 분기 사슬에 한 갈래를 끼운다:

```ts
      if (action === "trimIn" || action === "trimOut") {
        trimAt(action === "trimIn" ? "in" : "out");
      } else if (action === "split") {
        void splitAt();
      } else if (action === "prevScene" || action === "nextScene") {
```

**의존성 배열도 함께 고친다.** 이 `useEffect`의 deps(`:928`)는 지금 `[preview, segments, visibleIndices, data]`인데, `splitAt`은 이름 제안에 `mode`·`seqIdx`·`sceneIdx`·`ocrRegion`을 쓴다. 빠뜨리면 단축키 경로가 **옛 구역·옛 토큰 규칙**을 들고 OCR을 부른다(버튼 경로는 매 렌더 새로 만들어져 멀쩡하므로, 키보드로만 재현되는 종류의 버그다):

```ts
  }, [preview, segments, visibleIndices, data, mode, seqIdx, sceneIdx, ocrRegion]);
```

- [ ] **Step 8: 팝업에 버튼을 단다**

`:1712-1721`의 In/Out 버튼 두 개 **뒤**, `{canUndo ? (` 앞에 추가:

```tsx
                    <button type="button" style={editBtn}
                      disabled={k <= 1}
                      title="지금 보는 프레임부터 새 씬으로 나눕니다 — 앞쪽이 새 씬이 되고 이름은 슬레이트를 읽어 채웁니다 (단축키 S)"
                      onClick={() => void splitAt(previewMs)}>
                      ✂ 여기서 나누기(S) · 앞 {k - 1}f | 뒤 {n - k + 1}f</button>
```

`:1707`의 `canUndo` 계산을 아래로 바꾼다(분할도 팝업에서 물린다):

```tsx
                const canUndo = (top?.kind === "boundary" || top?.kind === "split")
                  && top.survivor === i;
```

- [ ] **Step 9: 타입 검사와 테스트**

Run: `pnpm -C apps/desktop build:vite && pnpm -C apps/desktop test`
Expected: tsc 통과 + vitest 전량 통과

- [ ] **Step 10: 커밋**

```bash
git add apps/desktop/src/console/sceneSplitLogic.ts \
        apps/desktop/src/console/sceneSplitLogic.test.ts \
        apps/desktop/src/console/SceneSplitView.tsx
git commit -m "feat(desktop): 팝업에서 씬 나누기 + 앞 구간 이름 자동 판독"
```

---

### Task 5: 도움말 + 실기 검증

**Files:**
- Modify: `apps/desktop/src/help/helpManualContent.ts:219`

**Interfaces:**
- Consumes: Task 1~4 전부
- Produces: 없음

- [ ] **Step 1: 도움말을 추가한다**

`apps/desktop/src/help/helpManualContent.ts:219`의 씬 분할 설명 문자열에서 `"…'↩되돌리기')."` 뒤에 아래 두 문장을 이어 붙인다:

```
한 줄에 두 씬이 붙어 있으면(스캔이 그 컷을 못 잡은 경우) 뒤 씬이 시작되는 프레임까지 옮긴 뒤 '✂ 여기서 나누기'(단축키 S)를 누르세요 — 그 프레임부터 뒤 씬이 되고, 앞 씬 이름은 앱이 슬레이트를 읽어 채웁니다(다르면 이름칸에서 고치면 됩니다). '⚠ 경계 오류' 목록에서 눈으로 확인했는데 문제가 없는 씬은 '✓ 문제없음'을 눌러 빼세요 — 저장되어 앱을 다시 켜도 안 뜨고, 나중에 그 씬 경계를 고치면 다시 나타납니다.
```

- [ ] **Step 2: 서버 번들을 재동결한다**

서버에 라우트를 추가했으므로 재동결하지 않으면 dev에서 `POST .../scenes/boundary-ok`가 404다.

**먼저 `tauri:dev`가 떠 있지 않은지 확인한다** — 실행 중 재동결은 금지(번들 바이너리를 덮어써 실행 중인 앱이 깨진다).

```bash
pgrep -fl "tauri.js dev|target/debug/yeson-" || echo "안 떠 있음 — 재동결 가능"
./apps/server_desktop/scripts/build-server.sh
```
Expected: 스모크 테스트 PASS 후 정상 종료

- [ ] **Step 3: 실기 검증 — 분할**

`pnpm -C apps/server_desktop tauri:dev`와 `pnpm -C apps/desktop tauri:dev`로 두 앱을 띄우고, 실제 번들에서 한 줄에 두 씬이 붙은 구간(예 `HH0305_140_0290`)을 연다.

확인할 것:
- 뒤 씬이 시작되는 프레임에서 `✂ 여기서 나누기` → 줄이 둘로 갈라지고 **뒤 줄이 원래 이름을 유지**한다
- 앞 줄 이름이 `HH0305_140_0280`으로 채워진다(못 읽으면 안내가 뜨고 이름칸으로 고칠 수 있다)
- 첫 프레임에서는 버튼이 **잠겨 있다**
- `↩되돌리기`로 원래 한 줄로 돌아간다
- 저장 후 그 두 씬을 익스포트하면 **두 mp4의 프레임 수 합이 원래 클립과 같다**

- [ ] **Step 4: 실기 검증 — 문제없음**

`⚠ 경계 오류` 탭에서:
- 한 줄의 `✓ 문제없음`을 누르면 그 줄이 사라지고 탭 숫자가 1 줄어든다
- **앱을 껐다 켜도** 그 줄은 안 뜬다
- 그 씬을 골라 In/Out 트림으로 경계를 한 프레임 옮기면 **다시 경계오류로 뜬다**
- `모두 해제`를 누르면 확인했던 줄이 전부 돌아온다

- [ ] **Step 5: PR 생성**

```bash
git push -u origin feature/scene-split-and-boundary-ok
gh pr create --base main --title "feat: 씬 나누기 + 경계오류 '문제없음'" --body "$(cat <<'EOF'
## 요약

씬 검수에 빠져 있던 두 가지를 채운다.

**① 씬 나누기** — 한 줄에 두 씬이 붙어 있어도(스캔이 그 컷을 못 잡음) 나눌 수단이 없었다. 유일한 우회가 재스캔인데 25분이 걸리고 수동 정렬이 초기화된다. 팝업에서 뒤 씬이 시작되는 프레임으로 옮기고 `✂ 여기서 나누기`(`S`)를 누르면 그 프레임부터 뒤 씬이 된다. 자르는 계산은 In 트림과 **같은 함수**라 익스포트 `-ss` 규칙과 어긋나지 않는다. 앞 구간 이름은 기존 미리읽기로 슬레이트를 읽어 채운다(서버 변경 없음).

**② 경계오류 '문제없음'** — 검사가 디졸브를 혼입으로 잡는 거짓 양성이 있는데 확인해도 목록에서 뺄 수가 없었다. `✓ 문제없음`으로 빼면 저장되어 앱을 다시 켜도, 다시 검사해도 안 뜬다. 다만 **그 씬 경계가 바뀌면 자동으로 풀린다** — 바뀐 경계를 안 본 채로 숨기지 않기 위해서다.

설계: `docs/superpowers/specs/2026-07-28-scene-split-and-boundary-ok-design.md`

## 검증

- vitest — 분할 경계가 In 트림과 일치·타임라인 연속·첫 프레임 거부, 확인표시가 경계 변경 시 되살아남
- pytest — 저장·전체 교체·빈 목록 해제·옛 스캔 기본값
- 실기(맥) — 실제 번들에서 분할·이름 자동판독·되돌리기·앱 재시작 후 확인표시 유지

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01VwNSe6SopfMBiM1QAqCi1h
EOF
)"
```

> **주의**: 머지는 사용자가 직접 한다(`! gh pr merge`) — 이 리포는 자기 PR 머지가 가드에 막힌다.

---

## 자체 점검 결과

**설계 커버리지** — 설계 문서 각 절이 어느 태스크에 있는지:
- §2 결정 1(팝업 현재 프레임) → Task 4 Step 6·8
- §2 결정 2(In 트림과 같은 계산) → Task 1 `splitSegment` + 그 테스트가 `shiftBoundaryMs` 값을 직접 대조
- §2 결정 3(뒤 구간이 원래 이름) → Task 1 `splitSegment`(둘 다 원래 라벨) + Task 4가 앞 구간만 rename
- §2 결정 4(슬레이트 읽어 제안) → Task 4 Step 6
- §2 결정 5(첫 프레임 금지) → Task 1(`k<=1` 무변화) + Task 4 Step 8(버튼 disabled)
- §2 결정 6(저장 + 경계 바뀌면 해제) → Task 2(저장) + Task 1 `boundaryIssueIndices`(해제 판정)
- §2 결정 7(라벨 기준 재조회) → Task 1 `boundaryIssueIndices`가 기존 인라인 로직을 흡수
- §3 순수 함수·화면 흐름 → Task 1·4
- §4 저장 형태·API·순수 함수·화면 → Task 2·3
- §5 변경 파일 → Task 1~5가 7개 파일 전부 덮는다
- §6 검증 → Task 1·2의 테스트 단계 + Task 5 실기

**되돌리기 일관성**: 분할 되돌리기는 팝업에 뜬다(경계 교정과 같은 자리, Task 4 Step 8). 줄의 되돌리기는 지금처럼 병합 전용이다 — `undoIndex`는 `kind === "merge"`일 때만 넘어가므로 건드리지 않는다.

**의도적으로 하지 않은 것**: 분할 직후 OCR 응답 전(또는 판독 실패 시)에는 같은 이름 두 줄이 잠깐 인접하고, 목록의 "인접 중복 병합" 카운트가 1 오른다. 그 버튼을 누르면 방금 나눈 것이 도로 합쳐지지만 되돌리기가 가능하고 이름을 붙이면 사라지므로 별도 방어를 넣지 않는다(설계 §3 "알아둘 것"과 동일 판단).
