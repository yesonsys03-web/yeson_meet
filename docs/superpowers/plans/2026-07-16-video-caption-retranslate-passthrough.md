# 자막메이커 영문 잔존 구간 일괄 재번역 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 결과보기에서 번역이 안 되고 영문으로 남은 구간을 자동 판별해, 사용자가 고른 엔진으로 한 번에 재번역한다.

**Architecture:** 판별은 `text_ko == text_en`(원문 유지 폴백 3경로가 전부 원문을 복사하므로 정확) + ascii 비율 보조. 서버가 대상 세그먼트만 골라 기존 `translate_segments`(글로서리·청킹 포함)로 다시 번역하고 `text_ko`를 갱신한다. 스키마 변경이 없어 기존 작업에 소급 적용된다. 동기 호출.

**Tech Stack:** FastAPI + SQLAlchemy(async) + Pydantic / React + TypeScript(Tauri) / pytest + vitest

## Global Constraints

- 스펙: `docs/superpowers/specs/2026-07-16-video-caption-retranslate-passthrough-design.md`
- 브랜치 `feature/video-retranslate-passthrough`, base `origin/main` = `76f3191`.
- **provider 검증 패턴을 새로 하드코딩하지 않는다** — `video_jobs.py:63`의 `_TRANSLATE_PROVIDER_PATTERN`(= `list_translate_engines()`에서 자동 도출)을 재사용한다. v1.3.6 qwen 422 사고의 재발 방지 조건.
- **판별 상수를 복제하지 않는다** — ascii 임계는 `ai/mlx_live_translate.py`의 `_ASCII_LEAK_MAX`(=0.6) 하나만 존재해야 한다.
- **엔진 라벨 규칙을 복제하지 않는다** — `reason` → `available` → gemini 특례 순서의 라벨 규칙은 `VideoCaptionPanel.tsx:57-73` `toEngineOptions`에 이미 있다(v1.3.9 최종 리뷰가 "(서버에 미설치)" 오안내를 고친 코드). Task 6이 공유 헬퍼로 추출해 양쪽이 쓴다.
- **apps/desktop에 testing-library/jsdom이 없다** — 컴포넌트 렌더 테스트 불가. 이 리포 관례는 `.tsx`에서 **순수 함수를 export해 vitest로 단위 테스트**하는 것이다(`toEngineOptions` ← `VideoCaptionPanel.test.ts`).
- **번역은 `translate_segments`를 거친다** — `_translate_resilient`를 직접 부르면 글로서리 보정(`apply_ko_corrections`)과 50줄 청킹이 빠져 기존 번역과 불일치한다.
- 사용자 수동 편집 줄(`text_ko != text_en`)은 어떤 경우에도 덮어쓰지 않는다.
- 서버 테스트 실행: 이 인텔맥은 워크스페이스 `uv.lock`에 onnxruntime x86_64 휠이 없어 full deps 설치가 안 된다. 스크래치 venv를 쓴다(기존 관례).

---

### Task 1: `is_english_leak` 공개 헬퍼

`_ASCII_LEAK_MAX`/`_ASCII_ALPHA_RE`는 private다. 검수 판별이 같은 기준을 쓰되 상수를 복제하지 않도록 공개 헬퍼를 노출하고, `guard_mlx_ko`가 그것을 쓰게 정리한다.

**Files:**
- Modify: `apps/server/ai/mlx_live_translate.py:48-67` (`guard_mlx_ko`)
- Test: `apps/server/tests/test_mlx_live_translate.py`

**Interfaces:**
- Consumes: 없음
- Produces: `is_english_leak(text: str) -> bool`

- [ ] **Step 1: Write the failing test**

`apps/server/tests/test_mlx_live_translate.py` 끝에 추가:

```python
def test_is_english_leak():
    from apps.server.ai.mlx_live_translate import is_english_leak

    assert is_english_leak("Margarita vibes, baby girl!") is True
    assert is_english_leak("마르가리타 분위기야!") is False
    assert is_english_leak("") is False
    # 한글에 고유명사가 섞인 정도는 누수가 아니다
    assert is_english_leak("Margarita 한 잔 하자") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/server/tests/test_mlx_live_translate.py::test_is_english_leak -v`
Expected: FAIL — `ImportError: cannot import name 'is_english_leak'`

- [ ] **Step 3: Write minimal implementation**

