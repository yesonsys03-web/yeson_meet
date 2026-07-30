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
import re
from collections import Counter
from collections.abc import Awaitable, Callable

from apps.server.ai.glossary import apply_ko_corrections, glossary_block
from apps.server.domain.video_captions.translate import (
    TranslationError,
    TranslationProvider,
)

from .house_style import apply_house_style

logger = logging.getLogger("yeson.pdf.translate")

WORKERS_ENV = "YESON_PDF_TRANSLATE_WORKERS"
_DEFAULT_WORKERS = 3

# 수작업본(납품 기준, GABE01 King of the Hill 콘티) few-shot 큐레이션 — Task 15
# E2E 후속(2026-07-30). 원문-번역 300+쌍(오프라인 스크립트, 커밋 안 함)에서
# 대표 14쌍만 뽑았다. 화살표 예시는 사용자가 실기에서 직접 지목한 케이스
# ("...so the arrows make sense!" → 축자번역 "그래야 화살표가 말이 되지!"는
# 오역, 수작업본은 "바비:화살표가 이해되게 함께 서보자..").
#
# 액션 노트 종결형 실측(615쌍 중): "-ㄴ다/-한다" 평서형이 압도적 다수이고
# "-하세요/-십시오" 요청형은 "NOTE:"/"PLEASE" 같은 명시적 제작진 지시문(21건)
# 에서만 쓰인다 — 일반 서술형 액션(예: "Hank grabs the gear shift lever.")은
# 전부 평서형이었다. 아래 예시가 그 구분을 보여준다.
#
# 편집 정규화(리뷰 후속, 2026-07-30) — 아래 예시는 원문 주석의 바이트 단위
# 사본이 아니다: (1) 콜론 뒤 공백 삽입("화자명:대사" 붙여쓰기 원문 → "화자명:
# 대사" 표기, "화자명: 대사" 규칙과 일치시키려는 편집), (2) 화살표 예시의
# "바비: " 화자 접두는 원문 주석("바비:화살표가...")에 실재하던 걸 그대로
# 복원한 것(창작 아님) — 단, 그 EN 절반은 두 패널 큐 태그("123 BOBBY" +
# "(CONT.)")를 결합해 재구성했다.
_STYLE_EXAMPLES: list[tuple[str, str]] = [
    # 화살표 예시(GABE01 A1 p963-964 결합) — 사용자 리포트 원본 사례.
    # 사진 포즈 상황에 맞춘 의역("...서보자")이지 "화살표가 말이 되도록"류
    # 축자번역이 아니다. 화자줄(화자명: 대사) 관례도 함께 시연.
    ("123 BOBBY Let's walk around... (CONT.) ...so the arrows make sense!",
     "바비: 화살표가 이해되게 함께 서보자.."),
    # 화자줄: 다중 화자(/) 표기 + 큐번호 생략 (GABE01 A1 p30)
    ("3 HANK/EMPLOYEES Propane.", "행크/직원들: 프로판."),
    # (CONT.)로 두 패널에 쪼개진 미완성 문장 — 이웃 문맥으로 이어 완결된
    # 두 문장으로 재구성 (GABE01 A1 p53+58)
    (("6 HANK (CONT.) I want to say how proud I am of all of you... "
      "for getting this showroom worthy of the fine product we sell."),
     ("행크: 난 그냥 자네들이 얼마나 자랑스러운지 말하고 싶었어. 여러분 모두가 "
      "우리가 판매하는 훌륭한 제품에 걸맞은 쇼룸을 만들어 준 것에 대해 정말 "
      "감사해.")),
    # (O.S.) 표시는 대사에 옮기지 않고 생략 + "suck" 구어체 의역
    # (GABE01 A1 p579)
    ("66 JIMMY (O.S.) Your pro-nuts suck!", "지미: 당신들 도너츠 완전 별로야!"),
    # 짧은 구어체 의역 — 축자 "통과"가 아닌 자연스러운 대화체 (GABE01 A1 p130)
    ("16 HANK Pass.", "행크: 넘어가죠."),
    # 감탄 어미(-군) 자연스러운 구어체 (GABE01 A1 p215)
    ("26 ENRIQUE Like Propane Jesus!", "엔리케: 프로판가스 예수님같군!"),
    # 팝컬처 고유명사는 음역으로 유지 (GABE01 A1 p99)
    ("10 HANK (CONT.) Lando Calrissian?", "행크: 랜도 칼리시안?"),
    # 관용구(배 은유)는 축자번역 대신 의미로 (GABE01 A1 p64+68)
    ("The Strickland ship has officially been righted.",
     "행크: 스트릭랜드는 이제 완전히 제자리를 찾았어."),
    # action: 평서형(-ㄴ다) 종결 관례 — 요청형이 아님 (GABE01 A1 p6)
    ("Hank grabs the gear shift lever.", "행크가 기어변속 레버를 잡는다."),
    # action: 문장 재구성(의역) + 과거 서술체. 원문 자체가 마침표 없이 끊긴
    # 문장인데도 문맥으로 완결된 한국어 문장을 만든다 (GABE01 A1 p162)
    ("Hank walks over to a framed picture of himself, PEGGY and BOBBY",
     "행크는 자신과 페기, 바비가 함께 찍은 액자 사진 앞으로 걸어갔다."),
    # action: 카메라 지시 관례 (GABE01 A1 p888)
    ("CAM ADJUST", "카메라 조정"),
    # action: 진짜 명령문(제작진 지시)만 요청형(-세요) — 서술형 액션과 구분
    # (GABE01 A1 p97)
    ("NOTE: PLEASE HOOKUP DESK TO SC13", "노트: 씬13으로 책상 훅업해주세요."),
    # action: 고유명사(브랜드) 음역 일관성 + "sales floor"→"매장" 의역
    # (GABE01 A1 p186)
    ("INT. STRICKLAND PROPANE - SALES FLOOR - MORNING",
     "스트릭랜드 프로판 내부-매장-아침"),
    # action: 짧은 평서형(화살표 예문 바로 다음 패널) (GABE01 A1 p968)
    ("They pose for a photo.", "그들은 포즈를 취한다."),
]


