"""PDF 번역 파이프라인 러너 — extract → translate → overlay."""
from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from apps.server.db.models import PdfJob
from apps.server.db.session import AsyncSessionLocal

# 이중 기동 가드(포트 프로브)는 video 스윕의 단일 진실을 그대로 재사용한다 —
# uvicorn lifespan이 소켓 바인딩보다 먼저 도는 문제는 도메인과 무관하게 같은
# 증상이라, 별도 구현을 두면 한쪽만 고치는 사고가 난다.
from apps.server.domain.video_captions.maintenance import _another_instance_is_serving
from apps.server.domain.video_captions.translate import maybe_aclose_translator
from apps.server.domain.video_captions.translate_cli import create_translator

from .backend import open_pdf
from .pdf_store import pdf_job_dir
from .pdf_tasks import (
    _PDF_SEMAPHORE,
    _bump_generation,
    _current_generation,
    _set_progress,
    _set_status,
    _try_set_error,
)
from .profiles import detect_profile
from .translate_blocks import build_pdf_prompt, translate_texts

logger = logging.getLogger("yeson.pdf.pipeline")


class PdfTranslateError(RuntimeError):
    pass


def _is_usable_rect(rect: tuple[float, float, float, float],
                    page_size: tuple[float, float]) -> bool:
    """add_freetext에 넘기기 전 마지막 방어선(2026-07-30 리뷰 Finding 1b).

    profile.place()가 아무리 견고해도(스토리보드 프로파일은 이미
    비퇴화·온페이지를 보장하지만, 다른/미래 프로파일까지 같은 보장을
    한다는 계약은 없다) rect의 폭·높이가 0 이하이거나 완전히 페이지
    밖이면 PyMuPDF add_freetext_annot이 'rect is infinite or empty'로
    터진다 — 그 예외가 _overlay_and_save 루프 중간에서 나면 이미 끝낸
    번역(최대 수백~천 블록)까지 통째로 날아간다."""
    x0, y0, x1, y1 = rect
    page_w, page_h = page_size
    if not (x1 > x0 and y1 > y0):
        return False
    return x1 > 0.0 and y1 > 0.0 and x0 < page_w and y0 < page_h


def _with_doc_lock(lock: threading.Lock, fn, *args):
    """doc을 만지는 모든 워커 스레드 진입점의 공통 게이트.

    asyncio.to_thread는 바깥 await이 취소돼도 스레드 자체를 멈추지 않는다 —
    고아 스레드가 계속 PyMuPDF 문서를 조작할 수 있다(스레드-안전하지 않음,
    최악=프로세스 크래시). doc을 만지는 모든 to_thread 바디와 최종 close()가
    이 락을 공유해야, 취소 시 close()가 고아 스레드보다 먼저(또는 동시에)
    doc을 건드리는 경쟁을 막을 수 있다.
    """
    with lock:
        return fn(*args)