`apps/server/ai/mlx_live_translate.py`에서 `guard_mlx_ko` **바로 앞**에 추가:

```python
def is_english_leak(text: str) -> bool:
    """ascii 알파벳 비율이 임계를 넘으면 영어 누수로 본다.

    guard_mlx_ko(생성 시점)와 검수 단계의 영문 잔존 판별이 같은 기준을 쓰도록
    공개한다 — 임계값이 두 곳에 복제되면 두 정의가 갈라진다.
    """
    stripped = text.strip()
    if not stripped:
        return False
    ascii_alpha = len(_ASCII_ALPHA_RE.findall(stripped))
    return ascii_alpha / max(1, len(stripped)) > _ASCII_LEAK_MAX
```

그리고 `guard_mlx_ko` 안의 기존 3줄

```python
    ascii_alpha = len(_ASCII_ALPHA_RE.findall(ko_stripped))
    if ascii_alpha / max(1, len(ko_stripped)) > _ASCII_LEAK_MAX:
        return "english_leak"
```

를 다음으로 교체:

```python
    if is_english_leak(ko_stripped):
        return "english_leak"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest apps/server/tests/test_mlx_live_translate.py -v`
Expected: PASS — 신규 `test_is_english_leak` 포함 **기존 가드 테스트가 전부 그대로 통과**해야 한다(리팩터링이라 동작 변화 없음).

- [ ] **Step 5: Commit**

```bash
git add apps/server/ai/mlx_live_translate.py apps/server/tests/test_mlx_live_translate.py
git commit -m "refactor(translate): is_english_leak 공개 — 가드와 검수 판별이 임계를 공유"
```

---

### Task 2: `is_untranslated` 판별 함수

**Files:**
- Modify: `apps/server/domain/video_captions/translate.py` (`apply_ko_guard` 뒤, 72행 이후)
- Test: `apps/server/tests/test_video_translate.py`

**Interfaces:**
- Consumes: `is_english_leak(text: str) -> bool` (Task 1)
- Produces: `is_untranslated(text_en: str, text_ko: str) -> bool`

- [ ] **Step 1: Write the failing test**

`apps/server/tests/test_video_translate.py` 끝에 추가:

```python
def test_is_untranslated():
    from apps.server.domain.video_captions.translate import is_untranslated

    src = "Margarita vibes, baby girl!"
    # 폴백 3경로는 전부 원문을 그대로 복사한다 — 1차 판별
    assert is_untranslated(src, src) is True
    assert is_untranslated(src, f"  {src}  ") is True  # 공백 차이 무시
    # 정상 번역
    assert is_untranslated(src, "마르가리타 분위기야, 자기!") is False
    # 원문과 다르지만 여전히 영어 — 2차 판별
    assert is_untranslated(src, "Margarita mood, girl!") is True
    # 사용자가 의도적으로 비운 줄은 건드리지 않는다
    assert is_untranslated(src, "") is False
    assert is_untranslated(src, "   ") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/server/tests/test_video_translate.py::test_is_untranslated -v`
Expected: FAIL — `ImportError: cannot import name 'is_untranslated'`

- [ ] **Step 3: Write minimal implementation**

`apps/server/domain/video_captions/translate.py`의 `apply_ko_guard` 정의 **바로 뒤**에 추가:

```python
def is_untranslated(text_en: str, text_ko: str) -> bool:
    """검수용 — 번역이 안 되고 영문이 남은 줄인가.

    영문이 남는 폴백 3경로(apply_ko_guard 불합격 / _translate_resilient 1줄
    실패 / Apple 언어팩 미설치)가 전부 원문을 그대로 복사하므로, 동일 비교가
    오탐 없는 1차 판별이다. 2차는 원문과 다르지만 여전히 영어인 경우(가드가
    없는 provider).

    빈 줄은 대상이 아니다 — 사용자가 의도적으로 비운 자막을 되살리면 안 된다.
    """
    from apps.server.ai.mlx_live_translate import is_english_leak

    ko = text_ko.strip()
    if not ko:
        return False
    if ko == text_en.strip():
        return True
    return is_english_leak(ko)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest apps/server/tests/test_video_translate.py::test_is_untranslated -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/server/domain/video_captions/translate.py apps/server/tests/test_video_translate.py
git commit -m "feat(translate): is_untranslated — 영문 잔존 구간 판별"
```