# 하우스 표기·화계 시트 — Task 18(사람 납품본 실측, house_style.py의
# HOUSE_KO_CORRECTIONS 표와 동일 근거). 프롬프트 단에서 먼저 맞는 표기·
# 화계로 내게 하고, house_style.apply_house_style이 KO→KO 후처리로 한 번
# 더 고정한다(이중 방어 — LLM이 프롬프트를 놓쳐도 후처리가 잡는다).
_HOUSE_STYLE_BLOCK = (
    "House-style renderings (use EXACTLY these Korean forms):\n"
    "Joseph=죠셉, Boomhauer=붐하우어, Thatherton=대더튼, Ray Roy=레이로이,\n"
    "Char King Especiale=챠 킹 에스페시알레, FX=효과, props=소품, ANGLE ON:=구도:,\n"
    "ESTABLISHING=설정, Camera move=카메라 무브, Cam Pos.=카메라 포즈, NEW ART=뉴 아트.\n"
    "Register (화계) consistency: keep each character's politeness level consistent\n"
    "toward the same listener across the whole document. HANK speaks politely\n"
    "(해요체/합쇼체) to employees and customers — never 반말/하게체 to them.\n"
    "When unsure, match the register of neighboring lines by the same speaker.\n"
)


def _style_examples_block() -> str:
    lines = [f"EN: {en}\nKO: {ko}" for en, ko in _STYLE_EXAMPLES]
    return (
        "Examples of the required style (English → Korean). Some examples "
        "show text merged from consecutive panels for context; your OUTPUT "
        "must still contain exactly one translation per input array item — "
        "never merge or split items.\n" + "\n\n".join(lines)
    )


