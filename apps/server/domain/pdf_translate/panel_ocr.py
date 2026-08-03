"""패널 OCR — 빨강 콜아웃 라벨 추출(Task 19) + 깨진 추출 문자 복구(Task 20).

두 기능 모두 "화면에는 제대로 그려져 있는데 텍스트 추출로는 못 읽는" 같은
문제를 같은 도구(렌더 + RapidOCR)로 푼다 — 엔진 싱글턴·PNG 디코딩·픽셀
크롭을 공유하려고 한 모듈에 둔다(별도 OCR 경로 신설 금지).

--- 1) 패널 콜아웃 라벨(빨강 리더라인 주석) 추출 — 빨강 프리필터 + RapidOCR.

Storyboard Pro 패널은 페이지당 단일 래스터 이미지로 구워져 있어(텍스트/주석/
벡터 전무) 일반 텍스트 추출로는 콜아웃 라벨("HANK'S TRUCK", "CAR006A",
"1000SB")을 전혀 못 읽는다(원본 p1 해부 실측). 라벨은 빨간 글자 + 빨간
사각/리더라인 관례라, 저해상도(dpi 60) 빨강 픽셀 카운트로 라벨 없는
대다수 페이지를 값싸게 걸러내고(프리필터), 통과한 페이지만 dpi 200으로
재렌더해 RapidOCR을 전체 실행한 뒤 히트별 빨강 비율로 검정 그림선 오탐을
배제한다.

--- 2) 깨진 추출 문자 복구(Task 20).

이 문서군은 일부 페이지에서 글리프→유니코드 매핑이 깨져 있다 — 화면에는
`sc49`가 제대로 그려지는데 텍스트 추출은 `sc4B`를 준다(페이지마다 매핑이
다르고 고정 오프셋 공식이 없다). 백엔드가 PDF 자신의 "이 글리프 유니코드를
모른다" 표식으로 깨진 단어를 짚어 주면(backend.CorruptWord), 여기서 그
단어만 렌더·재판독해 **깨진 문자 위치만** 고친다.

RapidOCR 엔진 지연 싱글턴은 video_captions/slate_ocr.py의 초기화 패턴을
미러한다(프로세스당 1회 로드, 스레드 로컬).
"""
from __future__ import annotations

import io
import logging
import os
import re
import threading
from difflib import SequenceMatcher

import numpy as np

from .backend import CorruptWord, PdfDocument, RawBlock

logger = logging.getLogger("yeson.pdf.panel_ocr")

_local = threading.local()

ENV_ENABLED = "YESON_PDF_PANEL_OCR"

_PREFILTER_DPI = 60
_OCR_DPI = 200
# 빨강 판정(프리필터·히트 비율 공용, 실측 재설계 2026-07-30): 절대 채도
# 기준(구 R>150,G<100,B<100)은 얇은 빨간 글자의 안티에일리어싱 희석에
# 취약하다 — p18 실측: 60dpi 프리필터 크롭은 옛 마스크로 5px뿐(문턱 50
# 미달, 전멸), 심지어 200dpi OCR 히트 자체도 옛 마스크 기준 비율
# 0.053~0.083(문턱 0.15 근처에도 한참 못 미침, 라벨 6종 전부). R−G/R−B
# 차이 기준으로 바꾸면 같은 픽셀들이 안정적으로 잡힌다(같은 페이지 60dpi
# 33px, 200dpi 히트 비율 0.237~0.348) — 검정 그림선(사인·표지판 텍스트
# 등)은 그레이스케일이라 R≈G≈B, 두 마스크 다 항상 0에 가까워 오탐이
# 늘지 않는다(p1·p93·p18의 검정 히트 전부 실측 0.000). 차이 기준 마스크는
# 절대 채도 마스크의 초집합이라(R>150&G<100&B<100 ⟹ R−G>50&R−B>50) 기존에
# 잡히던 히트는 전부 그대로 잡힌다.
_RED_R_MIN = 120
_RED_DIFF_MIN = 50
# 프리필터 문턱(60dpi 픽셀 카운트): p18 실측 33px에 맞춰 50→10 완화 —
# 오탐 페이지는 OCR 1~2초 비용뿐이라 완화 방향이 안전(전 문서 재측정으로
# 폭주 없음 확인, task-19-report.md 참고).
_PREFILTER_MIN_PIXELS = 10
# 히트별 빨강 비율 문턱(200dpi 크롭): 확인된 잡음(검정 그림선, 전부
# 0.000~0.071 — YOSEMITE 0.071은 이 태스크 범위 밖이라 손대지 않음)과
# 확인된 유효 라벨(p1·p18·p93·p410 전부 0.219~0.391) 사이 실측 간극에
# 여유를 두고 0.15로 설정.
_HIT_RED_RATIO_MIN = 0.15

