"""PDF 번역 파이프라인 러너 — extract → translate → overlay."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from apps.server.db.models import PdfJob
from apps.server.db.session import AsyncSessionLocal
from apps.server.domain.video_captions.translate import maybe_aclose_translator
from apps.server.domain.video_captions.translate_cli import create_translator

from .backend import open_pdf
from .pdf_store import pdf_job_dir
from .pdf_tasks import (_PDF_SEMAPHORE, _bump_generation, _current_generation,
                        _set_progress, _set_status, _try_set_error)
from .profiles import detect_profile
from .translate_blocks import build_pdf_prompt, translate_texts

logger = logging.getLogger("yeson.pdf.pipeline")


class PdfTranslateError(RuntimeError):
    pass


async def run_pdf_job(external_id: UUID) -> None:
    await _PDF_SEMAPHORE.acquire()
    generation = _bump_generation(external_id)
    doc = None
    try:
        async with AsyncSessionLocal() as db:
            job = (await db.execute(
                select(PdfJob).where(PdfJob.external_id == external_id)
            )).scalar_one()
            source_path = job.source_path
            provider = job.translate_provider
            cli_model = job.translate_cli_model
        if not source_path or not Path(source_path).exists():
            raise PdfTranslateError("원본 PDF 파일이 없습니다")

        await _set_status(external_id, "extracting")
        doc = await asyncio.to_thread(open_pdf, Path(source_path))
        profile = detect_profile(doc)
        if profile is None:
            raise PdfTranslateError(
                "지원하지 않는 PDF 포맷입니다 (현재 지원: 스토리보드형)")
        blocks = await asyncio.to_thread(profile.extract, doc)
        if not blocks:
            raise PdfTranslateError("번역할 텍스트 블록을 찾지 못했습니다")
        await _set_status(external_id, "translating", format=profile.name,
                          page_count=doc.page_count, block_count=len(blocks))

        async def on_progress(frac: float) -> None:
            # 세대가 바뀌었으면(취소) 다음 청크로 가기 전에 중단한다
            if generation != _current_generation(external_id):
                raise asyncio.CancelledError
            await _set_progress(external_id, int(frac * 100), generation)

        translator = create_translator(provider, cli_model,
                                       prompt_builder=build_pdf_prompt)
        try:
            ko_texts = await translate_texts([b.text for b in blocks], translator,
                                             progress_cb=on_progress)
        finally:
            await maybe_aclose_translator(translator)

        await _set_status(external_id, "overlaying")

        def _overlay_and_save() -> Path:
            for block, ko in zip(blocks, ko_texts):
                ko = ko.strip()
                # 번역 실패 폴백(원문 복사)·빈 결과는 주석을 달지 않는다
                if not ko or ko == block.text.strip():
                    continue
                ov = profile.place(block, ko, doc.page_size(block.page))
                doc.add_freetext(ov.page, ov.rect, ov.text, fontsize=ov.fontsize)
            dest = pdf_job_dir(external_id) / "translated.pdf"
            doc.save(dest)
            return dest

        dest = await asyncio.to_thread(_overlay_and_save)
        await _set_status(external_id, "done", translated_path=str(dest))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — 최종 방어선
        logger.exception("pdf job %s failed", external_id)
        if generation == _current_generation(external_id):
            await _try_set_error(external_id, str(exc))
    finally:
        if doc is not None:
            doc.close()
        _PDF_SEMAPHORE.release()


async def fail_inflight_pdf_jobs_at_startup() -> None:
    """재시작 시 in-flight 작업을 error로 — 좀비 'translating' 행 방지."""
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(PdfJob).where(PdfJob.status.in_(
            ("queued", "extracting", "translating", "overlaying"))))
        ).scalars().all()
        for job in rows:
            job.status = "error"
            job.error = "서버 재시작으로 중단됨 — 다시 업로드하세요"
        if rows:
            await db.commit()