def build_pdf_prompt(texts: list[str]) -> str:
    """제작 문서(스토리보드 대사·액션 노트) 배열 → KO 번역 지시 프롬프트."""
    numbered = json.dumps(texts, ensure_ascii=False)
    return (
        "Translate each English text block from an animation production "
        "document (storyboard dialog and action notes) into natural Korean "
        "for Korean animation staff.\n"
        "Translate for MEANING in natural spoken Korean as a professional "
        "Korean animation-script translator would — never word-for-word "
        "literal renderings. Prefer the phrasing a Korean voice actor could "
        "speak naturally.\n"
        "The array items are consecutive storyboard blocks from the same "
        "episode, in order — use neighboring items as context for pronouns, "
        "continuations ((CONT.)), and incomplete sentences.\n"
        "Descriptive action/stage-direction notes (e.g. \"Hank grabs the "
        "gear shift lever.\") take plain declarative 평서형 (예: '...한다'). "
        "Only explicit directives to the production staff (e.g. lines "
        "starting with \"NOTE:\" or \"PLEASE\") take polite 요청형 (예: "
        "'...하세요'). Keep dialogue natural and faithful to the tone of "
        "the source.\n"
        "Do NOT translate asset IDs, scene/panel codes, or file-name-like "
        "tokens (e.g. TGNO_PizzaBox_CL_V01, 5LBW03_07_01) — copy them "
        "unchanged.\n"
        "Panel callout labels: translate the word part and keep any trailing "
        "code (e.g. \"CAR006A\" → \"차006A\", \"HANK'S TRUCK\" → \"행크의 "
        "트럭\"); labels that are pure codes (e.g. \"1000SB\", \"656A\") — "
        "copy them unchanged.\n"
        "When a dialog block begins with a leading cue number and speaker "
        "name (e.g. \"3 HANK/EMPLOYEES Propane.\"), format the Korean as "
        "\"화자명: 대사\" — translate the speaker name, omit the leading "
        "cue number (e.g. \"행크/직원들: 프로판.\").\n"
        + _style_examples_block() + "\n\n"
        "Copy every digit sequence (scene/shot references like sc103, "
        "counts, codes) EXACTLY as in the source — never alter, swap, or "
        "invent digits.\n"
        "Input is a JSON array of strings; return ONLY a JSON array of the "
        "same length with the Korean translations in the same order.\n"
        "Return ONLY the JSON array. No prose, no markdown fences.\n"
        + _HOUSE_STYLE_BLOCK + "\n"
        "Use this glossary:\n"
        + glossary_block()
        + "\n\nInput:\n" + numbered
    )


_DIGITS_RE = re.compile(r"\d+")


def _verify_numbers(src: str, ko: str) -> tuple[str, str]:
    """숫자열(re.findall(r"\\d+")) 보존 검증 + 자동 교정 — Task 16(E2E 후속
    5, 사용자 실기 오류 신고: "...sc103." → "...씬109..." 오염).

    반환 (교정된 ko, verdict) — verdict ∈ {"ok", "fixed", "unresolved"}.

    규칙(멀티셋 기준):
    - 소스에 없는 숫자열이 KO에 등장하면 오염 후보(foreign).
    - foreign마다 소스에서 누락된(KO에 없는) 숫자열 중 같은 자릿수인 후보를
      찾는다 — 후보 정확히 1개면 그 값으로 치환(fixed, 발생 횟수 전부),
      0개면 허용(no action — 영어 수사 "two"→"2" 같은 정당한 변환 오탐
      방지), 2개 이상이면 모호(unresolved, 원문 그대로 반환).
    - 소스 숫자가 KO에서 사라진 것 자체는 허용 — 화자줄 규칙이 선행 큐
      번호를 의도적으로 생략하므로 누락 단독은 오류가 아니다.
    """
    src_counts = Counter(_DIGITS_RE.findall(src))
    ko_counts = Counter(_DIGITS_RE.findall(ko))
    foreign = ko_counts - src_counts  # KO에만 있는(초과) 숫자열
    missing = src_counts - ko_counts  # SRC에만 있는(누락된) 숫자열

    if not foreign:
        return ko, "ok"

    result = ko
    ambiguous = False
    for number in foreign:
        candidates = [m for m in missing if len(m) == len(number)]
        if not candidates:
            continue  # 허용 — 같은 자릿수 누락 후보가 없으면 대응 실종 아님
        if len(candidates) > 1:
            ambiguous = True
            continue
        pattern = re.compile(r"(?<!\d)" + re.escape(number) + r"(?!\d)")
        result = pattern.sub(candidates[0], result)

    if ambiguous:
        return ko, "unresolved"
    if result != ko:
        return result, "fixed"
    return ko, "ok"


