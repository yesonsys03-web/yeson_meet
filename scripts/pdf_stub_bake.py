#!/usr/bin/env python3
"""S0 측정 하네스 — 스텁 번역기로 `run_pdf_job`을 실물 문서에 돌려 단계별 벽시계를 잰다.

**개발 전용**이다. 배포 번들(`build-server.sh`)에 들어가지 않으며 서버 코드가
이 모듈을 import하지 않는다. `scripts/baseline_collect.py`와 같은 자리의 동류다.

왜 필요한가 — AC13(실용 속도)의 재굽기 상한이 "최초 `overlaying` 단계 × 1.2"인데,
이 기능 자체가 최초 `overlaying`에 `build_plan`(판넬 열거 + 직렬화)을 더한다.
기준선을 이 기능이 들어간 뒤에 재면 **자기참조**가 되어 회귀를 영원히 못 잡는다.
그래서 착수 **전에**(S0-a) 현행 main의 수치를 남기고, S1 직후(S0-b) 신규 작업을
**분리 계측**해 `기준 overlaying = 실측 − 신규작업`으로 도출한다.

왜 LLM을 안 쓰는가 — 1037페이지 전량 번역은 실행마다 비용이 들고 재현이 비싸다.
`run_pdf_job`은 `create_translator`를 **모듈 전역으로** 호출하므로(`pdf_run.py:129`)
테스트가 이미 쓰는 심(`test_pdf_run.py:38`)을 그대로 재사용해 번역기만 갈아끼운다.
LLM 호출 0회, 반복 재현이 공짜다.

`annots()`에서 계획을 역생성하는 대안은 `kind`·`source_text`·`panel_index`가
복원 불가라 가짜 계획이 되고 ADR-1(계획 파일의 단일 저자)을 깨므로 쓰지 않는다.

사용:
    .venv/bin/python scripts/pdf_stub_bake.py --phase a
    .venv/bin/python scripts/pdf_stub_bake.py --phase a --pdf <경로> --pages 50
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_PDF = Path.home() / "Downloads" / "1601_콘티번역" / "GABE01_A1_FinalShipped.pdf"
DEFAULT_WORKDIR = REPO_ROOT / ".omc" / "artifacts" / "s0-stub-bake"

# 파이프라인이 지나가는 상태 순서. 인접 두 상태의 시각 차이가 그 단계의 벽시계다.
_STAGES = ("extracting", "translating", "overlaying", "done")


def _bootstrap_env(workdir: Path) -> None:
    """서버 모듈을 import하기 **전에** 격리된 DB·저장소를 가리키게 한다.

    `apps.server.db.session`은 import 시점에 `DATABASE_URL`을 읽어 엔진을
    만든다(`session.py:11-18`) — 그래서 이 함수는 반드시 모든 서버 import보다
    먼저 돌아야 하고, 이 스크립트가 지연 import를 쓰는 이유가 그것이다.
    운영 Postgres나 실제 사용자 저장소를 건드리지 않는다.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    os.environ["STORAGE_ROOT"] = str(workdir / "storage")
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{workdir / 'stub.db'}"
    # 번역 프롬프트가 글로서리를 읽어 들이는 경로는 측정 대상이 아니다.
    os.environ.setdefault("GEMINI_GLOSSARY_ENABLED", "0")
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))


class _StubTranslator:
    """LLM을 대신하는 결정적 한글 스텁.

    ⚠ 계획 C-m3의 단서: 고정 문자열을 쓰면 실제 번역과 **길이**가 달라
    `place()`가 고르는 폰트 크기와 주석 rect가 달라지고, 그러면 `overlaying`
    기준선이 실제와 벌어진다. 그래서 고정 문자열이 아니라 **원문 길이에
    비례하는** 한글을 만든다 — 여전히 결정적이고 LLM 0회이면서, 배치 로직이
    실제와 비슷한 크기의 텍스트를 다루게 된다.

    비율 0.6은 영문→한국어에서 문자 수가 대체로 줄어드는 경향을 근사한 값이며,
    정확한 재현이 아니라 **기준선의 왜곡을 줄이는 것**이 목적이다. 그래도
    S0-a 수치는 근사 기준선이므로 절대 상한 판정에는 여유를 두고 해석한다.
    """

    _FILLER = "가나다라마바사아자차카타파하"

    async def translate_batch(self, texts: list[str]) -> list[str]:
        out: list[str] = []
        for t in texts:
            n = max(1, int(len(t) * 0.6))
            reps = n // len(self._FILLER) + 1
            out.append((self._FILLER * reps)[:n])
        return out


def _truncate_pdf(src: Path, dest: Path, pages: int) -> None:
    """앞 N페이지만 잘라낸 사본 — 스크립트 자체를 빠르게 점검할 때만 쓴다."""
    import fitz

    doc = fitz.open(src)
    try:
        out = fitz.open()
        out.insert_pdf(doc, from_page=0, to_page=min(pages, doc.page_count) - 1)
        dest.parent.mkdir(parents=True, exist_ok=True)
        out.save(dest)
        out.close()
    finally:
        doc.close()


