"""PDF 번역 파이프라인 러너 — extract → translate → overlay."""
from __future__ import annotations

import asyncio
import logging
import shutil
import threading
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, select

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
from .utterances import group_utterances

logger = logging.getLogger("yeson.pdf.pipeline")

# 진행 중 상태 — 프루닝이 절대 건드리면 안 되는 집합이자, 재시작 스윕이
# error로 정리하는 집합이다. 두 곳이 같은 상수를 봐야 한쪽만 갱신하는
# 사고가 안 난다(video의 maintenance._INFLIGHT_STATUSES와 같은 취지).
_INFLIGHT_STATUSES = ("queued", "extracting", "translating", "overlaying")

# PDF 작업이 무한정 쌓이지 않도록 유지할 최근 작업 수 (개수 상한 정책).
# 영상 자막(maintenance.RETENTION_KEEP=30)보다 작게 잡는다 — 이 기능의 기준
# 문서 실측이 원본 129MB + 번역본 ~180MB로 **작업 1건당 약 300MB**라, 같은
# 30을 쓰면 상한이 9GB가 된다. 자가호스팅 데스크톱 앱이라 이 디스크는
# 사용자의 개인 디스크다(2026-07-30 전브랜치 리뷰 I-2).
RETENTION_KEEP = 10


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

        # 발화 단위 병합(Task 17): 한 발화가 여러 페이지에 걸치면((CONT.)
        # 조각) 조각째 번역할 경우 어순이 붕괴하고 헤더뿐인 조각은 "화자
        # (계속)"만 남는다 — 사람 번역본 관례(발화 전문을 걸친 모든
        # 페이지에 동일하게 반복 기재)를 재현하려면 번역은 그룹(발화)
        # 단위로 하고, 결과를 멤버 블록 전체에 팬아웃해야 한다.
        groups, group_texts = group_utterances(blocks)

        async def on_progress(frac: float) -> None:
            # 세대가 바뀌었으면(취소) 다음 청크로 가기 전에 중단한다
            if generation != _current_generation(external_id):
                raise asyncio.CancelledError
            await _set_progress(external_id, int(frac * 100), generation)

        translator = create_translator(provider, cli_model,
                                       prompt_builder=build_pdf_prompt)
        try:
            ko_group_texts = await translate_texts(group_texts, translator,
                                                    progress_cb=on_progress)
        finally:
            await maybe_aclose_translator(translator)

        # 한글 블록은 추출 단계(has_hangul)에서 이미 걸러지므로, 정상적으로
        # 도는 실행이라면 유효 번역이 0일 수 없다. 0이면 번역 엔진이 전량
        # 실패해 원문을 그대로 복사한 것이다(예: 구독 CLI가 로그아웃 상태 —
        # list_translate_engines의 available은 resolve_cli()만 확인해 실제
        # 로그인 여부는 못 잡는다). 이 경우를 그냥 done으로 넘기면 사용자가
        # "원본과 바이트만 다를 뿐 내용은 똑같은 번역본"을 조용히 받는다.
        #
        # 판정은 반드시 그룹 단위(group.merged_text 대조)여야 한다 — 조각
        # 블록 자신의 텍스트와 비교하면 (CONT.)-헤더만 있던 조각은 항상
        # "달라졌다"고 오판되어, 번역 실패 시에도 전문 영어가 그 조각의
        # 주석으로 그대로 새 나간다(Task 17).
        ko_by_block: list[str | None] = [None] * len(blocks)
        kept_as_source = 0
        for group, ko in zip(groups, ko_group_texts):
            ko_stripped = ko.strip()
            if ko_stripped and ko_stripped != group.merged_text.strip():
                for idx in group.member_indices:
                    ko_by_block[idx] = ko_stripped
            else:
                kept_as_source += 1
        # 판넬 약어는 번역기 결과 대신 **결정적 해독값**을 쓴다.
        #
        # `SPCZMB`·`TTINCA`·`IN` 같은 제작 코드는 LLM이 옮길 게 없어 원문을
        # 그대로 돌려주고, 그러면 위 루프가 "번역 실패"로 보아 주석을 아예
        # 만들지 않는다 — 재실행 실측에서 판넬 라벨이 되살아난 7페이지가 전부
        # `아웃`(OUT 음역) 하나뿐이고 나머지 27페이지가 비어 있던 이유다.
        # 해독된 블록은 실패 집계에서도 빼야 한다(번역을 안 한 게 아니라
        # 번역이 필요 없는 블록이다).
        predecoded = 0
        for i, block in enumerate(blocks):
            if block.ko:
                if ko_by_block[i] is None:
                    predecoded += 1
                ko_by_block[i] = block.ko
        effective = len(groups) - kept_as_source + predecoded
        if kept_as_source > 0:
            # 부분 실패(청크 병렬화로 CLI 콜 수가 늘어난 뒤 특히 조용히
            # 묻히기 쉽다) — 몇 그룹이 원문 그대로 남았는지 남겨야 다음
            # 실기 런에서 CLI 오류를 추적할 수 있다(2026-07-30 리뷰 Finding 3a).
            logger.warning(
                "pdf-translate: %d/%d groups kept as source (번역 실패 폴백)",
                kept_as_source, len(groups))
        if effective == 0:
            raise PdfTranslateError(
                "모든 블록 번역에 실패했습니다 — 번역 엔진 상태를 확인하세요")

        await _set_status(external_id, "overlaying")

        def _overlay_and_save() -> Path | None:
            for i, block in enumerate(blocks):
                ko = ko_by_block[i]
                # 번역 실패 폴백(원문 복사)·빈 결과는 주석을 달지 않는다
                if ko is None:
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
        rows = (await db.execute(
            select(PdfJob).where(PdfJob.status.in_(_INFLIGHT_STATUSES)))
        ).scalars().all()
        for job in rows:
            job.status = "error"
            job.error = "서버 재시작으로 중단됨 — 다시 업로드하세요"
        if rows:
            await db.commit()