async def run_pdf_job(external_id: UUID) -> None:
    await _PDF_SEMAPHORE.acquire()
    generation = _bump_generation(external_id)
    doc = None
    doc_lock = threading.Lock()
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
        # detect_profile은 최대 3페이지 get_text("dict")를 훑는다(GIL 바운드) —
        # 이벤트 루프에서 직접 돌리면 실시간 자막 WebSocket이 수십ms 멎는다.
        profile = await asyncio.to_thread(_with_doc_lock, doc_lock, detect_profile, doc)
        if profile is None:
            raise PdfTranslateError(
                "지원하지 않는 PDF 포맷입니다 (현재 지원: 스토리보드형)")
        blocks = await asyncio.to_thread(_with_doc_lock, doc_lock, profile.extract, doc)
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

        # 한글 블록은 추출 단계(has_hangul)에서 이미 걸러지므로, 정상적으로
        # 도는 실행이라면 유효 번역이 0일 수 없다. 0이면 번역 엔진이 전량
        # 실패해 원문을 그대로 복사한 것이다(예: 구독 CLI가 로그아웃 상태 —
        # list_translate_engines의 available은 resolve_cli()만 확인해 실제
        # 로그인 여부는 못 잡는다). 이 경우를 그냥 done으로 넘기면 사용자가
        # "원본과 바이트만 다를 뿐 내용은 똑같은 번역본"을 조용히 받는다.
        kept_as_source = sum(
            1 for block, ko in zip(blocks, ko_texts)
            if not (ko.strip() and ko.strip() != block.text.strip())
        )
        effective = len(blocks) - kept_as_source
        if kept_as_source > 0:
            # 부분 실패(청크 병렬화로 CLI 콜 수가 늘어난 뒤 특히 조용히
            # 묻히기 쉽다) — 몇 블록이 원문 그대로 남았는지 남겨야 다음
            # 실기 런에서 CLI 오류를 추적할 수 있다(2026-07-30 리뷰 Finding 3a).
            logger.warning(
                "pdf-translate: %d/%d blocks kept as source (번역 실패 폴백)",
                kept_as_source, len(blocks))
        if effective == 0:
            raise PdfTranslateError(
                "모든 블록 번역에 실패했습니다 — 번역 엔진 상태를 확인하세요")

        await _set_status(external_id, "overlaying")

        def _overlay_and_save() -> Path | None:
            for block, ko in zip(blocks, ko_texts):
                ko = ko.strip()
                # 번역 실패 폴백(원문 복사)·빈 결과는 주석을 달지 않는다
                if not ko or ko == block.text.strip():
                    continue
                ov = profile.place(block, ko, doc.page_size(block.page))
                if not _is_usable_rect(ov.rect, doc.page_size(block.page)):
                    # 방어선(2026-07-30 리뷰 Finding 1b) — place()가 아무리
                    # 견고해도, 퇴화(폭·높이 0 이하)하거나 페이지 밖으로 나간
                    # rect를 add_freetext에 넘기면 PyMuPDF가
                    # 'rect is infinite or empty'로 터진다. 그 한 블록
                    # 때문에 이미 끝낸 번역(최대 수백~천 블록)까지 통째로
                    # 잃을 수는 없으니, 이 블록만 건너뛰고 경고를 남긴다.
                    logger.warning(
                        "pdf-translate: page %d %s block의 rect가 유효하지 "
                        "않아 주석을 건너뜀 %r", block.page, block.kind, ov.rect)
                    continue
                doc.add_freetext(ov.page, ov.rect, ov.text, fontsize=ov.fontsize)
            if generation != _current_generation(external_id):
                # 취소/삭제가 오버레이 배치 도중 도착했다 — 여기서 저장을
                # 건너뛰지 않으면 doc.save()의 mkdir(parents=True)이 DELETE가
                # 방금 지운 작업 폴더를 되살리고, DB 행 없는 고아
                # translated.pdf를 남긴다(pdf_jobs엔 프루닝 스윕도 없다).
                return None
            dest = pdf_job_dir(external_id) / "translated.pdf"
            doc.save(dest)
            return dest

        dest = await asyncio.to_thread(_with_doc_lock, doc_lock, _overlay_and_save)
        if dest is None:
            raise asyncio.CancelledError
        await _set_status(external_id, "done", translated_path=str(dest))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("pdf job %s failed", external_id)
        if generation == _current_generation(external_id):
            await _try_set_error(external_id, str(exc))
    finally:
        # 세마포어 반납은 무조건 실행돼야 한다 — close 대기 중 두 번째 취소가
        # 도착하면(cancel_pdf_task는 task.done()이 아닌 한 언제든 재취소 가능,
        # 셧다운/그룹 취소·루프 종료 시 executor 거부도 마찬가지) 안쪽 await이
        # CancelledError/RuntimeError를 던지고, 그걸 못 잡으면 release() 줄이
        # 통째로 건너뛰어져 모듈 전역 _PDF_SEMAPHORE(값 1)가 영구 고갈된다
        # (재시작 전까진 이후 모든 작업이 조용히 멈춤).
        try:
            if doc is not None:
                # 락을 쥔 채(고아 스레드가 doc을 다 쓸 때까지) 닫는다. 대기 자체도
                # 워커 스레드에서 해야 이벤트 루프(실시간 자막 WebSocket)를 막지
                # 않는다. asyncio.shield: close 도중 두 번째 취소가 와도 close
                # 작업 자체는 백그라운드에서 끝까지 돌게 한다(중간에 버려지면
                # 파일 핸들이 안 닫힌 채 새는 fitz 문서가 된다) — shield는 취소를
                # 호출자(여기)에게는 그대로 전달하므로 바로 아래 except가 받는다.
                await asyncio.shield(
                    asyncio.to_thread(_with_doc_lock, doc_lock, doc.close))
        except BaseException:  # 무엇이 오든(재취소·RuntimeError 등) release는 반드시 실행
            logger.exception("pdf job %s: doc close failed (during cleanup)",
                             external_id)
        finally:
            _PDF_SEMAPHORE.release()


async def fail_inflight_pdf_jobs_at_startup() -> None:
    """재시작 시 in-flight 작업을 error로 — 좀비 'translating' 행 방지.

    uvicorn은 lifespan startup을 소켓 바인딩보다 먼저 실행한다 — 이중 기동된
    두 번째 프로세스가 살아있는 인스턴스의 진행 중 PDF 작업을 오판해 쓸어버릴
    수 있어(video 스윕과 동일한 위험), 같은 포트-프로브 가드를 그대로 쓴다.
    """
    if _another_instance_is_serving():
        logger.warning(
            "startup pdf-job sweep skipped: another instance is already serving")
        return
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(PdfJob).where(PdfJob.status.in_(
            ("queued", "extracting", "translating", "overlaying"))))
        ).scalars().all()
        for job in rows:
            job.status = "error"
            job.error = "서버 재시작으로 중단됨 — 다시 업로드하세요"
        if rows:
            await db.commit()
