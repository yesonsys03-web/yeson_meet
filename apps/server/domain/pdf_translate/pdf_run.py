"""PDF 번역 파이프라인 러너 — extract → translate → overlay."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import threading
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from functools import partial
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
from .overlay_plan import (
    apply_composed,
    build_plan,
    compose,
    edits_path,
    invalidate_baked_version,
    load_edits,
    load_plan,
    mark_baked,
    panels_resolver,
    save_plan,
)
from .pdf_store import pdf_job_dir
from .pdf_tasks import (
    _PDF_SEMAPHORE,
    _bump_generation,
    _current_generation,
    _set_progress,
    _set_status,
    _set_status_if_current,
    _try_set_error,
    mark_rebaking,
)
from .profiles import detect_profile, profile_by_name
from .profiles.base import PdfBlock
from .translate_blocks import build_pdf_prompt, translate_texts
from .utterances import group_utterances

logger = logging.getLogger("yeson.pdf.pipeline")

# 진행 중 상태 — 프루닝이 절대 건드리면 안 되는 집합이자, 재시작 스윕이
# error로 정리하는 집합이다. 두 곳이 같은 상수를 봐야 한쪽만 갱신하는
# 사고가 안 난다(video의 maintenance._INFLIGHT_STATUSES와 같은 취지).
_INFLIGHT_STATUSES = (
    "queued", "extracting", "transcribing", "translating", "overlaying")

# PDF 작업이 무한정 쌓이지 않도록 유지할 최근 작업 수 (개수 상한 정책).
# 영상 자막(maintenance.RETENTION_KEEP=30)보다 작게 잡는다 — 이 기능의 기준
# 문서 실측이 원본 129MB + 번역본 ~180MB로 **작업 1건당 약 300MB**라, 같은
# 30을 쓰면 상한이 9GB가 된다. 자가호스팅 데스크톱 앱이라 이 디스크는
# 사용자의 개인 디스크다(2026-07-30 전브랜치 리뷰 I-2).
RETENTION_KEEP = 10

# 추출 결과 캐시(엑스시트 재번역 가속). 전 페이지 OCR은 문서당 10~17분인데
# 결정적이라 같은 원본·같은 추출 코드면 결과가 같다 — 재번역은 배치·용어·
# 번역만 다시 하려는 것이므로 이걸 매번 다시 읽을 이유가 없다. 전사 캐시
# (transcripts.json)와 같은 자리, 같은 취지다.
_BLOCKS_CACHE = "blocks_cache.json"


def _blocks_cache_key(profile, source: Path) -> str | None:
    """`(추출코드 지문, 원본 파일 신원)` — 프로파일이 지문을 줄 때만 캐시를 쓴다.

    `extract_cache_key`는 refine_ko·place_with_doc과 같은 **선택** 훅이다
    (Protocol 미등록). 원본 신원은 크기+mtime으로 본다 — 265MB PDF를 매
    실행 해시하는 비용을 피하고, 잡 폴더의 원본은 업로드 후 바뀌지 않는다.
    """
    hook = getattr(profile, "extract_cache_key", None)
    if hook is None:
        return None
    try:
        st = source.stat()
    except OSError:
        return None
    return f"{hook()}|{st.st_size}|{int(st.st_mtime)}"


def _load_cached_blocks(job_dir: Path, key: str) -> list | None:
    path = job_dir / _BLOCKS_CACHE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("key") != key:
            logger.info("pdf-translate: 추출 캐시 무효(지문 불일치) — 다시 추출")
            return None
        return [PdfBlock(page=b["page"], kind=b["kind"], text=b["text"],
                         bbox=tuple(b["bbox"]), limit_y=b["limit_y"],
                         limit_x1=b["limit_x1"], ko=b["ko"])
                for b in data["blocks"]]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning("pdf-translate: 추출 캐시를 읽지 못했습니다 (%s) — 다시 추출",
                       exc)
        return None


def _save_cached_blocks(job_dir: Path, key: str, blocks: list) -> None:
    payload = {"key": key, "blocks": [
        {"page": b.page, "kind": b.kind, "text": b.text, "bbox": list(b.bbox),
         "limit_y": b.limit_y, "limit_x1": b.limit_x1, "ko": b.ko}
        for b in blocks]}
    try:
        (job_dir / _BLOCKS_CACHE).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:  # 캐시는 편의 기능 — 실패해도 파이프라인은 간다
        logger.warning("pdf-translate: 추출 캐시 저장 실패 (%s)", exc)


class PdfTranslateError(RuntimeError):
    pass


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


@dataclass
class _JobSlot:
    """한 잡 실행이 점유하는 자원 — 세대·doc 락·열린 문서."""
    generation: int
    doc_lock: threading.Lock
    doc: object | None = None

    def run(self, fn, *args):
        """doc을 만지는 워커 스레드 진입점 — 코루틴에서 `await`해 쓴다."""
        return asyncio.to_thread(_with_doc_lock, self.doc_lock, fn, *args)


@asynccontextmanager
async def _pdf_job_slot(external_id: UUID):
    """잡 실행 슬롯 — 세마포어·세대·doc 수명을 **한곳에서** 관리한다.

    `run_pdf_job`과 `rebake_pdf_job`이 공유한다. 복제하면 이 파일에서 사고
    이력이 가장 두꺼운 구간(아래 세마포어 반납 방어)이 두 벌이 되어, 다음에
    한쪽만 고치는 사고가 난다.

    ⚠ `acquire`는 `try` **밖**이다. 이건 의도적이다 — acquire 대기 중 취소가
    오면 release를 하면 안 되는데, `try` 안으로 넣으면 값 1짜리 전역 세마포어가
    2가 되어 직렬화가 깨진다. 바로 아래 구간은 정반대 사고(영구 고갈)를
    기록하고 있어 **양방향 함정**이다.
    """
    await _PDF_SEMAPHORE.acquire()
    slot = _JobSlot(generation=_bump_generation(external_id),
                    doc_lock=threading.Lock())
    try:
        yield slot
    finally:
        # 세마포어 반납은 무조건 실행돼야 한다 — close 대기 중 두 번째 취소가
        # 도착하면(cancel_pdf_task는 task.done()이 아닌 한 언제든 재취소 가능,
        # 셧다운/그룹 취소·루프 종료 시 executor 거부도 마찬가지) 안쪽 await이
        # CancelledError/RuntimeError를 던지고, 그걸 못 잡으면 release() 줄이
        # 통째로 건너뛰어져 모듈 전역 _PDF_SEMAPHORE(값 1)가 영구 고갈된다
        # (재시작 전까진 이후 모든 작업이 조용히 멈춤).
        try:
            if slot.doc is not None:
                # 락을 쥔 채(고아 스레드가 doc을 다 쓸 때까지) 닫는다. 대기 자체도
                # 워커 스레드에서 해야 이벤트 루프(실시간 자막 WebSocket)를 막지
                # 않는다. asyncio.shield: close 도중 두 번째 취소가 와도 close
                # 작업 자체는 백그라운드에서 끝까지 돌게 한다(중간에 버려지면
                # 파일 핸들이 안 닫힌 채 새는 fitz 문서가 된다) — shield는 취소를
                # 호출자에게는 그대로 전달하므로 바깥 except가 받는다.
                await asyncio.shield(slot.run(slot.doc.close))
        except BaseException:  # 무엇이 오든(재취소·RuntimeError 등) release는 반드시 실행
            logger.exception("pdf job %s: doc close failed (during cleanup)",
                             external_id)
        finally:
            _PDF_SEMAPHORE.release()


async def _converge_after_failure(external_id: UUID, generation: int,
                                  message: str) -> None:
    """실패·취소로 끝났을 때 **쓸 만한 번역본이 남아 있으면** `done`으로 수렴.

    `error`/`cancelled`로 굳으면 네 가지가 한꺼번에 잠긴다: `/download`가 영구
    409(`status != "done"`), 편집·rebake·retranslate도 409, 시작 스윕은
    in-flight만 보므로 구제 불가, 게다가 그 상태는 in-flight가 아니라서 **다음
    업로드의 프루닝이 그 폴더를 rmtree 후보로 삼는다** — 사람이 넣은 라벨이
    그렇게 사라진다.

    첫 번역이 실패했을 때는 번역본 자체가 없으므로 평소대로 `error`가 된다.
    재번역·재굽기가 실패했을 때만 옛 번역본이 남아 있어 `done`으로 돌아온다 —
    한 규칙이 두 경우를 모두 옳게 처리한다.
    """
    translated = None
    try:
        async with AsyncSessionLocal() as db:
            job = (await db.execute(
                select(PdfJob).where(PdfJob.external_id == external_id)
            )).scalar_one_or_none()
            translated = job.translated_path if job is not None else None
    except Exception:
        logger.exception("pdf job %s: 상태 수렴 중 조회 실패", external_id)
    if (translated and Path(translated).exists()
            and await _set_status_if_current(external_id, generation, "done",
                                             error=message)):
        return
    if generation == _current_generation(external_id):
        await _try_set_error(external_id, message)


async def run_pdf_job(external_id: UUID) -> None:
    """번역 파이프라인 한 번 — 추출 → 번역 → 오버레이."""
    async with _pdf_job_slot(external_id) as slot:
        await _translate_and_overlay(external_id, slot)


async def _translate_group_texts(profile, blocks, groups, group_texts, *,
                                 provider, cli_model, progress_cb) -> list[str]:
    """그룹 번역 1차 + (프로파일이 켰으면) 에코 그룹 전용 프롬프트 재시도.

    에코(번역=원문)는 아래 호출부가 "번역 실패 폴백"으로 보아 주석을
    버린다. 이름·짧은 노트는 LLM이 확률적으로 에코해 증발했다(A2 실측
    16건, 2026-08-24). 재시도는 딱 한 번이고, 전 멤버 블록이 predecode
    (block.ko)된 그룹은 건너뛴다 — 결과가 어차피 사전값으로 덮인다."""
    # 줄 나누기 규칙은 프로파일이 정한다(엑스시트는 낱말 기둥을 막아야 한다).
    line_rule = getattr(profile, "prompt_line_rule", None)
    # 잡 시점에 계산되는 규칙(엑스시트: 이름표 운영자 파일)이 있으면 그것을 쓴다
    dynamic_rule = getattr(profile, "prompt_line_rule_now", None)
    if callable(dynamic_rule):
        line_rule = dynamic_rule()
    translator = create_translator(
        provider, cli_model,
        prompt_builder=partial(build_pdf_prompt, line_rule=line_rule))
    try:
        ko_group_texts = await translate_texts(group_texts, translator,
                                               progress_cb=progress_cb)
    finally:
        await maybe_aclose_translator(translator)
    if not getattr(profile, "retry_echoed_groups", False):
        return ko_group_texts
    echoed = [
        i for i, (g, ko) in enumerate(zip(groups, ko_group_texts))
        if not (ko.strip() and ko.strip() != g.merged_text.strip())
        and not all(blocks[idx].ko for idx in g.member_indices)
    ]
    if not echoed:
        return ko_group_texts
    from .translate_blocks import build_pdf_retry_prompt
    retry = create_translator(
        provider, cli_model,
        prompt_builder=partial(build_pdf_retry_prompt, line_rule=line_rule))
    try:
        ko_retry = await translate_texts([group_texts[i] for i in echoed],
                                         retry)
    finally:
        await maybe_aclose_translator(retry)
    recovered = 0
    for i, ko2 in zip(echoed, ko_retry):
        s = ko2.strip()
        if s and s != groups[i].merged_text.strip():
            ko_group_texts[i] = ko2
            recovered += 1
    logger.info("pdf-translate: 에코 그룹 %d개 재시도 → %d개 회수",
                len(echoed), recovered)
    return ko_group_texts


async def _translate_and_overlay(external_id: UUID, slot: _JobSlot) -> None:
    generation = slot.generation
    doc_lock = slot.doc_lock
    doc = None
    try:
        async with AsyncSessionLocal() as db:
            job = (await db.execute(
                select(PdfJob).where(PdfJob.external_id == external_id)
            )).scalar_one()
            source_path = job.source_path
            provider = job.translate_provider
            cli_model = job.translate_cli_model
            format_hint = job.format
        if not source_path or not Path(source_path).exists():
            raise PdfTranslateError("원본 PDF 파일이 없습니다")

        await _set_status(external_id, "extracting")
        doc = slot.doc = await asyncio.to_thread(open_pdf, Path(source_path))
        # detect_profile은 최대 3페이지 get_text("dict")를 훑는다(GIL 바운드) —
        # 이벤트 루프에서 직접 돌리면 실시간 자막 WebSocket이 수십ms 멎는다.
        #
        # 업로드가 format_hint로 포맷을 미리 지정했으면(탭별 업로드) 감지
        # 대신 그 프로파일을 쓰되, detect로 파일이 실제 그 포맷인지 한 번
        # 확인한다 — 스토리보드를 엑스시트 탭에 올리는 실수를 조용히
        # 엉뚱한 결과로 흘리지 않기 위해서다.
        profile = profile_by_name(format_hint) if format_hint else None
        if profile is not None:
            matched = await asyncio.to_thread(
                _with_doc_lock, doc_lock, profile.detect, doc)
            if not matched:
                raise PdfTranslateError(
                    f"선택한 포맷({profile.label})과 파일이 다릅니다")
        else:
            profile = await asyncio.to_thread(
                _with_doc_lock, doc_lock, detect_profile, doc)
        if profile is None:
            raise PdfTranslateError(
                "지원하지 않는 PDF 포맷입니다 (현재 지원: 스토리보드형·엑스시트)")
        # 추출 캐시 — 같은 원본·같은 추출 코드면 결과가 같다(_BLOCKS_CACHE 참조).
        cache_key = _blocks_cache_key(profile, Path(source_path))
        job_dir = pdf_job_dir(external_id)
        blocks = _load_cached_blocks(job_dir, cache_key) if cache_key else None
        if blocks:
            logger.info("pdf-translate: 추출 캐시 적중 — %d blocks (OCR 생략)",
                        len(blocks))
        else:
            blocks = await asyncio.to_thread(
                _with_doc_lock, doc_lock, profile.extract, doc)
            if blocks and cache_key:
                _save_cached_blocks(job_dir, cache_key, blocks)
        if not blocks:
            raise PdfTranslateError("번역할 텍스트 블록을 찾지 못했습니다")
        # 손글씨 포맷(xsheet)의 전사 훅 — 크롭 렌더(doc 락 필요·빠름)와
        # CLI 전사(락 불필요·문서당 수십 분)를 반드시 분리한다. 한 훅으로
        # 합치면 전사 내내 페이지 미리보기 라우트가 doc 락에 막힌다.
        if hasattr(profile, "transcribe_blocks"):
            await asyncio.to_thread(
                _with_doc_lock, doc_lock,
                profile.render_transcribe_crops, doc, blocks, job_dir)
            # 전사는 문서당 수십 분 — 전용 상태 + 배치 단위 진행률이 없으면
            # 사용자는 "추출 중"에 멈춘 화면만 본다. format을 여기서 미리
            # 기록해 탭별 목록 필터도 전사 중에 바로 선다.
            await _set_status(external_id, "transcribing",
                              format=profile.name, page_count=doc.page_count)
            loop = asyncio.get_running_loop()

            def _tx_progress(frac: float) -> None:
                # 워커 스레드에서 불린다 — 이벤트 루프의 _set_progress(세대
                # 가드 내장)로 넘긴다. result()로 짧게 기다려 역압을 준다.
                asyncio.run_coroutine_threadsafe(
                    _set_progress(external_id, int(frac * 100), generation),
                    loop).result(timeout=10)

            # 전사 엔진 = 사용자가 고른 번역 엔진(비전 가능할 때). 화면에는
            # 엔진 선택이 하나뿐이라 전사만 딴 엔진을 쓰면 설명이 안 된다.
            blocks = await asyncio.to_thread(
                profile.transcribe_blocks, blocks, job_dir,
                lambda: generation == _current_generation(external_id),
                _tx_progress, provider)
            if not blocks:
                raise PdfTranslateError(
                    "판독 가능한 손글씨 노트를 찾지 못했습니다 — "
                    "전사 CLI 상태를 확인하세요")
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

        ko_group_texts = await _translate_group_texts(
            profile, blocks, groups, group_texts,
            provider=provider, cli_model=cli_model, progress_cb=on_progress)

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
            job_dir = pdf_job_dir(external_id)
            # 굽기 진입 = 디스크의 PDF와 계획이 어긋나는 구간의 시작. 계획을
            # **지우지 않고 무효화만** 한다 — 지우면 취소가 이 직후에 도착했을
            # 때 "멀쩡한 옛 PDF + 멀쩡한 편집 파일 + 계획 없음"이 남아, 목록에서
            # 사용자의 수동 라벨이 통째로 사라지고 잡이 되살릴 길 없이 막힌다
            # (overlay_plan.UNBAKED 주석 참조).
            previous_version = invalidate_baked_version(job_dir)
            plan = build_plan(doc, profile, blocks, ko_by_block,
                              job_id=str(external_id),
                              plan_version=previous_version + 1)
            # 파이프라인은 편집 파일을 **읽기만** 한다 — 수동 라벨이
            # (페이지, 판넬, 상대좌표) 주소로 구조적으로 재부착되는 지점이다.
            edits = load_edits(job_dir, job_id=str(external_id))
            composed = compose(plan, edits, panels_resolver(doc, profile))
            if apply_composed(doc, composed.placed) is None:
                return None
            if generation != _current_generation(external_id):
                # 취소/삭제가 오버레이 배치 도중 도착했다 — 여기서 저장을
                # 건너뛰지 않으면 doc.save()의 mkdir(parents=True)이 DELETE가
                # 방금 지운 작업 폴더를 되살리고, DB 행 없는 고아
                # translated.pdf를 남긴다(pdf_jobs엔 프루닝 스윕도 없다).
                return None
            # tmp → os.replace: 디스크의 translated.pdf는 언제나 **완결된**
            # 파일이어야 한다. 재시작 스윕이 "translated_path가 실재하면 done"
            # 으로 복구하는데, 제자리 저장이면 doc.save 도중 죽었을 때 **잘린
            # PDF가 done으로 승격**돼 다운로드 200이 나간다.
            dest = job_dir / "translated.pdf"
            tmp = dest.with_name(dest.name + ".tmp")
            doc.save(tmp)
            if generation != _current_generation(external_id):
                tmp.unlink(missing_ok=True)
                return None
            os.replace(tmp, dest)
            # 계획 저장은 세대 가드 **뒤**에 둔다(취소 시 고아 계획 금지).
            # baked_edits_version은 방금 합성에 쓴 **그 스냅샷**의 값이다 —
            # 여기서 편집 파일을 다시 읽으면 굽지 않은 편집을 구웠다고 기록한다.
            save_plan(job_dir, mark_baked(plan, edits.edits_version))
            return dest

        dest = await asyncio.to_thread(_with_doc_lock, doc_lock, _overlay_and_save)
        if dest is None:
            # 취소로 저장을 건너뛰었다. **최종 상태는 취소 라우트가 쓴다** —
            # 여기서 쓰려 해도 세대가 이미 밀려 있어 억제된다(그게 경합을
            # 없애는 기전이다). 옛 번역본이 남아 있으면 라우트가 `done`으로
            # 수렴시킨다(§4.7-C.7).
            raise asyncio.CancelledError
        await _set_status_if_current(external_id, generation, "done",
                                     translated_path=str(dest))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("pdf job %s failed", external_id)
        await _converge_after_failure(external_id, generation, str(exc))


def _cleanup_tmp_files(job_dir: Path) -> None:
    """중단된 저장이 남긴 `*.tmp` 정리 — 원자 교체의 뒷정리다."""
    try:
        for tmp in job_dir.glob("*.tmp"):
            tmp.unlink(missing_ok=True)
    except OSError:
        logger.warning("pdf: %s 의 tmp 정리 실패", job_dir, exc_info=True)


async def rebake_pdf_job(external_id: UUID) -> None:
    """계획 + 편집을 합성해 `translated.pdf`만 다시 굽는다 — **번역은 하지 않는다.**

    편집 한 건마다 문서를 굽지 않는 이유는 축 (c) 결정에 있다: 편집은 JSON
    왕복(수백 ms)으로 끝내고 문서 재생성은 진행률·취소가 있는 백그라운드 작업으로
    분리한다. 1037페이지 문서에서 키 입력마다 5초를 태울 수는 없다.

    `run_pdf_job`과 **같은 슬롯**(세마포어·세대·doc 수명)을 쓴다 — 복제하면
    세마포어 고갈 방어가 두 벌이 된다.
    """
    mark_rebaking(external_id, True)
    try:
        async with _pdf_job_slot(external_id) as slot:
            generation = slot.generation
            try:
                async with AsyncSessionLocal() as db:
                    job = (await db.execute(
                        select(PdfJob).where(PdfJob.external_id == external_id)
                    )).scalar_one()
                    source_path, fmt = job.source_path, job.format
                if not source_path or not Path(source_path).exists():
                    raise PdfTranslateError("원본 PDF 파일이 없습니다")
                job_dir = pdf_job_dir(external_id)
                plan = load_plan(job_dir)
                if plan is None:
                    raise PdfTranslateError(
                        "이 작업에는 편집 정보가 없습니다 — 다시 번역을 실행하세요")
                profile = profile_by_name(fmt or "")
                if profile is None:
                    raise PdfTranslateError("이 작업은 편집을 지원하지 않습니다")

                await _set_status_if_current(external_id, generation, "overlaying")
                # `_set_status`가 overlaying에 95를 박으므로(`pdf_tasks._PROGRESS`)
                # 곧바로 0으로 되돌린 뒤 0→100으로 올린다 — 95에서 3%로 되감기는
                # 표시를 만들지 않는다.
                await _set_progress(external_id, 0, generation)

                doc = slot.doc = await asyncio.to_thread(
                    open_pdf, Path(source_path))
                edits = load_edits(job_dir, job_id=str(external_id))
                composed = await slot.run(
                    lambda: compose(plan, edits, panels_resolver(doc, profile)))

                progress = {"n": 0}
                total = max(1, len(composed.placed))

                def _apply_and_save() -> Path | None:
                    stats = apply_composed(
                        doc, composed.placed,
                        should_continue=lambda: (
                            generation == _current_generation(external_id)),
                        on_progress=lambda n: progress.__setitem__("n", n))
                    if stats is None:
                        return None      # 취소를 실제로 관측했다
                    dest = job_dir / "translated.pdf"
                    tmp = dest.with_name(dest.name + ".tmp")
                    doc.save(tmp)
                    if generation != _current_generation(external_id):
                        tmp.unlink(missing_ok=True)
                        return None
                    os.replace(tmp, dest)
                    save_plan(job_dir, mark_baked(plan, edits.edits_version))
                    return dest

                async def _poll() -> None:
                    # 폴러 본문 전체를 감싼다 — 진행률은 부가 정보라, 여기서 난
                    # 예외가 재굽기 자체를 죽이면 안 된다.
                    try:
                        while True:
                            await asyncio.sleep(2)
                            await _set_progress(
                                external_id,
                                min(99, int(progress["n"] / total * 100)),
                                generation)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("pdf rebake %s: 진행률 폴러 실패",
                                         external_id)

                poller = asyncio.create_task(_poll())
                try:
                    dest = await slot.run(_apply_and_save)
                finally:
                    # 성공·실패·취소 **모든 경로**에서 폴러를 거둔다(누수 방지).
                    poller.cancel()
                    with suppress(BaseException):
                        await poller

                if dest is None:
                    # 최종 상태는 취소 라우트가 쓴다(§4.7-C.6).
                    raise asyncio.CancelledError
                await _set_status_if_current(external_id, generation, "done",
                                             translated_path=str(dest))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("pdf rebake %s failed", external_id)
                await _converge_after_failure(external_id, generation, str(exc))
    finally:
        mark_rebaking(external_id, False)


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
            # 쓸 만한 번역본이 남아 있으면 `error`로 봉인하지 않는다 — 재굽기나
            # 재번역 도중 앱이 죽은 경우가 여기 해당한다. `error`로 굳히면
            # /download가 영구 409가 되고 편집·rebake가 막히며, in-flight가
            # 아니게 되어 다음 업로드의 프루닝이 그 폴더를 rmtree 후보로 삼는다.
            #
            # 이 승격이 **잘린 PDF를 done으로 만들지 않는** 이유: 최초 번역과
            # 재굽기 두 경로 모두 tmp+os.replace로 저장하므로 디스크의
            # translated.pdf는 언제나 완결된 파일이고, 미완성 산출물은 .tmp로만
            # 존재한다. 중단된 재번역은 계획의 baked_edits_version이 -1로 남아
            # stale=true로 보고되므로 "반영된 줄 아는" 위장도 생기지 않는다.
            if job.translated_path and Path(job.translated_path).exists():
                job.status = "done"
                job.progress = 100
                job.error = "작업이 중단됐습니다 — 다시 굽기를 눌러 주세요"
            else:
                job.status = "error"
                job.error = "서버 재시작으로 중단됨 — 다시 업로드하세요"
            _cleanup_tmp_files(pdf_job_dir(job.external_id))
        if rows:
            await db.commit()


def _jobs_with_edits(external_ids: list) -> set:
    """사람의 편집이 **내용상** 있는 작업들 — 프루닝 고정 대상.

    파일 존재가 아니라 항목 수로 본다. `label_edits.json`은 항목이 0이 돼도
    삭제하지 않으므로(생성/삭제 경합 제거) 존재만 보면 영원히 고정된다.
    """
    pinned = set()
    for eid in external_ids:
        job_dir = pdf_job_dir(eid)
        try:
            has_file = edits_path(job_dir).exists()
            if has_file and load_edits(job_dir, job_id=str(eid)).item_count() > 0:
                pinned.add(eid)
            elif has_file:
                # 파일은 있는데 항목이 0 — 사람이 전부 지웠거나(일반 대상으로
                # 돌아간다) 파일이 깨져 `load_edits`가 빈 값으로 수렴시켰거나다.
                # 둘을 구분할 수 없으므로 **읽어서 항목 0임이 확실한 경우만**
                # 후보로 되돌린다. 여기서는 파싱이 성공했다는 뜻이므로 통과.
                pass
        except Exception:  # noqa: BLE001 — 아래 근거로 의도된 포괄 catch
            # 편집 파일 상태를 확신할 수 없으면 **보존한다.** 번역본은 다시
            # 구울 수 있지만 사람이 친 라벨은 재생성할 수 없어서, 여기서
            # 틀리는 방향은 "지우지 않는 쪽"이어야 한다.
            logger.warning("retention: %s 편집 파일 확인 실패 — 보존한다", eid)
            pinned.add(eid)
    return pinned


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
                select(PdfJob.id, PdfJob.status, PdfJob.external_id).order_by(
                    PdfJob.created_at.desc(), PdfJob.id.desc())
            )).all()
            # 사람이 넣은 편집이 있는 작업은 **아무리 오래돼도 지우지 않는다.**
            #
            # 이 함수 하나가 업로드 시(`pdf_jobs.py`의 `_prune_old_jobs`)와 서버
            # 시작 시(`prune_old_pdf_jobs_at_startup`) 프루닝의 유일한 경로라,
            # 여기 한 번만 막으면 두 경로가 함께 닫힌다. 막지 않으면 스토리보드를
            # 묶음 업로드하는 순간(또는 재시작 한 번에) 두 시간 걸려 넣은 라벨이
            # 경고도 없이 폴더째 사라진다 — `translated.pdf`는 다시 구울 수 있지만
            # 사람이 친 라벨은 재생성할 수 없다.
            #
            # 판정은 파일 존재가 아니라 **내용**이다(`item_count`) — 사용자가
            # 편집을 전부 지우면 다시 일반 대상이 된다.
            #
            # ⚠ 이 재확인은 DB 상태에만 원자적이다. `_prune_delete` 의 WHERE는
            # 컬럼만 볼 수 있고 편집 항목 수는 파일이라, SELECT와 DELETE 사이
            # (밀리초)에 첫 편집이 생기면 그 작업은 여전히 삭제될 수 있다.
            # 창이 매우 좁고(방금 만든 작업은 정렬상 후보에도 안 든다) 완전
            # 배제가 아님을 기록해 둔다.
            pinned = await asyncio.to_thread(
                _jobs_with_edits, [r.external_id for r in rows[keep:]])
            candidate_ids = [r.id for r in rows[keep:]
                             if r.status not in _INFLIGHT_STATUSES
                             and r.external_id not in pinned]
            if pinned:
                logger.info("retention: pinned %d job(s) with manual edits",
                            len(pinned))
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