# 색과 무관하게 통과시키는 제작 지시어(2026-07-31, FL102 실측).
#
# 왜 필요한가: 위 빨강 비율 문턱은 "패널 라벨은 빨간 글자"라는 GABE01 관례를
# 전제한다. FL102는 같은 성격의 제작 지시어를 **검정 글자**로 적는다 —
# 계측 결과 OCR은 `CAM GUIDE`를 신뢰도 1.00으로 정확히 읽는데 빨강 비율이
# 0.000이라 이 문턱에서 버려졌다(사용자 신고: p27 "카메라 가이드 번역 누락").
# 사람 납품본은 이 문서에서 `카메라 가이드` 6건·`필드가이드…` 5건을 단다.
#
# 왜 문턱을 낮추지 않는가: 같은 문턱이 그림 속 간판(p6 `REHABCENTER`)과
# 씬/판넬 숫자도 버리는데 **그건 버리는 게 맞다**(사람도 번역하지 않는다).
# 판별 기준은 색이 아니라 "제작 지시어냐 그림 속 사물이냐"이므로, 색 문턱은
# 그대로 두고 확인된 지시어만 이름으로 통과시킨다.
#
# 공백 무시로 매칭한다 — RapidOCR이 같은 라벨을 `CAM GUIDE`로도
# `CAMGUIDE`로도 돌려준다(실측: p30 vs p27).
_PRODUCTION_LABEL_TERMS = ("CAMGUIDE", "FIELDGUIDE", "REFERENCE")
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


def _is_production_label(text: str) -> bool:
    """확인된 제작 지시어를 포함하면 True — 빨강 비율 검사를 우회한다.

    뒤에 식별자가 붙는 변형(`FIELD GUIDE 1-2`, `FIELD GUIDE A LOUIS ONLY`)을
    일일이 열거하지 않으려고 원래 **접두** 매칭이었다. 그런데 실물은 앞에도
    수식어를 단다 — FL104 p16의 `CAMERA FIELD GUIDE (MANNY and BELLE ONLY)`는
    squash하면 `CAMERAFIELDGUIDE…`라 `CAMGUIDE`로도 `FIELDGUIDE`로도 시작하지
    않아 통째로 버려졌다(사용자 신고 "카메라 필드 가이드 번역 누락").
    OCR은 이 라벨을 신뢰도 1.00으로 정확히 읽고 있었는데 색 문턱에서 죽은
    것이다(검정 지시어, 빨강 비율 0.000).

    그래서 포함(substring) 매칭으로 넓힌다. 접두→포함으로 넓히면 필드 본문의
    `CAM FIELD GUIDE 1-2` 같은 텍스트까지 통과할 수 있는데, 그건
    `_panel_region`이 이제 필드 박스를 OCR 영역에서 제외하므로(같은 날 수정)
    애초에 히트로 들어오지 않는다 — 두 수정은 함께 가야 한다."""
    squashed = _NON_ALNUM.sub("", text).upper()
    return any(t in squashed for t in _PRODUCTION_LABEL_TERMS)


_HANGUL = re.compile(r"[가-힣]")

# 제작 지시어 다음 줄에 오는 괄호 한정구(`(MANNY and BELLE ONLY)`) — 위 줄이
# 통과된 제작 지시어일 때만 함께 살린다(find_panel_labels의 2차 통과).
_PARENTHETICAL_RE = re.compile(r"^\(.*\)$")
# 두 줄 사이 허용 간격(200dpi 픽셀). 12pt 한 줄이 약 33px이라 그보다 좁게 잡아
# **바로 다음 줄**만 인정한다 — 멀리 떨어진 그림 속 괄호까지 끌어오지 않는다.
_QUALIFIER_MAX_GAP_PX = 25