async def _run(pdf_path: Path, workdir: Path, phase: str) -> dict:
    from sqlalchemy import select

    from apps.server.db.models import AppUser, PdfJob
    from apps.server.db.seed import create_schema
    from apps.server.db.session import AsyncSessionLocal
    from apps.server.domain.pdf_translate import backend_mupdf, pdf_run
    from apps.server.domain.pdf_translate.pdf_store import pdf_job_dir

    await create_schema()

    # ── 운영자 행 확보: PdfJob.owner_user_id가 NOT NULL FK다(models.py:241-243).
    async with AsyncSessionLocal() as db:
        owner_id = (await db.execute(
            select(AppUser.id).order_by(AppUser.id).limit(1))).scalar_one_or_none()
        if owner_id is None:
            user = AppUser(email="s0@stub.local", name="S0 Harness",
                           password_hash="!", role="admin", is_active=True)
            db.add(user)
            await db.commit()
            await db.refresh(user)
            owner_id = user.id

    # ── 잡 생성. 181MB 원본을 매 실행 복사하지 않으려고 심볼릭 링크를 건다
    #    (fitz는 링크를 따라간다). 실패하면 복사로 내려간다.
    from uuid import uuid4

    external_id = uuid4()
    job_dir = pdf_job_dir(external_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    src = job_dir / "source.pdf"
    try:
        src.symlink_to(pdf_path)
    except OSError:
        import shutil

        shutil.copy2(pdf_path, src)

    async with AsyncSessionLocal() as db:
        db.add(PdfJob(external_id=external_id, owner_user_id=owner_id,
                      title=pdf_path.name, source_ref=pdf_path.name,
                      status="queued", source_path=str(src)))
        await db.commit()

    # ── 계측 훅 ──────────────────────────────────────────────────────────────
    stage_at: dict[str, float] = {}
    orig_set_status = pdf_run._set_status
    # 파이프라인은 최종 상태를 세대 가드가 달린 래퍼로 쓴다 — 둘 다 감싸지
    # 않으면 `done` 시각이 안 잡혀 마지막 단계의 벽시계가 null이 된다.
    orig_set_status_if_current = pdf_run._set_status_if_current

    async def timed_set_status(eid, status, **kw):
        stage_at.setdefault(status, time.perf_counter())
        return await orig_set_status(eid, status, **kw)

    async def timed_set_status_if_current(eid, generation, status, **kw):
        stage_at.setdefault(status, time.perf_counter())
        return await orig_set_status_if_current(eid, generation, status, **kw)

    save_seconds: list[float] = []
    orig_save = backend_mupdf.MuPdfDocument.save

    def timed_save(self, dest):
        t = time.perf_counter()
        try:
            return orig_save(self, dest)
        finally:
            save_seconds.append(time.perf_counter() - t)

    # ── phase b: 이 기능이 오버레이 단계에 **새로 더한** 작업만 따로 잰다.
    #
    # AC13의 재굽기 상한이 "기준 overlaying × 1.2"인데, 기준을 이 기능이 들어간
    # 뒤에 재면 자기참조가 된다. 그래서 신규 작업(panels 호출 + 계획 직렬화)을
    # 분리 계측해 `기준 = 실측 − 신규작업`으로 되돌린다.
    panels_by_page: dict[int, float] = {}
    serialize_seconds: list[float] = []
    if phase == "b":
        from apps.server.domain.pdf_translate import overlay_plan
        from apps.server.domain.pdf_translate.profiles.storyboard import (
            StoryboardProfile,
        )

        orig_layout = StoryboardProfile.panel_layout

        def timed_layout(self, doc_, page):
            t = time.perf_counter()
            try:
                return orig_layout(self, doc_, page)
            finally:
                panels_by_page[page] = (panels_by_page.get(page, 0.0)
                                        + time.perf_counter() - t)

        orig_save_plan = overlay_plan.save_plan

        def timed_save_plan(job_dir_, plan_):
            t = time.perf_counter()
            try:
                return orig_save_plan(job_dir_, plan_)
            finally:
                serialize_seconds.append(time.perf_counter() - t)

        StoryboardProfile.panel_layout = timed_layout       # type: ignore[assignment]
        overlay_plan.save_plan = timed_save_plan            # type: ignore[assignment]
        pdf_run.save_plan = timed_save_plan                 # type: ignore[assignment]

    pdf_run._set_status = timed_set_status                      # type: ignore[assignment]
    pdf_run._set_status_if_current = timed_set_status_if_current  # type: ignore[assignment]
    pdf_run.create_translator = (                               # type: ignore[assignment]
        lambda provider, cli_model, prompt_builder: _StubTranslator())
    backend_mupdf.MuPdfDocument.save = timed_save               # type: ignore[assignment]

    started = time.perf_counter()
    try:
        await pdf_run.run_pdf_job(external_id)
    finally:
        pdf_run._set_status = orig_set_status                   # type: ignore[assignment]
        pdf_run._set_status_if_current = orig_set_status_if_current  # type: ignore[assignment]
        backend_mupdf.MuPdfDocument.save = orig_save            # type: ignore[assignment]
        if phase == "b":
            StoryboardProfile.panel_layout = orig_layout        # type: ignore[assignment]
            overlay_plan.save_plan = orig_save_plan             # type: ignore[assignment]
            pdf_run.save_plan = orig_save_plan                  # type: ignore[assignment]
    total = time.perf_counter() - started

    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(PdfJob).where(PdfJob.external_id == external_id))).scalar_one()
        status, error = row.status, row.error
        page_count, block_count = row.page_count, row.block_count
        translated_path = row.translated_path

    # 인접 상태 시각 차이 = 그 단계의 벽시계. 마지막 단계는 total로 닫는다.
    stages: dict[str, float | None] = {}
    for i, name in enumerate(_STAGES[:-1]):
        t0 = stage_at.get(name)
        t1 = stage_at.get(_STAGES[i + 1])
        stages[name] = round(t1 - t0, 3) if (t0 and t1) else None

    out_size = (Path(translated_path).stat().st_size
                if translated_path and Path(translated_path).exists() else None)
    plan_file = job_dir / "overlay_plan.json"

    # ── phase b 파생값: 신규 작업을 빼서 기준 overlaying을 되돌린다.
    extra: dict = {}
    if phase == "b":
        from apps.server.domain.pdf_translate.backend import open_pdf

        probe = open_pdf(pdf_path)
        try:
            broken = {p for p in range(probe.page_count) if probe.corrupt_words(p)}
        finally:
            probe.close()
        panels_total = sum(panels_by_page.values())
        # 깨진 페이지에서 `panels()`가 쓴 시간 = `extract`가 이미 낸 300dpi
        # 렌더+단어 OCR을 **재지불**한 몫(상한). 이 값이 크면 폴백이 필요하다:
        # ⓐ extract가 계산한 판넬을 부산물로 넘겨 재사용 ⓑ panel_index를
        # 목록 라우트에서 지연 산출(표시 전용이라 배치에 영향 없음).
        dup = sum(t for p, t in panels_by_page.items() if p in broken)
        serialize = sum(serialize_seconds)
        overlaying = stages.get("overlaying")
        extra = {
            "panels_calls": len(panels_by_page),
            "panels_seconds_total": round(panels_total, 3),
            "broken_pages": len(broken),
            "repair_duplication_seconds": round(dup, 3),
            "serialize_seconds": round(serialize, 3),
            "baseline_overlaying_seconds": (
                None if overlaying is None
                else round(overlaying - panels_total - serialize, 3)),
        }

    return {
        "phase": phase,
        "pdf": str(pdf_path),
        "job_id": str(external_id),
        "job_dir": str(job_dir),
        "status": status,
        "error": error,
        "page_count": page_count,
        "block_count": block_count,
        "stage_seconds": stages,
        "doc_save_seconds": [round(s, 3) for s in save_seconds],
        "total_seconds": round(total, 3),
        "source_bytes": pdf_path.stat().st_size,
        "translated_bytes": out_size,
        # S1 이후에만 존재한다 — S0-a에서는 null이 정상이며, 그 사실 자체가
        # "기준선은 계획 파일이 생기기 전에 쟀다"는 증거다.
        "overlay_plan_bytes": (plan_file.stat().st_size
                               if plan_file.exists() else None),
        **extra,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="S0 측정 하네스 (스텁 번역기, LLM 0회)")
    ap.add_argument("--phase", choices=("a", "b"), default="a",
                    help="a=착수 전 기준선(현행 main) / b=S1 직후 분리 계측")
    ap.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    ap.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    ap.add_argument("--pages", type=int, default=0,
                    help="앞 N페이지만 사용(스크립트 점검용). 0=전체")
    ap.add_argument("--out", type=Path, default=None,
                    help="결과 JSON 경로 (기본: <workdir>/s0-<phase>.json)")
    args = ap.parse_args()

    if not args.pdf.exists():
        print(f"원본 PDF가 없습니다: {args.pdf}", file=sys.stderr)
        return 2

    _bootstrap_env(args.workdir)

    pdf_path = args.pdf
    if args.pages:
        pdf_path = args.workdir / f"trunc_{args.pages}p.pdf"
        if not pdf_path.exists():
            _truncate_pdf(args.pdf, pdf_path, args.pages)

    import asyncio

    result = asyncio.run(_run(pdf_path, args.workdir, args.phase))

    out = args.out or (args.workdir / f"s0-{args.phase}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n→ {out}", file=sys.stderr)
    return 0 if result["status"] == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