---

### Task 3: `maybe_aclose_translator` 공개화

Task 4가 번역기 수명주기를 다뤄야 하는데, 현재 그 로직은 `pipeline.py:34`의 private `_maybe_aclose_translator`다. 복제 대신 `translate.py`로 올려 공유한다(MLX 번역기는 워커 프로세스를 붙들고 있어 닫지 않으면 샌다).

**Files:**
- Modify: `apps/server/domain/video_captions/translate.py` (`is_untranslated` 뒤)
- Modify: `apps/server/domain/video_captions/pipeline.py:34-39`, `:301`
- Test: `apps/server/tests/test_video_translate.py`

**Interfaces:**
- Consumes: 없음
- Produces: `async maybe_aclose_translator(translator) -> None`

- [ ] **Step 1: Write the failing test**

`apps/server/tests/test_video_translate.py` 끝에 추가:

이 리포는 `asyncio_mode = "auto"`라 `@pytest.mark.asyncio`를 붙이지 않는다(`test_video_translate.py`의 기존 async 테스트들과 동일).

```python
async def test_maybe_aclose_translator():
    from apps.server.domain.video_captions.translate import maybe_aclose_translator

    closed = []

    class WithAclose:
        async def aclose(self):
            closed.append(True)

    class WithoutAclose:
        pass

    await maybe_aclose_translator(WithAclose())
    assert closed == [True]
    # aclose가 없는 번역기(gemini/CLI/apple)는 조용히 무시된다
    await maybe_aclose_translator(WithoutAclose())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest apps/server/tests/test_video_translate.py::test_maybe_aclose_translator -v`
Expected: FAIL — `ImportError: cannot import name 'maybe_aclose_translator'`

- [ ] **Step 3: Write minimal implementation**

`apps/server/domain/video_captions/translate.py`에 추가:

```python
async def maybe_aclose_translator(translator) -> None:
    """번역기가 들고 있는 자원을 닫는다(MLX는 워커 프로세스를 붙든다).

    aclose가 없는 번역기(gemini/CLI/apple)는 무시한다.
    """
    aclose = getattr(translator, "aclose", None)
    if aclose is not None:
        await aclose()
```

`apps/server/domain/video_captions/pipeline.py`에서 `_maybe_aclose_translator` 정의(34-39행)를 **삭제**하고, import에 추가:

```python
from .translate import maybe_aclose_translator, translate_segments
```

그리고 301행의 호출부를 교체:

```python
            await maybe_aclose_translator(translator)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest apps/server/tests/test_video_translate.py::test_maybe_aclose_translator apps/server/tests/test_video_pipeline.py -v`
Expected: PASS — 파이프라인 테스트가 그대로 통과해야 한다(이동일 뿐 동작 변화 없음).

- [ ] **Step 5: Commit**

```bash
git add apps/server/domain/video_captions/translate.py apps/server/domain/video_captions/pipeline.py apps/server/tests/test_video_translate.py
git commit -m "refactor(translate): maybe_aclose_translator 공개 — 파이프라인과 재번역이 공유"
```

---

### Task 4: `POST /{external_id}/retranslate` 엔드포인트

**Files:**
- Modify: `apps/server/api/v1/video_jobs.py` (`rebuild_video_job` 뒤, 418행 부근)
- Test: `apps/server/tests/test_api_video_jobs.py`

**Interfaces:**
- Consumes: `is_untranslated(text_en, text_ko) -> bool` (Task 2), `maybe_aclose_translator(translator)` (Task 3)
- Produces: `POST /api/v1/video-jobs/{external_id}/retranslate` — body `{"provider": str, "cli_model": str | None}`, 응답 `{"total": int, "retranslated": int, "remaining": int}`

- [ ] **Step 1: Write the failing test**

`apps/server/tests/test_api_video_jobs.py` 끝에 추가.

관례 주의: 이 리포는 `asyncio_mode = "auto"`(`pyproject.toml:11`)라 **`@pytest.mark.asyncio`를 붙이지 않는다**. job/segment는 헬퍼 없이 인라인 생성한다(`test_detail_includes_segments_and_patch_edits:66` 참조). 커밋 후 재조회 전에는 `db_session.expire_all()`이 필요하다. `video_jobs` 모듈은 이 파일에 이미 `api_vj`로 import돼 있다.