async def _prune_pre_delete_hook(candidate_ids: list[int]) -> None:
    """프루닝의 SELECT와 DELETE 사이 지점 (기본 no-op). 테스트가 여기서 상태
    전이(done→translating 재실행)를 주입해 DELETE 시점의 상태 재확인 가드를
    검증한다 — video maintenance의 동명 훅과 같은 역할."""


async def prune_old_pdf_jobs(keep: int = RETENTION_KEEP) -> int:
    """가장 최근 ``keep``개만 남기고 오래된 PDF 작업을 삭제한다 (작업 폴더 + DB 행).

    PDF 작업은 source.pdf와 translated.pdf를 작업 폴더에 쌓으므로 정리하지
    않으면 무한정 누적된다(작업 1건 ≈ 300MB 실측). 서버 시작 시와 새 작업
    생성 직후 호출해 개수를 상한으로 유지한다 — video 잡 리텐션과 동일한
    형태·동일한 규칙이다.

    진행 중(in-flight) 작업은 아무리 오래돼도 절대 지우지 않는다. 방금 만든
    작업도 두 겹으로 안전하다 — 정렬상 가장 최신이라 ``rows[keep:]``에 들지
    않고, 상태가 queued(in-flight)라 후보에서도 빠진다.
    """
    try:
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(PdfJob.id, PdfJob.status).order_by(
                    PdfJob.created_at.desc(), PdfJob.id.desc())
            )).all()
            candidate_ids = [r.id for r in rows[keep:]
                             if r.status not in _INFLIGHT_STATUSES]
            if not candidate_ids:
                return 0
            await _prune_pre_delete_hook(candidate_ids)
            # 삭제 시점에 상태를 원자적으로 재확인한다. SELECT와 DELETE 사이에
            # in-flight로 전이한 작업(같은 문서를 다시 돌리기 시작한 경우)은
            # 지우지 않는다 — 그 폴더/행을 지우면 실행 중인 run_pdf_job이
            # 깨진다. Core 벌크 삭제라 동시 프루닝 두 개가 겹쳐도
            # StaleDataError가 나지 않고, 실제로 삭제된 행만 RETURNING으로
            # 받아 그 폴더만 정리한다.
            deleted = (await db.execute(
                delete(PdfJob)
                .where(PdfJob.id.in_(candidate_ids),
                       PdfJob.status.not_in(_INFLIGHT_STATUSES))
                .returning(PdfJob.external_id)
            )).all()
            await db.commit()
        for row in deleted:
            shutil.rmtree(pdf_job_dir(row.external_id), ignore_errors=True)
        if deleted:
            logger.info("retention: pruned %d old pdf job(s) (keep=%d)",
                        len(deleted), keep)
        return len(deleted)
    except Exception:  # fire-and-forget 태스크로도 호출되므로 삼키고 로그만
        logger.exception("pdf-job retention prune failed")
        return 0


async def prune_old_pdf_jobs_at_startup() -> int:
    """서버 시작 시 리텐션 프루닝 — in-flight 스윕과 동일한 '다른 인스턴스가
    서빙 중' 가드로 보호한다.

    이중 기동된 비소유 프로세스(uvicorn lifespan이 포트 바인딩보다 먼저 도는)가
    살아있는 인스턴스의 작업 폴더/DB 행을 지운 뒤 'address already in use'로
    죽는 것을 막는다. 런타임 작업 생성 시 호출되는 prune_old_pdf_jobs()는
    자기 자신이 이미 포트를 점유하고 있어 이 가드를 쓰면 항상 스킵되므로,
    가드는 startup 경로에만 둔다.
    """
    if _another_instance_is_serving():
        logger.warning(
            "startup pdf retention prune skipped: another instance is already serving")
        return 0
    return await prune_old_pdf_jobs()