def _new_engine(**kwargs):  # test seam(slate_ocr 미러)
    from rapidocr_onnxruntime import RapidOCR  # 지연 import(번들 무관 경로 보호)
    return RapidOCR(**kwargs)


def _reset_engines() -> None:  # test seam
    global _local
    _local = threading.local()


def _get_engine():
    """RapidOCR 지연 생성 — 스레드마다 자기 엔진(slate_ocr과 동일한 이유:
    엔진 하나당 모델 로드 비용 ~수백ms, 스레드 로컬로 두면 오염 위험이 없다)."""
    engine = getattr(_local, "engine", None)
    if engine is None:
        engine = _new_engine()
        _local.engine = engine
    return engine


def _panel_ocr_enabled() -> bool:
    return os.environ.get(ENV_ENABLED, "1") != "0"


def _decode_png(png_bytes: bytes) -> np.ndarray:
    from PIL import Image  # RapidOCR 전이의존(lock 고정) — 지연 import
    with Image.open(io.BytesIO(png_bytes)) as im:
        return np.array(im.convert("RGB"))


def _red_mask(arr: np.ndarray) -> np.ndarray:
    """빨강 마스크(프리필터·히트 비율 공용) — R−G/R−B 차이 기준이라
    안티에일리어싱으로 희석된 얇은 빨간 글자도 잡고, 그레이스케일인 검정
    그림선(R≈G≈B)은 밝기와 무관하게 걸러진다."""
    r = arr[..., 0].astype(np.int16)
    g = arr[..., 1].astype(np.int16)
    b = arr[..., 2].astype(np.int16)
    return (r > _RED_R_MIN) & (r - g > _RED_DIFF_MIN) & (r - b > _RED_DIFF_MIN)


def _crop_region_px(arr: np.ndarray, region: tuple[float, float, float, float],
                    dpi: int) -> np.ndarray:
    """region(pdf pt) → 렌더된 배열의 픽셀 슬라이스(dpi 배율 + 경계 클램프)."""
    scale = dpi / 72.0
    rx0, ry0, rx1, ry1 = region
    h, w = arr.shape[:2]
    x0 = max(0, min(w, round(rx0 * scale)))
    y0 = max(0, min(h, round(ry0 * scale)))
    x1 = max(x0, min(w, round(rx1 * scale)))
    y1 = max(y0, min(h, round(ry1 * scale)))
    return arr[y0:y1, x0:x1]