async def _verify_and_fix_numbers(
        provider: TranslationProvider, src: str, ko: str) -> str:
    """_verify_numbers 결과에 따라 채택/재번역/원문유지 폴백을 수행한다
    (블록당 재번역 최대 1회 — Task 16)."""
    fixed_ko, verdict = _verify_numbers(src, ko)
    if verdict == "ok":
        return ko
    if verdict == "fixed":
        logger.info(
            "pdf-translate: 숫자 오염 자동교정 원문=%r 오염값=%r 교정후=%r",
            src[:80], ko[:80], fixed_ko[:80])
        return fixed_ko

    # unresolved → 블록 단건 재번역 폴백(기존 kept-as-source 경로 재사용).
    retried = await _resilient(provider, [src])
    retried_ko = apply_house_style(apply_ko_corrections(retried[0].strip()))
    re_fixed, re_verdict = _verify_numbers(src, retried_ko)
    if re_verdict != "unresolved":
        if re_verdict == "fixed":
            logger.info(
                "pdf-translate: 숫자 오염 재번역 후 자동교정 원문=%r "
                "교정후=%r", src[:80], re_fixed[:80])
        return re_fixed

    logger.warning(
        "pdf-translate: 숫자 오염 재번역 후에도 미해결 — 원문 유지 "
        "원문=%r 오염번역=%r", src[:80], retried_ko[:80])
    return src


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


# 동일 원문 dedupe 정규화 키(Task 18) — 공백·기타 구두점은 한 칸으로 접되,
# 문말 부호(? ! . …)는 지우지 않는다. 전부 지우면(원래 시도했던
# `re.sub(r"[\s\W]+", " ", src.lower())`) 실측 코퍼스에서 실제 충돌이
# 났다 — `'37 BOBBY (CONT.) ...this?'`(의문으로 흐리는 조각)와 `'37 BOBBY
# (CONT.) This...'`(새로 여는 조각)가 같은 키로 뭉쳐 서로 다른 번역을
# 공유했다. 문말 부호만 보존하면 이 충돌이 사라진다(구두점 전체를 보존할
# 필요는 없다).
_DEDUPE_KEY_RE = re.compile(r"[^\w?!.…]+")


def _dedupe_key(text: str) -> str:
    return _DEDUPE_KEY_RE.sub(" ", text.lower()).strip()


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

    # 동일 원문 번역 캐시(Task 18) — Task 17이 발화를 그룹 텍스트로 낮춘
    # 뒤에도 남는 (CONT.)-헤더 조각·패널 콜아웃 라벨류 중복을 없애 LLM
    # 호출을 줄이고, 같은 원문이 페이지마다 다르게 번역되는 비결정성(사람
    # 번역본 비교 P3)도 구조적으로 없앤다. 경쟁 조건 없음 — 배치 전
    # 결정적 전처리라 병렬 청크는 유니크 목록 위에서만 돈다.
    first_seen: dict[str, int] = {}
    unique_texts: list[str] = []
    index_map: list[int] = []
    for text in texts:
        key = _dedupe_key(text)
        unique_idx = first_seen.get(key)
        if unique_idx is None:
            unique_idx = len(unique_texts)
            first_seen[key] = unique_idx
            unique_texts.append(text)
        index_map.append(unique_idx)
    logger.info("pdf-translate: dedupe: %d→%d unique", len(texts), len(unique_texts))

    chunks = [unique_texts[i:i + chunk_size]
              for i in range(0, len(unique_texts), chunk_size)]
    total = len(unique_texts)
    sem = asyncio.Semaphore(_workers_from_env())
    progress_lock = asyncio.Lock()
    results: list[list[str]] = [[] for _ in chunks]
    done_blocks = 0

    async def _run_chunk(idx: int, chunk: list[str]) -> None:
        nonlocal done_blocks
        async with sem:
            translated = await _resilient(provider, chunk)
            # 하우스 표기 강제 치환(Task 18)은 apply_ko_corrections
            # 다음·숫자 게이트 이전에 적용한다(숫자와 무관해 순서가 결과에
            # 영향을 주진 않지만, 고정 순서로 테스트가 잠근다).
            corrected = [
                apply_house_style(apply_ko_corrections(t.strip()))
                for t in translated
            ]
            # 숫자 보존 게이트(Task 16) — 순서·진행률 계약을 흔들지 않게
            # 같은 청크 워커·같은 세마포어 구간 안에서 후처리한다.
            results[idx] = [
                await _verify_and_fix_numbers(provider, src, ko)
                for src, ko in zip(chunk, corrected)
            ]
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

    unique_out: list[str] = []
    for chunk_result in results:
        unique_out.extend(chunk_result)
    return [unique_out[i] for i in index_map]