**패턴 드리프트 테스트는 새로 쓰지 않는다** — `test_create_pattern_accepts_every_listed_engine_value:287`이 이미 `_TRANSLATE_PROVIDER_PATTERN`을 검증하고, `RetranslateIn`이 그 **같은 상수**를 재사용하므로 자동으로 커버된다. 복제하면 DRY 위반이다.

```python
async def test_retranslate_only_touches_untranslated(client, db_session, admin_user,
                                                     monkeypatch):
    """핵심 안전 속성: 수동 편집 줄은 절대 덮어쓰지 않는다."""
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4",
                   whisper_model="small", status="review")
    db_session.add(job)
    await db_session.flush()
    job_id = job.id
    db_session.add(VideoSegment(job_id=job_id, seq=1, start_ms=0, end_ms=900,
                                text_en="Hello there", text_ko="Hello there"))  # 영문 잔존
    db_session.add(VideoSegment(job_id=job_id, seq=2, start_ms=900, end_ms=1800,
                                text_en="Good morning", text_ko="좋은 아침"))    # 사용자 편집
    await db_session.commit()

    class FakeTranslator:
        async def translate_batch(self, texts):
            return ["안녕하세요"] * len(texts)

    monkeypatch.setattr(api_vj, "create_translator", lambda p, m: FakeTranslator())
    monkeypatch.setattr(api_vj, "list_translate_engines",
                        lambda: [{"value": "claude", "label": "Claude 구독",
                                  "available": True}])

    resp = await client.post(f"/api/v1/video-jobs/{job.external_id}/retranslate",
                             json={"provider": "claude"})
    assert resp.status_code == 200
    assert resp.json() == {"total": 1, "retranslated": 1, "remaining": 0}

    db_session.expire_all()
    rows = (await db_session.execute(
        select(VideoSegment).where(VideoSegment.job_id == job_id)
        .order_by(VideoSegment.seq))).scalars().all()
    assert rows[0].text_ko == "안녕하세요"   # 영문 잔존만 갱신
    assert rows[1].text_ko == "좋은 아침"     # 편집 보존


async def test_retranslate_rejects_unavailable_engine(client, db_session, admin_user,
                                                      monkeypatch):
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4",
                   whisper_model="small", status="review")
    db_session.add(job)
    await db_session.commit()
    monkeypatch.setattr(api_vj, "list_translate_engines",
                        lambda: [{"value": "claude", "label": "Claude 구독",
                                  "available": False}])
    resp = await client.post(f"/api/v1/video-jobs/{job.external_id}/retranslate",
                             json={"provider": "claude"})
    assert resp.status_code == 409


async def test_retranslate_rejects_running_job(client, db_session, admin_user):
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4",
                   whisper_model="small", status="transcribing")
    db_session.add(job)
    await db_session.commit()
    resp = await client.post(f"/api/v1/video-jobs/{job.external_id}/retranslate",
                             json={"provider": "claude"})
    assert resp.status_code == 409


async def test_retranslate_reports_remaining(client, db_session, admin_user,
                                             monkeypatch):
    """재번역해도 여전히 영문이면 remaining으로 보고한다."""
    job = VideoJob(external_id=uuid4(), owner_user_id=admin_user.id, title="t",
                   source_type="upload", source_ref="c.mp4",
                   whisper_model="small", status="review")
    db_session.add(job)
    await db_session.flush()
    db_session.add(VideoSegment(job_id=job.id, seq=1, start_ms=0, end_ms=900,
                                text_en="Hello there", text_ko="Hello there"))
    await db_session.commit()

    class EchoTranslator:
        async def translate_batch(self, texts):
            return list(texts)   # 영문 그대로 반환

    monkeypatch.setattr(api_vj, "create_translator", lambda p, m: EchoTranslator())
    monkeypatch.setattr(api_vj, "list_translate_engines",
                        lambda: [{"value": "claude", "label": "Claude 구독",
                                  "available": True}])

    resp = await client.post(f"/api/v1/video-jobs/{job.external_id}/retranslate",
                             json={"provider": "claude"})
    assert resp.json() == {"total": 1, "retranslated": 0, "remaining": 1}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest apps/server/tests/test_api_video_jobs.py -k retranslate -v`
Expected: FAIL — 404 (라우트 없음)

- [ ] **Step 3: Write minimal implementation**