def find_panel_labels(
    doc: PdfDocument, page: int,
    region: tuple[float, float, float, float],
) -> list[RawBlock]:
    """region(pdf pt, (x0,y0,x1,y1)) 안에서 빨강 콜아웃 라벨을 OCR로 찾는다.

    킬스위치 YESON_PDF_PANEL_OCR=0이면 렌더조차 하지 않고 즉시 []. 프리필터
    (dpi 60 저해상도 렌더 + 빨강 픽셀 카운트)를 통과 못 하면 비싼 dpi 200
    렌더·OCR을 아예 건너뛴다 — 대다수 페이지(라벨 없음)의 비용을 없앤다.
    """
    if not _panel_ocr_enabled():
        return []

    low_arr = _decode_png(doc.render_png(page, dpi=_PREFILTER_DPI))
    low_crop = _crop_region_px(low_arr, region, _PREFILTER_DPI)
    if (low_crop.size == 0
            or int(_red_mask(low_crop).sum()) < _PREFILTER_MIN_PIXELS):
        return []

    hi_arr = _decode_png(doc.render_png(page, dpi=_OCR_DPI))
    hi_crop = _crop_region_px(hi_arr, region, _OCR_DPI)
    if hi_crop.size == 0:
        return []

    try:
        result, _elapse = _get_engine()(hi_crop)
    except Exception:  # 한 페이지 OCR 실패가 추출 전체를 막지 않게
        logger.exception("panel OCR failed for page %d", page)
        return []
    if not result:
        return []

    scale = _OCR_DPI / 72.0
    region_x0, region_y0, _rx1, _ry1 = region
    crop_h, crop_w = hi_crop.shape[:2]
    out: list[RawBlock] = []
    # 제작 지시어의 괄호 한정구(둘째 줄)를 살리기 위한 기록 — 아래 2차 통과 참고.
    kept_production_px: list[tuple[int, int, int]] = []  # (x0, x1, y1)
    deferred: list[tuple[str, tuple[int, int, int, int]]] = []
    for box, text, _score in result:
        text = text.strip()
        if not text or _HANGUL.search(text):
            continue
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        bx0 = max(0, min(crop_w, int(min(xs))))
        bx1 = max(bx0, min(crop_w, int(max(xs))))
        by0 = max(0, min(crop_h, int(min(ys))))
        by1 = max(by0, min(crop_h, int(max(ys))))
        sub = hi_crop[by0:by1, bx0:bx1]
        if sub.size == 0:
            continue
        # 히트 bbox 내 빨강 픽셀 비율 — 패널 라벨은 빨간 글자이므로 검정
        # 그림선(사인·표지판 텍스트 등)이 우연히 OCR 히트가 돼도 여기서 걸러진다.
        # 단 확인된 제작 지시어(CAM GUIDE 등)는 검정으로 적히는 문서가 있어
        # 색 검사를 우회한다(_PRODUCTION_LABEL_TERMS 주석 참고).
        is_production = _is_production_label(text)
        if (float(_red_mask(sub).mean()) < _HIT_RED_RATIO_MIN
                and not is_production):
            # 지금 버리되, 제작 지시어의 괄호 한정구일 수 있으니 후보로 남긴다.
            if _PARENTHETICAL_RE.match(text):
                deferred.append((text, (bx0, by0, bx1, by1)))
            continue
        if is_production:
            kept_production_px.append((bx0, bx1, by1))
        out.append(RawBlock(
            text=text,
            bbox=(bx0 / scale + region_x0, by0 / scale + region_y0,
                  bx1 / scale + region_x0, by1 / scale + region_y0),
        ))

    # 2차 통과: 제작 지시어 **바로 아래**에 붙은 괄호 한정구를 되살린다.
    #
    # 실물(FL104 p16): `CAMERA FIELD GUIDE` 다음 줄이 `(MANNY and BELLE ONLY)`이고
    # 사람은 두 줄 다 옮긴다(`카메라 필드 가이드` / `(매니&벨만)`). 이 줄은 지시어
    # 이름을 갖고 있지 않아 _is_production_label로는 못 잡고, 검정이라 색 문턱에도
    # 걸린다 — 그렇다고 괄호를 무조건 통과시키면 그림 속 괄호 텍스트까지 들어온다.
    # 그래서 "바로 위에 통과된 제작 지시어가 있고 x가 겹칠 때"로만 한정한다.
    for text, (bx0, by0, bx1, by1) in deferred:
        for kx0, kx1, ky1 in kept_production_px:
            if by0 - ky1 <= _QUALIFIER_MAX_GAP_PX and by0 >= ky1 - 2 \
                    and min(bx1, kx1) - max(bx0, kx0) > 0:
                out.append(RawBlock(
                    text=text,
                    bbox=(bx0 / scale + region_x0, by0 / scale + region_y0,
                          bx1 / scale + region_x0, by1 / scale + region_y0),
                ))
                break
    return out


# ── 깨진 추출 문자 복구 (Task 20) ────────────────────────────────────────

ENV_TEXT_REPAIR = "YESON_PDF_TEXT_REPAIR"

# 단어 크롭 렌더 해상도. 200dpi(라벨 OCR)보다 높인 건 본문 글자가 라벨보다
# 작아서다 — 실측(GABE01 A1 깨진 21페이지 전수)에서 300dpi가 이 문서의
# 본문 크기에 안정적이었다.
_REPAIR_DPI = 300
# 크롭 여백(pt) — 가로/세로를 따로 둔다. 실측 스윕에서 가로 여백을 4pt로
# 키우면 옆 글자 일부가 딸려 들어와 없던 문자가 생겼고(`7Cont.8` →
# `((Cont.)`), 2pt로 좁히되 세로를 3pt 두면 글자 위아래가 잘리지 않아
# 얇은 괄호까지 정확히 읽혔다(`(Cont.)` 신뢰도 1.000).
_REPAIR_PAD_X = 2.0
_REPAIR_PAD_Y = 3.0
# 채택 최소 신뢰도(히트들 중 최솟값 기준). 실측에서 실제 복구 케이스는
# 전부 0.90 이상이었고, 글자 하나짜리 크롭처럼 근거가 약한 판독은 0.58까지
# 떨어졌다 — 그 사이에 문턱을 둔다. 미달이면 원래 추출값을 유지한다.
_REPAIR_MIN_SCORE = 0.80

