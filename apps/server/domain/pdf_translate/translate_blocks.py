"""PDF 블록 배치 번역 — 자막 도메인의 엔진(create_translator)을 그대로 쓰되
프롬프트와 리질리언트 배치는 이 도메인 소유다.

_translate_resilient(자막 모듈 private)를 import하지 않고 동일 알고리즘을
여기 둔다 — 자막 쪽 리팩토링이 PDF 번역을 흔들지 않게 도메인을 분리한다.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable

from apps.server.ai.glossary import apply_ko_corrections, glossary_block
from apps.server.domain.video_captions.translate import (
    TranslationError,
    TranslationProvider,
)

logger = logging.getLogger("yeson.pdf.translate")

WORKERS_ENV = "YESON_PDF_TRANSLATE_WORKERS"
_DEFAULT_WORKERS = 3


def build_pdf_prompt(texts: list[str]) -> str:
    """제작 문서(스토리보드 대사·액션 노트) 배열 → KO 번역 지시 프롬프트."""
    numbered = json.dumps(texts, ensure_ascii=False)
    return (
        "Translate each English text block from an animation production "
        "document (storyboard dialog and action notes) into natural Korean "
        "for Korean animation staff.\n"
        "Keep imperative production notes in polite 요청형 (예: '...하세요'). "
        "Keep dialogue natural and faithful to the tone of the source.\n"
        "Do NOT translate asset IDs, scene/panel codes, or file-name-like "
        "tokens (e.g. TGNO_PizzaBox_CL_V01, 5LBW03_07_01) — copy them "
        "unchanged.\n"
        "When a dialog block begins with a leading cue number and speaker "
        "name (e.g. \"3 HANK/EMPLOYEES Propane.\"), format the Korean as "
        "\"화자명: 대사\" — translate the speaker name, omit the leading "
        "cue number (e.g. \"행크/직원들: 프로판.\").\n"
        "Input is a JSON array of strings; return ONLY a JSON array of the "
        "same length with the Korean translations in the same order.\n"
        "Return ONLY the JSON array. No prose, no markdown fences.\n"
        "Use this glossary:\n"
        + glossary_block()
        + "\n\nInput:\n" + numbered
    )


async def _resilient(provider: TranslationProvider, texts: list[str],
                     cause: str | None = None) -> list[str]:
    """개수 불일치/오류에 견디는 배치 — 반으로 쪼개 재시도, 1줄 실패는 원문 유지."""
    if not texts:
        return []
    try:
        result = await provider.translate_batch(texts)
        if len(result) == len(texts):
            return result
        cause = f"반환 개수 불일치({len(result)} != {len(texts)})"
    except TranslationError as exc:
        cause = str(exc)
    if len(texts) == 1:
        logger.warning("pdf-translate: 1블록 번역 실패(%s) — 원문 유지: %r",
                       cause or "원인 미상", texts[0][:60])
        return list(texts)
    mid = len(texts) // 2
    left = await _resilient(provider, texts[:mid], cause)
    right = await _resilient(provider, texts[mid:], cause)
    return left + right


def _workers_from_env() -> int:
    raw = os.environ.get(WORKERS_ENV, "")
    try:
        workers = int(raw) if raw.strip() else _DEFAULT_WORKERS
    except ValueError:
        workers = _DEFAULT_WORKERS
    return max(1, workers)


async def translate_texts(
    texts: list[str],
    provider: TranslationProvider,
    *,
    chunk_size: int = 50,
    progress_cb: Callable[[float], Awaitable[None]] | None = None,
) -> list[str]:
    """청크(chunk_size블록) 단위로 나눠 Semaphore(workers)로 동시 실행한다
    (YESON_PDF_TRANSLATE_WORKERS, 기본 3 — 1 이하면 사실상 기존 직렬과
    동일하게 동작). 결과는 청크 인덱스로 재조립해 입력 순서를 그대로
    보존하고, 진행률은 "완료된" 청크들의 블록 수 누적이라 완료 순서와
    무관하게 단조 증가한다(progress_cb 호출은 락으로 직렬화).

    CliTranslator 인스턴스 하나를 여러 청크가 동시에 공유해도 안전하다 —
    유일한 가변 상태 변경은 translate_batch() 맨 앞의 _ensure_binary()가
    argv[0]을 절대경로로 1회 교체하는 것뿐인데, 이 헬퍼 자체가 await 없는
    동기 코드라 각 translate_batch 호출의 첫 진입부에서 이벤트 루프로
    제어권이 넘어가기 전에 원자적으로 끝난다(다른 태스크가 끼어들 수
    없고, 같은 절대경로를 여러 번 써도 멱등이라 실제 경쟁도 없다).
    """
    if not texts:
        return []
    chunks = [texts[i:i + chunk_size] for i in range(0, len(texts), chunk_size)]
    total = len(texts)
    sem = asyncio.Semaphore(_workers_from_env())
    progress_lock = asyncio.Lock()
    results: list[list[str]] = [[] for _ in chunks]
    done_blocks = 0

    async def _run_chunk(idx: int, chunk: list[str]) -> None:
        nonlocal done_blocks
        async with sem:
            translated = await _resilient(provider, chunk)
        results[idx] = [apply_ko_corrections(t.strip()) for t in translated]
        if progress_cb is not None:
            async with progress_lock:
                done_blocks += len(chunk)
                logger.info("pdf-translate: %d/%d blocks", done_blocks, total)
                await progress_cb(done_blocks / total)

    tasks = [asyncio.ensure_future(_run_chunk(i, chunk))
             for i, chunk in enumerate(chunks)]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        # 러너의 세대 가드(progress_cb의 CancelledError) 등 어떤 예외로든
        # 여기 도달하면, 아직 끝나지 않은 청크 태스크를 명시적으로
        # cancel + await 해서 고아 태스크(=백그라운드에서 계속 도는 CLI
        # 서브프로세스)를 남기지 않는다.
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    out: list[str] = []
    for chunk_result in results:
        out.extend(chunk_result)
    return out