`apps/server/api/v1/video_jobs.py` import에 추가:

```python
from apps.server.domain.video_captions.translate import (is_untranslated,
                                                         maybe_aclose_translator,
                                                         translate_segments)
from apps.server.domain.video_captions.translate_cli import (create_translator,
                                                             list_translate_engines)
```

(기존 38행의 `from ...translate_cli import list_translate_engines`는 위 줄로 합친다.)

`rebuild_video_job` 뒤에 추가:

```python
class RetranslateIn(BaseModel):
    provider: str = Field(pattern=_TRANSLATE_PROVIDER_PATTERN)
    cli_model: str | None = None


@router.post("/{external_id}/retranslate")
async def retranslate_video_job(
    external_id: UUID,
    body: RetranslateIn,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict:
    """영문으로 남은 세그먼트만 골라 지정 엔진으로 다시 번역한다.

    대상이 is_untranslated를 만족하는 줄로 한정되므로 사용자가 직접 수정한 줄은
    덮어쓰지 않는다 — 검수 편집을 통째로 폐기하는 rebuild와 다른 점이다.
    번역은 translate_segments를 거쳐 글로서리 보정·청킹을 그대로 탄다.
    """
    job = await _get_job_or_404(db, external_id)
    if job.status not in ("review", "done", "error", "cancelled"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"진행 중인 작업은 재번역할 수 없습니다 (status={job.status})")
    engine = next(
        (e for e in list_translate_engines() if e["value"] == body.provider), None)
    if engine is None or not engine["available"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"이 서버에서 사용할 수 없는 번역 엔진입니다: {body.provider}")

    rows = (await db.execute(
        select(VideoSegment)
        .where(VideoSegment.job_id == job.id)
        .order_by(VideoSegment.seq)
    )).scalars().all()
    targets = [r for r in rows if is_untranslated(r.text_en, r.text_ko)]
    if not targets:
        return {"total": 0, "retranslated": 0, "remaining": 0}

    translator = create_translator(body.provider, body.cli_model)
    try:
        out = await translate_segments(
            [SubSegment(seq=r.seq, start_ms=r.start_ms, end_ms=r.end_ms,
                        text=r.text_en) for r in targets],
            translator,
        )
    finally:
        await maybe_aclose_translator(translator)

    by_seq = {s.seq: s.text for s in out}
    retranslated = 0
    for row in targets:
        ko = by_seq.get(row.seq)
        if ko is None or is_untranslated(row.text_en, ko):
            continue
        row.text_ko = ko
        retranslated += 1
    await db.commit()
    return {"total": len(targets), "retranslated": retranslated,
            "remaining": len(targets) - retranslated}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest apps/server/tests/test_api_video_jobs.py -v`
Expected: PASS — 신규 retranslate 4건 + **기존 테스트 전부**(특히 `test_create_pattern_accepts_every_listed_engine_value`가 `RetranslateIn`이 재사용하는 패턴을 커버한다)

- [ ] **Step 5: Commit**

```bash
git add apps/server/api/v1/video_jobs.py apps/server/tests/test_api_video_jobs.py
git commit -m "feat(video): 영문 잔존 구간 일괄 재번역 엔드포인트"
```

---

### Task 5: 클라 판별 + API 함수

**Files:**
- Modify: `apps/desktop/src/console/videoReviewLogic.ts`
- Modify: `apps/desktop/src/console/videoApi.ts:262` 부근 (`burnVideoJob` 뒤)
- Test: `apps/desktop/src/console/videoReviewLogic.test.ts`

**Interfaces:**
- Consumes: 없음
- Produces: `isUntranslated(textEn: string, textKo: string): boolean`, `retranslateSegments(jobId: string, provider: string, cliModel?: string): Promise<RetranslateResult>`, `type RetranslateResult = { total: number; retranslated: number; remaining: number }`

- [ ] **Step 1: Write the failing test**

`apps/desktop/src/console/videoReviewLogic.test.ts` 끝에 추가:

```ts
describe("isUntranslated", () => {
  const src = "Margarita vibes, baby girl!";

  it("원문을 그대로 복사한 줄을 잡는다", () => {
    expect(isUntranslated(src, src)).toBe(true);
    expect(isUntranslated(src, `  ${src}  `)).toBe(true);
  });

  it("정상 번역은 통과시킨다", () => {
    expect(isUntranslated(src, "마르가리타 분위기야, 자기!")).toBe(false);
  });

  it("원문과 달라도 여전히 영어면 잡는다", () => {
    expect(isUntranslated(src, "Margarita mood, girl!")).toBe(true);
  });

  it("의도적으로 비운 줄은 건드리지 않는다", () => {
    expect(isUntranslated(src, "")).toBe(false);
    expect(isUntranslated(src, "   ")).toBe(false);
  });
});
```

파일 상단 import에 `isUntranslated`를 추가한다.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/desktop && pnpm vitest run src/console/videoReviewLogic.test.ts`
Expected: FAIL — `isUntranslated is not exported`

- [ ] **Step 3: Write minimal implementation**

`apps/desktop/src/console/videoReviewLogic.ts`에 추가:

```ts
// 서버 ai/mlx_live_translate.py의 _ASCII_LEAK_MAX와 같은 값. 서버가 최종
// 판정자이고 이 함수는 배지 카운트·표시용이다 — 값이 갈라져도 저장되는
// 데이터는 서버 규칙을 따른다.
const ASCII_LEAK_MAX = 0.6;

/** 번역이 안 되고 영문이 남은 줄인가 — 서버 is_untranslated와 같은 규칙. */
export function isUntranslated(textEn: string, textKo: string): boolean {
  const ko = textKo.trim();
  if (!ko) return false;
  if (ko === textEn.trim()) return true;
  const asciiAlpha = (ko.match(/[A-Za-z]/g) ?? []).length;
  return asciiAlpha / Math.max(1, ko.length) > ASCII_LEAK_MAX;
}
```

`apps/desktop/src/console/videoApi.ts`의 `burnVideoJob` 뒤에 추가:

```ts
export type RetranslateResult = {
  total: number;
  retranslated: number;
  remaining: number;
};

