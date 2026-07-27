"""RapidOCR(onnxruntime) 슬레이트 판독 래퍼.

onnxruntime은 이미 faster-whisper 전이의존 + 번들 --collect-all 대상이라
새 시스템 바이너리가 없다. RapidOCR 초기화(모델 로드)는 비싸므로 프로세스당
1회 지연 생성한다. 슬레이트는 배경 대비 뚜렷한 산세리프라 판독이 쉽다.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from .scene_split import tokenize

logger = logging.getLogger("yeson.video.slate_ocr")

_local = threading.local()


# 검출 입력 크기 상한. RapidOCR 기본값(limit_type=min, 736)은 "짧은 변이 736이
# 되도록" 맞추기 때문에 작은 이미지를 확대한다 — 사용자가 지정한 슬레이트 구역
# (실측 336x63)이 약 3900x736으로 부풀려져 판독이 1014ms까지 걸렸고, 과확대가
# 검출을 망가뜨려 텍스트가 잘리기까지 했다("HH0307_030_0240_AC_V01" → "HH0307_030").
# max 기준은 큰 이미지만 줄이고 작은 이미지는 그대로 둔다 — 크롭 40ms(25배),
# 전체 프레임도 894→675ms이며 판독 결과는 동일하다.
_DET_LIMIT_TYPE = "max"
_DET_LIMIT_SIDE_LEN = 960


def _new_engine(**kwargs):  # test seam
    from rapidocr_onnxruntime import RapidOCR  # 지연 import (번들 무관 경로 보호)
    return RapidOCR(**kwargs)


def _reset_engines() -> None:  # test seam
    global _local
    _local = threading.local()


def _get_engine():
    """RapidOCR 지연 생성 — 스레드마다 자기 엔진.

    정밀화는 경계를 병렬로 처리하므로 한 엔진을 여러 스레드가 동시에 호출하게
    되는데, 래퍼가 호출 중 self에 상태를 두면 서로를 오염시킬 수 있다. 스레드
    로컬로 두면 그 위험 자체가 없어진다(엔진 하나당 모델 로드 ~1초·수십MB).
    import·초기화 실패는 호출자에게 전파한다.
    """
    engine = getattr(_local, "engine", None)
    if engine is None:
        engine = _new_engine(det_limit_type=_DET_LIMIT_TYPE,
                             det_limit_side_len=_DET_LIMIT_SIDE_LEN)
        _local.engine = engine
    return engine


# 슬레이트로 인정하는 상단 밴드(프레임 높이 대비 y중심 비율). 슬레이트는 관례상
# 프레임 상단에 붙고, 하단에는 워터마크·번인 자막이 온다 — 실기(HZBN307)에서
# 하단 워터마크가 토큰 수(6>5)로 슬레이트를 이겨 경계가 전멸한 회귀의 방지선.
_TOP_BAND_FRAC = 0.35


def _candidate_field_count(text: str, delimiters: list[str]) -> int:
    """후보 자격 판정용 필드 수.

    구분자로 센 토큰 수가 기본이지만, OCR이 필드 구분자를 아예 흘려 공백으로
    읽는 쇼가 있다(실기 FL102: 12프레임 표본에서 '_'로 읽힌 적 0회, 공백 10회).
    구분자로만 세면 1토큰이라 후보에서 탈락해 그 쇼 전체가 판독실패가 되므로
    공백 분해도 인정한다 — 단 갈라진 필드가 '전부 숫자를 품을 때'만. 이 단서가
    없으면 "Seq 11B"(공백이 필드 안에 있는 슬레이트)나 "THE END"(타이틀카드)
    같은 텍스트가 슬레이트로 둔갑한다.
    """
    n = len(tokenize(text, delimiters))
    if " " in delimiters:
        return n
    fields = tokenize(text, [*delimiters, " "])
    if len(fields) > n and all(any(c.isdigit() for c in f) for f in fields):
        return len(fields)
    return n


def pick_slate_line(
    lines: list[tuple[str, float, float]], delimiters: list[str],
    min_tokens: int, top_frac: float = _TOP_BAND_FRAC,
) -> str:
    """OCR 라인 후보 중 슬레이트 1줄 선택 — 상단 밴드(y_frac ≤ top_frac) 안에서
    토큰 수(내림차순)·신뢰도(내림차순) 우선. min_tokens 미만으로 쪼개지는 라인과
    밴드 밖 라인(하단 워터마크·자막)은 후보에서 제외한다. 밴드 안에 후보가 없으면
    하단으로 폴백하지 않고 ""(판독실패) — hold_keys가 직전 유효값으로 홀드한다.

    후보 자격은 공백 관용(_candidate_field_count)으로 재지만 순위는 구분자
    토큰 수 그대로다 — 공백으로 잘게 쪼개지는 텍스트가 토큰 수 싸움에서 진짜
    슬레이트를 이기는 회귀(하단 워터마크 6>5로 경계 전멸)를 되풀이하지 않게.
    """
    scored = []
    for text, score, y_frac in lines:
        if y_frac > top_frac:
            continue
        n = len(tokenize(text, delimiters))
        if _candidate_field_count(text, delimiters) >= min_tokens:
            scored.append((n, score, text))
    if not scored:
        return ""
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return scored[0][2]


# 재판독 배율(시도 순서) — 검출기(det max/960)가 원본 해상도의 흐릿한 경계
# 프레임에서 획이 뭉개져 통째로 실패하는 경우가 있다(실기 040_0200 무컷 전환
# 17프레임: 원본 판독 0/17, 0.6× 축소 판독 17/17 — LANCZOS 축소가 실효 획을
# 또렷하게). 반대로 작은 크롭에서는 필드 구분자가 뭉개져 두 필드가 붙어 읽히는데
# (실기 FL102 720s 'FL102J002'), 확대하면 갈라진다('FL102 J002' 12/12). 축소가
# 못 받은 프레임을 확대가 받으므로 둘 다, 싼 쪽(축소) 먼저 시도한다.
_RESCALE_FRACS = (0.6, 2.0)


def read_slate_line_rescaled(
    image_path: str | Path, delimiters: list[str], min_tokens: int = 2,
    top_frac: float = _TOP_BAND_FRAC,
) -> str:
    """이미지 배율을 바꿔 다시 판독 — 원본 해상도 판독이 실패한 프레임의 3차
    시도(1차 타이트 크롭, 2차 패딩 크롭 다음). _RESCALE_FRACS를 차례로 시도해
    처음 읽힌 값을 쓴다. 리스케일본은 원본 옆 임시 파일로 쓰고 지운다.

    검출 상한(_DET_LIMIT_SIDE_LEN)을 넘기는 확대는 건너뛴다 — 검출기가 도로
    줄이므로 결과는 원본 판독과 같고 수천 프레임분 시간만 든다.
    """
    try:
        from PIL import Image  # RapidOCR 전이의존(lock 고정) — 지연 import
        src = Path(image_path)
        with Image.open(src) as im:
            size = (im.width, im.height)
        for i, frac in enumerate(_RESCALE_FRACS):
            if frac > 1 and max(size) * frac > _DET_LIMIT_SIDE_LEN:
                continue
            dst = src.with_name(f"{src.stem}_rs{i}{src.suffix}")
            with Image.open(src) as im:
                im.resize((max(1, int(im.width * frac)),
                           max(1, int(im.height * frac))),
                          Image.LANCZOS).save(dst)
            try:
                text = read_slate_line(dst, delimiters, min_tokens, top_frac)
            finally:
                try:
                    dst.unlink()
                except OSError:
                    pass
            if text:
                return text
        return ""
    except Exception:  # noqa: BLE001 — 한 프레임 재판독 실패가 스캔을 막지 않게
        logger.exception("rescaled OCR failed for %s", image_path)
        return ""


def read_frame_text(image_path: str | Path) -> str:
    """프레임의 모든 OCR 텍스트를 이어붙여 반환 — 슬레이트 한 줄만 고르지 않고
    프레임에 '보이는 모든 라벨'을 본다. 경계 오버랩(디졸브/와이프에서 두 슬레이트가
    동시에 보임) 감지용: 이웃 씬 번호열이 경계 프레임에 나타나는지 부분일치로 확인해,
    한 줄만 고르는 read_slate_line이 자기 슬레이트를 골라 오버랩을 놓치는 문제를 피한다.
    판독 실패는 "" 반환."""
    try:
        result, _elapse = _get_engine()(str(image_path))
        if not result:
            return ""
        return " ".join(str(item[1]) for item in result)
    except Exception:  # noqa: BLE001 — 한 프레임 판독 실패가 검사를 막지 않게
        logger.exception("OCR failed for %s", image_path)
        return ""


def read_slate_line(
    image_path: str | Path, delimiters: list[str], min_tokens: int = 2,
    top_frac: float = _TOP_BAND_FRAC,
) -> str:
    """이미지 한 장 OCR → 슬레이트 라인. 판독 실패/후보 없음은 "" 반환.

    top_frac은 슬레이트로 인정할 상단 밴드 비율. 사용자가 OCR 영역을 지정해
    프레임이 이미 잘려 들어온 경우 1.0을 줘야 한다 — 크롭 자체가 영역 필터라
    잘린 이미지 안에서는 슬레이트가 어디에 있어도 정상이다.
    """
    try:
        result, _elapse = _get_engine()(str(image_path))
        if not result:
            return ""
        from PIL import Image  # RapidOCR 전이의존(lock 고정) — 지연 import
        with Image.open(image_path) as im:
            height = im.height
        # RapidOCR 반환: [[box(4점 [x,y]), text, score], ...]
        lines = []
        for item in result:
            ys = [float(p[1]) for p in item[0]]
            y_frac = (sum(ys) / len(ys)) / height if height else 1.0
            lines.append((item[1], float(item[2]), y_frac))
        return pick_slate_line(lines, delimiters, min_tokens, top_frac)
    except Exception:  # noqa: BLE001 — 한 프레임 판독 실패가 전체 스캔을 막지 않게
        logger.exception("OCR failed for %s", image_path)
        return ""