_DIGITS = re.compile(r"\d+")


def _text_repair_enabled() -> bool:
    return os.environ.get(ENV_TEXT_REPAIR, "1") != "0"


def _align_repair(extracted: str, ocr_text: str,
                  bad_indices: tuple[int, ...]) -> str:
    """OCR 판독을 추출 문자열에 정렬해 **깨진 위치의 문자만** 갈아끼운다.

    계약(테스트로 잠금):
      1. 반환 길이 == `extracted` 길이. 언제나.
      2. `bad_indices`에 없는 위치의 문자는 절대 바뀌지 않는다.

    이 두 성질이 "조용한 악화 금지"를 코드로 강제한다. PDF가 옳다고 말한
    문자(=깨지지 않은 문자)는 OCR이 뭐라 하든 그대로 두므로, OCR 오독이
    멀쩡한 텍스트를 망칠 수 없다 — 실측 사례: `sc4B.`의 OCR 판독은
    `SC49.`(대문자)지만 대소문자는 깨진 위치가 아니라 추출값 `sc`가
    남고 깨진 숫자만 `9`로 바뀌어 `sc49.`가 된다.

    길이가 같은 replace 구간만 채택한다. 길이가 다른 replace는 어느 문자가
    어느 문자에 대응하는지 알 수 없어, 없던 문자를 끼워 넣을 수 있다
    (실측: 여백을 넓혔을 때 `7Cont.8` → `((Cont.)`처럼 괄호가 하나 늘었다).
    OCR이 문자를 더 봤으면(insert) 무시하고, 덜 봤으면(delete) 추출값을
    남긴다 — 둘 다 "고치지 못했다"로 수렴할 뿐 손상되지 않는다.
    """
    bad = set(bad_indices)
    out: list[str] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(
            None, extracted, ocr_text, autojunk=False).get_opcodes():
        if tag == "insert":
            continue  # OCR이 본 여분 문자는 버린다(추출 길이 보존)
        if tag == "replace" and i2 - i1 == j2 - j1:
            out.append("".join(
                ocr_text[j1 + k] if (i1 + k) in bad else extracted[i1 + k]
                for k in range(i2 - i1)))
        else:  # equal / delete / 길이가 다른 replace → 추출값 유지
            out.append(extracted[i1:i2])
    return "".join(out)


def _ocr_word(arr: np.ndarray, word: CorruptWord) -> tuple[str, float]:
    """단어 bbox를 여백만큼 넓혀 크롭·판독한다 → (판독 텍스트, 최저 신뢰도).
    판독이 없으면 ("", 0.0)."""
    region = (word.bbox[0] - _REPAIR_PAD_X, word.bbox[1] - _REPAIR_PAD_Y,
              word.bbox[2] + _REPAIR_PAD_X, word.bbox[3] + _REPAIR_PAD_Y)
    crop = _crop_region_px(arr, region, _REPAIR_DPI)
    if crop.size == 0:
        return "", 0.0
    try:
        result, _elapse = _get_engine()(crop)
    except Exception:  # 단어 하나의 OCR 실패가 추출 전체를 막지 않게
        logger.exception("text-repair OCR failed for %r", word.text)
        return "", 0.0
    if not result:
        return "", 0.0
    return (" ".join(str(text).strip() for _box, text, _score in result),
            min(float(score) for _box, _text, score in result))