export async function retranslateSegments(
  jobId: string, provider: string, cliModel?: string,
): Promise<RetranslateResult> {
  return request<RetranslateResult>(
    `${apiBase()}/api/v1/video-jobs/${jobId}/retranslate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider, cli_model: cliModel ?? null }),
    });
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/desktop && pnpm vitest run src/console/videoReviewLogic.test.ts && pnpm tsc --noEmit`
Expected: PASS, tsc 0 errors

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/src/console/videoReviewLogic.ts apps/desktop/src/console/videoReviewLogic.test.ts apps/desktop/src/console/videoApi.ts
git commit -m "feat(video): 클라 영문 잔존 판별 + 재번역 API"
```

---

### Task 6: 결과보기 UI

**Files:**
- Modify: `apps/desktop/src/console/VideoCaptionPanel.tsx:56-73` (`engineLabel` 추출)
- Modify: `apps/desktop/src/console/VideoReviewView.tsx` (상태 27-41행, 툴바 183행, 세그먼트 렌더 312-336행)
- Test: `apps/desktop/src/console/VideoCaptionPanel.test.ts`

**Interfaces:**
- Consumes: `isUntranslated` (Task 5), `retranslateSegments`, `RetranslateResult`, `listTranslateEngines`, `TranslateEngineInfo` (기존)
- Produces: `engineLabel(engine: TranslateEngineInfo): string` — `"번역: "` 접두 **없는** 엔진 표시 라벨

- [ ] **Step 0: 라벨 규칙을 공유 헬퍼로 추출 (RED 먼저)**

결과보기 드롭다운도 같은 라벨 규칙이 필요하다. 규칙을 복제하지 않도록 `toEngineOptions`에서 추출한다. `toEngineOptions`의 기존 동작은 **한 글자도 바뀌면 안 된다**(기존 테스트가 고정).

`apps/desktop/src/console/VideoCaptionPanel.test.ts`에 추가:

```ts
describe("engineLabel", () => {
  it("reason이 있으면 reason을 붙인다 — '번역: ' 접두는 붙이지 않는다", () => {
    expect(engineLabel({ value: "qwen_x", label: "Qwen 12B", available: false,
                         reason: "실리콘맥 전용" })).toBe("Qwen 12B (실리콘맥 전용)");
  });
  it("gemini 미가용은 키 없음으로 구분한다", () => {
    expect(engineLabel({ value: "gemini", label: "Gemini", available: false }))
      .toBe("Gemini (서버에 키 없음)");
  });
  it("그 외 미가용은 미설치", () => {
    expect(engineLabel({ value: "claude", label: "Claude 구독", available: false }))
      .toBe("Claude 구독 (서버에 미설치)");
  });
  it("가용하면 라벨 그대로", () => {
    expect(engineLabel({ value: "claude", label: "Claude 구독", available: true }))
      .toBe("Claude 구독");
  });
});
```

Run: `cd apps/desktop && pnpm vitest run src/console/VideoCaptionPanel.test.ts`
Expected: FAIL — `engineLabel is not exported`

`VideoCaptionPanel.tsx`의 `toEngineOptions`를 다음으로 교체(로직 이동일 뿐, 동작 동일):

```ts
// 엔진 표시 라벨 — 결과보기 재번역 드롭다운도 같은 규칙을 쓴다(규칙 복제 금지).
export function engineLabel(engine: TranslateEngineInfo): string {
  let label = engine.label;
  if (engine.reason) {
    // 이 서버가 런타임 자체를 지원 못하는 티어 — "미설치" 문구는 오히려
    // 오해를 부른다(설치해도 못 쓴다는 뜻이므로 사유를 그대로 보여준다).
    label += ` (${engine.reason})`;
  } else if (!engine.available) {
    label += engine.value === "gemini" ? " (서버에 키 없음)" : " (서버에 미설치)";
  }
  return label;
}

// 서버의 gemini(값 없음=기본)를 클라 상태값 ""와 맞추고, 미설치 엔진은 disabled 처리
export function toEngineOptions(engines: TranslateEngineInfo[]): EngineOption[] {
  return engines.map((engine) => {
    const isGemini = engine.value === "gemini";
    return {
      value: isGemini ? "" : engine.value,
      label: `번역: ${engineLabel(engine)}`,
      available: isGemini ? true : engine.available, // 기본값이므로 gemini는 항상 선택 허용
    };
  });
```

Run: `cd apps/desktop && pnpm vitest run src/console/VideoCaptionPanel.test.ts`
Expected: PASS — 신규 `engineLabel` 4건 + **기존 `toEngineOptions` 테스트 전부 그대로**

Commit:

```bash
git add apps/desktop/src/console/VideoCaptionPanel.tsx apps/desktop/src/console/VideoCaptionPanel.test.ts
git commit -m "refactor(video): engineLabel 추출 — 결과보기와 라벨 규칙 공유"
```

- [ ] **Step 1: 엔진 목록 로드 + 상태 추가**

`VideoReviewView.tsx` import에 추가:

```ts
import { listTranslateEngines, retranslateSegments } from "./videoApi";
import type { TranslateEngineInfo } from "./videoApi";
import { engineLabel } from "./VideoCaptionPanel";
import { isUntranslated } from "./videoReviewLogic";
```

상태 선언부(41행 뒤)에 추가:

```ts
  const [engines, setEngines] = useState<TranslateEngineInfo[]>([]);
  // 로컬 엔진 재시도는 같은 실패가 반복될 수 있으므로 기본값은 작업이 쓰던
  // 엔진이 아니라 Claude 구독이다.
  const [retProvider, setRetProvider] = useState("claude");
  const [retCliModel, setRetCliModel] = useState("");
  const [retranslating, setRetranslating] = useState(false);
```

엔진 로드 useEffect는 **`if (!job) return` 조기 반환(77행)보다 위**, 기존 useEffect들 옆(76행 뒤)에 넣는다 — 훅은 조기 반환 뒤에 올 수 없다.

```ts
  useEffect(() => {
    void listTranslateEngines().then(setEngines).catch(() => setEngines([]));
  }, []);
```

- [ ] **Step 2: 영문 잔존 카운트 + 재번역 핸들러**

⚠️ `segments`·`koOf`·`activeIdx`는 **이미 88-90행에 선언돼 있다**(`if (!job) return` 조기 반환 뒤). 재선언하면 컴파일이 깨진다. 아래 `untranslatedSeqs`는 **`activeText` 선언(92행) 바로 뒤**에 넣고, `runRetranslate`는 `saveEdits`(94-102행) 뒤에 넣는다.

92행 뒤에 추가:

```ts
  const untranslatedSeqs = new Set(
    segments.filter((s) => isUntranslated(s.text_en, koOf(s.seq, s.text_ko)))
            .map((s) => s.seq));
```

`saveEdits` 뒤에 추가:

```ts
  const runRetranslate = async () => {
    setRetranslating(true);
    setError(null);
    setNotice(null);
    try {
      const r = await retranslateSegments(
        jobId, retProvider,
        retProvider === "opencode" ? retCliModel : undefined);
      setNotice(r.remaining > 0
        ? `${r.total}개 중 ${r.retranslated}개 해결, ${r.remaining}개 남음`
        : `${r.retranslated}개 재번역 완료`);
      // 서버가 text_ko를 바꿨으므로 미저장 편집 버퍼를 비우고 다시 읽는다.
      // refresh()는 이미 있는 useCallback — getVideoJob을 직접 부르지 않는다.
      setEdits({});
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRetranslating(false);
    }
  };
```

- [ ] **Step 3: 툴바 UI 추가**

세그먼트 리스트 `<div>`(311행) 바로 안, `{segments.map(...)}` **앞**에 추가:

```tsx
          <div style={{ display: "flex", gap: 8, alignItems: "center",
                        flexWrap: "wrap", paddingBottom: 4 }}>
            <span style={{ fontSize: 12, opacity: 0.75 }}>
              {untranslatedSeqs.size > 0
                ? `영문 잔존 ${untranslatedSeqs.size}구간`
                : "영문 잔존 없음"}
            </span>
            <select
              style={{ ...consoleStyles.input, width: "auto", fontSize: 12 }}
              value={retProvider}
              onChange={(e) => setRetProvider(e.target.value)}>
              {/* 라벨 규칙은 engineLabel이 단일 출처 — 여기서 다시 짜지 않는다.
                  gemini는 value를 ""로 바꾸지 않는다: toEngineOptions의 ""는
                  VideoCaptionPanel 상태 규약이고, 이 엔드포인트는 provider를
                  그대로 받으므로 ""는 검증 패턴에서 거부된다. */}
              {engines.map((e) => (
                <option key={e.value} value={e.value} disabled={!e.available}>
                  {engineLabel(e)}
                </option>
              ))}
            </select>
            {retProvider === "opencode" ? (
              <input
                style={{ ...consoleStyles.input, width: 180, fontSize: 12 }}
                value={retCliModel}
                placeholder="모델명"
                onChange={(e) => setRetCliModel(e.target.value)} />
            ) : null}
            <button type="button" style={consoleStyles.mutedAction}
              disabled={untranslatedSeqs.size === 0 || retranslating}
              onClick={() => void runRetranslate()}>
              {retranslating ? "재번역 중…" : "영문 구간 일괄 재번역"}
            </button>
          </div>
```

- [ ] **Step 4: 영문 세그먼트 시각 표시**

세그먼트 렌더(314-317행)의 `border` 계산을 교체 — 활성 세그먼트 표시는 유지하고 영문 잔존을 주황 테두리로 구분한다:

```tsx
            <div key={seg.seq}
              style={{ padding: "6px 10px", borderRadius: 6,
                       border: `1px solid ${
                         idx === activeIdx ? "rgba(48,164,108,0.9)"
                         : untranslatedSeqs.has(seg.seq) ? "rgba(230,145,56,0.9)"
                         : "rgba(255,255,255,0.12)"}` }}>
```

- [ ] **Step 5: 타입체크 + 기존 테스트 통과 확인**

Run: `cd apps/desktop && pnpm tsc --noEmit && pnpm vitest run src/console/`
Expected: tsc 0 errors, 기존 테스트 전부 PASS

- [ ] **Step 6: Commit**

```bash
git add apps/desktop/src/console/VideoReviewView.tsx
git commit -m "feat(video): 결과보기 영문 잔존 배지·시각표시·일괄 재번역 버튼"
```

---

## 실기 검증 (자동 테스트가 못 덮는 것)

- [ ] 서버 재동결(`apps/server` 변경 → `build-server.sh`) 후 프로즌 번들에서 `/api/v1/video-jobs/{id}/retranslate` 200
- [ ] 실제 영문 잔존이 있는 기존 작업(사용자 스크린샷의 그 작업)에서 버튼 → 한글로 바뀌는지
- [ ] 수동 편집한 줄이 재번역 후에도 보존되는지 (핵심 안전 속성)
- [ ] Claude 구독 CLI가 없는 서버에서 해당 옵션이 비활성으로 보이는지