def repair_corrupt_words(doc: PdfDocument, page: int,
                         blocks: list[RawBlock]) -> list[RawBlock]:
    """깨진 추출 문자를 OCR로 복구한 블록 목록을 돌려준다(Task 20).

    깨진 단어가 없으면 `blocks`를 그대로 돌려준다 — 실물 1037페이지 중
    깨진 페이지는 21개뿐이라 절대다수 페이지는 탐지(0.7초/전 문서) 외에
    비용이 없고 렌더조차 하지 않는다.

    킬스위치 YESON_PDF_TEXT_REPAIR=0이면 탐지도 하지 않는다. 백엔드가
    `corrupt_words`를 제공하지 않으면(다른 구현으로 교체된 경우) 조용히
    현행 동작으로 내려간다 — 복구는 부가 기능이지 추출의 전제가 아니다.

    복구는 항상 "고치거나, 그대로 두거나" 둘 중 하나다. OCR이 실패하든
    신뢰도가 낮든 한글/CJK가 섞여 나오든, 최악의 결과는 원래 추출값이
    남는 것이다(사용자 요구: 조용한 악화 금지).
    """
    if not _text_repair_enabled():
        return blocks
    finder = getattr(doc, "corrupt_words", None)
    if finder is None:
        return blocks
    words = finder(page)
    if not words:
        return blocks

    arr = _decode_png(doc.render_png(page, dpi=_REPAIR_DPI))
    # 블록별 (offset, 원본단어, 복구단어) — 오프셋 내림차순으로 적용해
    # 앞쪽 치환이 뒤쪽 오프셋을 흔들 가능성을 원천 차단한다(현 계약상
    # 길이가 보존되므로 실제로 흔들리지 않지만, 계약에 기대지 않는다).
    edits: dict[int, list[tuple[int, str, str]]] = {}
    for word in words:
        if not 0 <= word.block_index < len(blocks):
            logger.warning("text-repair: page %d block_index %d 범위 밖 — 건너뜀",
                           page, word.block_index)
            continue
        block_text = blocks[word.block_index].text
        end = word.offset + len(word.text)
        if block_text[word.offset:end] != word.text:
            # 백엔드의 좌표계와 raw_blocks()가 어긋났다는 뜻 — 엉뚱한
            # 자리를 덮느니 아무것도 하지 않는다.
            logger.warning(
                "text-repair: page %d 오프셋 불일치(기대 %r, 실제 %r) — 건너뜀",
                page, word.text, block_text[word.offset:end])
            continue
        ocr_text, score = _ocr_word(arr, word)
        if not ocr_text:
            logger.info("text-repair: page %d %r 판독 실패 — 원문 유지",
                        page, word.text)
            continue
        if score < _REPAIR_MIN_SCORE:
            logger.info("text-repair: page %d %r 신뢰도 미달(%.2f) — 원문 유지",
                        page, word.text, score)
            continue
        if not ocr_text.isascii():
            # 이 문서군의 원문은 영문이다 — 판독에 한글/CJK가 섞였다면
            # 글자가 아닌 것을 글자로 본 것이다(실측: 패널 그림 일부를
            # 전각 문장부호로 판독). 그런 판독은 통째로 버린다.
            logger.info("text-repair: page %d %r 판독에 비ASCII 포함(%r) — 원문 유지",
                        page, word.text, ocr_text)
            continue
        fixed = _align_repair(word.text, ocr_text, word.bad_indices)
        if fixed == word.text:
            continue
        if _DIGITS.findall(fixed) != _DIGITS.findall(word.text):
            # 사용자 요구가 "특히 숫자는 틀리면 안 된다"이고, 숫자가 바뀌는
            # 복구야말로 이 기능의 존재 이유다(`sc109` → `sc103`). 눈에
            # 보이게 남긴다 — 나중에 오복구가 의심될 때 추적 근거가 된다.
            logger.info(
                "text-repair: page %d 숫자 복구 %r → %r (OCR %r, 신뢰도 %.2f)",
                page, word.text, fixed, ocr_text, score)
        else:
            logger.info("text-repair: page %d 복구 %r → %r", page, word.text, fixed)
        edits.setdefault(word.block_index, []).append(
            (word.offset, word.text, fixed))

    if not edits:
        return blocks
    out = list(blocks)
    for block_index, block_edits in edits.items():
        text = out[block_index].text
        for offset, old, new in sorted(block_edits, reverse=True):
            text = text[:offset] + new + text[offset + len(old):]
        out[block_index] = RawBlock(text=text, bbox=out[block_index].bbox)
    return out
