"""손글씨 전사 — 비전 CLI(기본 agy)로 크롭 이미지를 영문 텍스트로.

⛔Gemini API는 쓰지 않는다(2026-08-20 사용자 확정, 비용). RapidOCR이
손글씨 판독을 못 하는 실측(SUBTLE→SUBnE) 때문에 존재하는 모듈이다 —
xsheet 프로파일이 위치를 찾은 노트 크롭을 배치로 agy 헤드리스에 보내
JSON 매핑(파일명→전사)을 받는다. 실측(2026-08-20, 표본 152크롭): agy
전사는 Gemini API와 동일 출력, 실노트 정확도 ~90%, 마커·셀번호는 빈값
스킵(원하는 동작 — 사람도 번역하지 않는다).

운영 전제: agy 헤드리스가 이미지 파일을 읽으려면 read_file 권한 허용이
선행돼야 한다(없으면 자동 거부 → 배치 실패로 표면화). 서버 기계의
`~/.gemini/antigravity-cli/settings.json`의 permissions.allow 또는
trustedWorkspaces에 잡 폴더를 등록하는 게 정석이고, 신뢰 환경 한정으로
`YESON_PDF_XSHEET_CLI_ARGS=--dangerously-skip-permissions`도 가능하다 —
단 크롭 이미지 안 텍스트가 프롬프트 주입이 될 수 있으므로(에이전트가
지시로 오독) 전역 자동승인은 기본값이 아니다.

전사 결과는 잡 폴더 `transcripts.json`에 배치마다 저장한다 — 재번역
(retranslate)이 파이프라인을 다시 돌려도 CLI를 재호출하지 않고 캐시로
이어받는다(크롭 이름이 페이지+좌표 기반이라 재추출에도 안정).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from .profiles.base import PdfBlock

if TYPE_CHECKING:
    from .backend import PdfDocument

logger = logging.getLogger("yeson.pdf_translate")


class TranscribeFatalError(RuntimeError):
    """재시도가 무의미한 CLI 거절(쿼터 소진·미로그인). 메시지가 그대로
    잡 오류로 노출되므로 사람이 읽고 조치할 수 있는 문장이어야 한다."""

ENV_CLI = "YESON_PDF_XSHEET_CLI"            # 환경변수 오버라이드(운영용)
# 이미지를 읽을 수 있고 실측으로 검증된 CLI. 잡이 고른 번역 엔진이 여기
# 있으면 전사도 그 엔진으로 한다 — 사용자는 화면의 엔진 하나만 고르는데
# 전사가 딴 엔진을 쓰면 "클로드 골랐는데 왜 agy 권한 오류냐"가 된다.
# gemini는 API라 제외(⛔비용, 2026-08-20 확정), apple/qwen은 이미지 입력 불가.
VISION_CLIS = ("claude", "agy")
ENV_EXTRA_ARGS = "YESON_PDF_XSHEET_CLI_ARGS"  # shlex 분해되어 argv 뒤에 붙는다
ENV_WORKERS = "YESON_PDF_XSHEET_CLI_WORKERS"  # 동시 CLI 세션 수(기본 3)
ENV_EFFORT = "YESON_PDF_XSHEET_CLI_EFFORT"    # 사고 깊이(기본 medium — 아래 근거)
_EFFORTS = ("low", "medium", "high", "xhigh", "max")
# Claude Code의 기본 effort는 **xhigh**(위에서 두 번째)다 — 코딩·에이전트
# 작업에 맞춘 값이고, "이미지 한 장 열어 손글씨 옮겨적기"에는 과하다. 실측
# (2026-08-26, A1 세션 해부): **청구 출력의 98%가 로그에 안 보이는 사고
# 토큰**이었고(턴당 중앙 1,908), 출력은 전사 비용의 **52%**($403/$777)였다.
# A/B(A2 크롭 120장 동일 집합·팔마다 폴더 분리·배치 60):
#     base(xhigh) 출력 50,316 · 666초 · 빈값 24 · 정확일치 69
#     medium      출력 22,375 · 300초 · 빈값 18 · 정확일치 69
#     low         출력 12,290 · 146초 · 빈값  6 · 정확일치 63
# medium은 **측정한 모든 축에서 base를 지배**한다(출력 −56%·시간 −55%·빈값
# −6·일치 ±0). 게다가 정답지가 base 설정으로 만든 것이라 base에 유리하게
# 편향됐는데도 동점이다. low는 빈값이 24→6으로 더 좋아지지만 판독 **내용**이
# 달라진다(읽어낸 것만 보면 일치율 71.9%→55.3%) — 사람 눈 검증 전까지 보류.
# ⚠턴 수는 134→122로 거의 안 줄었다. 절감은 도구 호출 뭉침이 아니라 **순수
# 사고 깊이**에서 나온다.
_EFFORT_DEFAULT = "medium"
ENV_MODEL = "YESON_PDF_XSHEET_CLI_MODEL"      # claude 전사 모델(운영 오버라이드)
# ⚠고정하지 않으면 헤드리스 `claude -p`가 **사용자의 인터랙티브 /model 기본값**
# 을 상속한다 — 대화 세션에서 모델을 바꾸는 순간 파이프라인 단가·거동이 조용히
# 따라 바뀐다(2026-08-28 실사고 직전: 기본값이 Fable 5로 저장됨 = 토큰당 2배).
# 지금까지의 실측 기준(effort A/B·엔진 A/B·A1 비용 해부 $777)은 전부 opus
# 등급($5/$25)에서 잡은 것이라 그 등급에 고정한다. agy는 제 기본 모델을
# 그대로 쓴다(claude 모델명이 통하지 않고, 실측도 기본 모델로 했다).
_MODEL_DEFAULT = "opus"


def _model() -> str:
    """전사 claude CLI에 줄 모델. 모델명은 열린 집합이라 effort처럼 오타를
    거를 수 없다 — 틀린 값은 첫 배치가 인자 오류로 즉사해 _MIN_ANSWERED
    안전망이 잡는다(빈 값만 기본값으로)."""
    return os.environ.get(ENV_MODEL, "").strip() or _MODEL_DEFAULT


def _effort() -> str:
    """전사 CLI에 줄 effort. 잘못된 값은 조용히 무시하고 기본값으로 — 오타
    하나로 문서당 3시간짜리 잡이 인자 오류로 즉사하면 안 된다."""
    v = os.environ.get(ENV_EFFORT, "").strip().lower()
    return v if v in _EFFORTS else _EFFORT_DEFAULT


def _workers() -> int:
    try:
        n = int(os.environ.get(ENV_WORKERS, "3"))
    except ValueError:
        n = 3
    return max(1, min(n, 8))

_CROP_DPI = 300      # 원본 스캔 해상도와 동일 — 전사 품질 실측 기준
_MARGIN_PT = 5.0     # 기본 여백 — 잘린 크롭만 _expand_to_ink가 더 넓힌다
_MAX_GROW_PX = 150      # 상자를 넓힐 수 있는 최대 거리(변당) ≈36pt
# 글자 덩어리 판정 — 화살표를 글자와 갈라내는 기준. 이 시트의 노트는
# 프레임을 가리키는 긴 곡선 화살표와 획이 이어져 있어서, 덩어리를 무조건
# 통째로 담으면 화살표를 따라 상한까지 부푼다(실측 87%가 상한 초과, 면적
# 2.2배). 글자는 짧고 촘촘하고, 화살표는 길고 성기다.
_MIN_INK_SIDE = 6       # 이보다 얇으면 점·스캔 잡티
_MAX_TEXT_SIDE = 330    # 300dpi에서 ≈80pt — 이보다 긴 획은 화살표로 본다
_MIN_INK_FILL = 0.12    # 외곽상자 대비 채움 — 성긴 곡선(화살표) 배제
_LINE_RATIO = 0.75      # 이 비율 이상 채우는 행/열 = 시트 인쇄 괘선
# 배치 크기 = **구독 쿼터의 주된 소비 단위**. CLI 세션 1개당 쿼터가 깎이므로
# (A1 전량 실측에서 20장 배치 235세션이 개인 쿼터를 소진시켜 전사가 8%에서
# 멈췄다) 세션 수를 줄이는 게 최우선이다. 20 → 60으로 올리면 세션이 1/3로
# 줄고, 커진 배치가 타임아웃 나도 아래 반토막 재시도가 회수한다.
_BATCH = 60
_SPLIT_MIN = 8       # 이 크기 이상의 실패 배치만 반으로 나눠 재시도
_CALL_TIMEOUT = 900  # 배치 하나당 상한(초) — 배치를 키운 만큼 함께 늘린다
# 응답을 하나도 못 받은 크롭 비율이 이보다 크면 잡을 실패시킨다. 정상 런은
# 쓰레기 크롭도 빈 문자열로 **응답은** 받으므로 1.0에 가깝다 — 0.8을 밑돈다는
# 건 CLI가 조직적으로 죽고 있다는 뜻이고, 그대로 두면 노트 대부분이 빠진
# PDF가 조용히 '완료'로 나간다(A1 실측에서 실제로 그럴 뻔했다).
_MIN_ANSWERED = 0.8
# 세션이 실패가 아니라 **치명적 거절**을 돌려준 경우(쿼터·인증·요금제).
# 이때는 쪼개서 재시도해도 전부 같은 거절이라 큐만 태운다 — 즉시 중단한다.
_FATAL_RE = re.compile(
    r"quota reached|upgrade your subscription|rate.?limit|not (?:authenticated|logged in)"
    r"|please (?:log ?in|authenticate)"
    # 헤드리스 권한 거부 — agy는 프롬프트를 띄울 수 없어 read_file을 자동
    # 거부한다(실측: 신뢰 워크스페이스로 등록해도 열리지 않는다). 쪼개서
    # 재시도해도 전부 같은 거부라 큐만 태운다.
    r"|permission check failed|denied permission|permission denied",
    re.IGNORECASE)
_CROPS_DIRNAME = "xsheet_crops"
_CACHE_NAME = "transcripts.json"

# 빈 전사("") 재판독: 원본 크롭의 2배 확대본으로 딱 한 번 더 묻는다.
# A2 실측(2026-08-24): 빈 전사 304장 중 114장이 사람이 번역한 노트 자리 —
# 사람 눈에는 읽히는(`OVS`·`STL`·`UP`) 작은 파편이 대부분이었다. 슬레이트
# 2×/0.6× 재판독과 같은 계보(원본 0/17→축소 17/17 선례).
_RETRY_PREFIX = "2x_"
_RETRY_SCALE = 2
# 전사에서 살아남는 기준: 영문 단어(2자+)가 하나라도 있어야 번역할 거리가
# 있다 — 셀 번호·서클 마커·화살표는 빈값/숫자만 나와 여기서 떨어진다.
# (refine_ko의 [A-Za-z]{2,} 기준과 같은 근거: FL104 사람 주석 실측)
_USABLE = re.compile(r"[A-Za-z]{2,}")

# Windows에서 서버가 CLI를 띄울 때 콘솔 창 번쩍임 방지(translate_cli 미러)
_NO_WINDOW = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {})


def crop_name(block: PdfBlock) -> str:
    """블록 → 크롭 파일명. 인덱스가 아니라 (페이지, 좌표) 기반이라 재추출
    후에도 같은 노트는 같은 이름 — transcripts.json 캐시가 런을 넘어 산다."""
    x0, y0 = block.bbox[0], block.bbox[1]
    return f"p{block.page + 1:03d}_{int(x0)}_{int(y0)}.png"


def crop_rect(arr, bbox: tuple[float, float, float, float],
              ) -> tuple[int, int, int, int] | None:
    """블록 bbox → **실제로 전사에 들어가는** 300dpi 픽셀 사각형.

    여백 `_MARGIN_PT` + `_expand_to_ink`. 크롭을 굽는 쪽(`render_crops`)과
    경계 절단을 되찾는 쪽(`xsheet._absorb_cut_ink`)이 **같은 사각형**을 봐야
    "이 잉크에 주인이 있나" 판정이 성립하므로 산식을 한 군데 둔다. 상자가
    뒤집히면 None(크롭을 만들 수 없는 블록)."""
    h, w = arr.shape[:2]
    scale = _CROP_DPI / 72.0
    x0, y0, x1, y1 = bbox
    px0 = max(int((x0 - _MARGIN_PT) * scale), 0)
    py0 = max(int((y0 - _MARGIN_PT) * scale), 0)
    px1 = min(int((x1 + _MARGIN_PT) * scale), w)
    py1 = min(int((y1 + _MARGIN_PT) * scale), h)
    if px1 <= px0 or py1 <= py0:
        return None
    return _expand_to_ink(arr, px0, py0, px1, py1)


def render_crops(doc: PdfDocument, blocks: list[PdfBlock],
                 job_dir: Path) -> None:
    """블록 크롭 PNG를 잡 폴더에 렌더한다 — doc 락 안에서 불리는 빠른
    단계(페이지당 렌더 1회 + PIL 크롭). 느린 CLI 호출은 transcribe가
    락 없이 수행한다."""
    from PIL import Image

    from .profiles.xsheet import _decode_png  # 지연 import(순환 방지)

    crops = job_dir / _CROPS_DIRNAME
    crops.mkdir(parents=True, exist_ok=True)
    by_page: dict[int, list[PdfBlock]] = {}
    for b in blocks:
        by_page.setdefault(b.page, []).append(b)
    import numpy as np

    rects = _load_crop_rects(job_dir)
    stale: list[str] = []
    touched = False
    for page, page_blocks in by_page.items():
        # 이 페이지가 **지난번과 같은 블록 집합**인가. 파일 존재만 보면 안
        # 된다 — 크롭 이름이 (페이지, x0, y0)뿐이라 블록이 쪼개져 같은 이름에
        # 다른 범위가 되면 파일은 그대로 있는 채로 내용만 낡는다.
        fresh = all((crops / crop_name(b)).exists()
                    and rects.get(crop_name(b)) == _rect_key(b)
                    for b in page_blocks)
        if fresh:
            # 이 페이지 크롭이 이미 다 있으면 렌더 자체를 건너뛴다. 300dpi
            # 페이지 렌더는 장당 수 초라, 빠뜨리면 재개·재번역 런이 아무것도
            # 새로 만들지 않으면서 문서당 10분 넘게 태운다(A1 전량 실측:
            # 188페이지 재렌더). 예전엔 렌더를 먼저 하고 크롭 단위로만
            # exists()를 봤다.
            continue
        arr = _decode_png(doc.render_png(page, dpi=_CROP_DPI, annots=False))
        # ★이 페이지는 **전부** 다시 굽는다(비싼 건 위의 페이지 렌더 한 번이고
        # 크롭 자르기는 밀리초다). 이유: 크롭 이름이 (페이지, x0, y0)뿐이라
        # 블록이 쪼개져 **같은 이름에 다른 범위**가 되면 옛 크롭·옛 전사가
        # 조용히 재사용된다 — 그러면 지금은 이웃 노트의 것이 된 낱말이 이
        # 노트의 번역에 섞인다. A2 밀집 10페이지 실측: 클러스터 규칙을 고치자
        # 302블록 중 6건(2%)이 그런 충돌이었다.
        for b in page_blocks:
            rect = crop_rect(arr, b.bbox)
            if rect is None:
                continue
            px0, py0, px1, py1 = rect
            sub = arr[py0:py1, px0:px1]
            name = crop_name(b)
            path = crops / name
            same = False
            if path.exists():
                try:
                    same = np.array_equal(np.asarray(Image.open(path)), sub)
                except OSError:
                    same = False
                if not same:
                    # 같은 이름인데 그림이 달라졌다 = 범위가 바뀐 블록
                    stale.append(name)
            if not same:
                Image.fromarray(sub).save(path)
            rects[name] = _rect_key(b)
            touched = True
    if touched:
        _save_crop_rects(job_dir, rects)
    _drop_stale_transcripts(job_dir, stale)


_RECTS_NAME = "crop_rects.json"


def _rect_key(block: PdfBlock) -> list[float]:
    return [round(v, 1) for v in block.bbox]


def _load_crop_rects(job_dir: Path) -> dict[str, list[float]]:
    """크롭 이름 → 그 크롭을 만든 블록 bbox. 없으면 빈 값(옛 잡 호환).

    빈 값이면 첫 런에서 모든 페이지를 한 번 다시 굽는다 — CPU만 쓰고 토큰은
    안 쓴다(그림이 같으면 전사 캐시를 그대로 둔다)."""
    path = job_dir / _RECTS_NAME
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_crop_rects(job_dir: Path, rects: dict[str, list[float]]) -> None:
    try:
        (job_dir / _RECTS_NAME).write_text(
            json.dumps(rects, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        logger.warning("xsheet-transcribe: 크롭 범위 기록 실패: %s", exc)


def _drop_stale_transcripts(job_dir: Path, stale: list[str]) -> None:
    """범위가 바뀐 크롭의 전사 캐시를 지운다 — 이름은 같지만 그림이 다르다.

    렌더 단계가 이걸 아는 유일한 지점이라 여기서 지운다(전사 단계는 옛 크롭이
    어떤 그림이었는지 알 길이 없다). 지운 만큼만 다시 읽으므로 비용은
    바뀐 블록 수에 비례한다."""
    if not stale:
        return
    cache_path = job_dir / _CACHE_NAME
    if not cache_path.exists():
        return
    try:
        done = json.loads(cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    dropped = [n for n in stale if done.pop(n, None) is not None]
    for n in stale:                       # 2배 확대 재판독본도 함께
        done.pop(_RETRY_PREFIX + n, None)
    if dropped:
        cache_path.write_text(json.dumps(done, ensure_ascii=False, indent=1),
                              encoding="utf-8")
        logger.info("xsheet-transcribe: 범위가 바뀐 크롭 %d건의 전사 캐시를 "
                    "버렸다", len(dropped))


_STRIP_DPI = 200      # 화자 스트립 렌더 — 세로로 길어 300은 과하다
_STRIP_BATCH = 20     # 스트립은 크므로 배치를 작게


def render_strips(doc: PdfDocument, strips: list[PdfBlock],
                  job_dir: Path) -> None:
    """화자 스트립 크롭 렌더(잉크 확장 없이 bbox 그대로) — 이름·위치는
    비전 CLI가 통째로 읽는다(scan_speaker_strips). doc 락 단계."""
    if not strips:
        return
    from PIL import Image

    from .profiles.xsheet import _decode_png  # 지연 import(순환 방지)

    crops = job_dir / _CROPS_DIRNAME
    crops.mkdir(parents=True, exist_ok=True)
    scale = _STRIP_DPI / 72.0
    for b in strips:
        dest = crops / crop_name(b)
        if dest.exists():
            continue
        arr = _decode_png(doc.render_png(b.page, dpi=_STRIP_DPI, annots=False))
        h, w = arr.shape[:2]
        x0, y0, x1, y1 = b.bbox
        px0, py0 = max(int(x0 * scale), 0), max(int(y0 * scale), 0)
        px1, py1 = min(int(x1 * scale), w), min(int(y1 * scale), h)
        if px1 <= px0 or py1 <= py0:
            continue
        Image.fromarray(arr[py0:py1, px0:px1]).save(dest)


def _build_strip_prompt(batch: list[str]) -> str:
    return (
        "Each PNG file in this directory is a tall vertical strip cut from "
        "the dialog-column area of an animation exposure sheet. Find every "
        "handwritten character NAME (often written inside a drawn pencil "
        "circle, e.g. HANK, DALE, SAUDI GUY) and any circled production "
        "note. Do NOT list lip-sync phonetic letters (EE, OH, AH, HU...), "
        "frame numbers, timing lines, or printed text. Read each file "
        "directly with your file-reading tool; do NOT run shell commands. "
        "Reply ONLY as a JSON object mapping each filename to an array of "
        "{\"text\": \"...\", \"y\": <0..1 fraction of the item's vertical "
        "position from the top of that strip>}; use [] when none. "
        "Files: " + ", ".join(batch)
    )


def scan_speaker_strips(strips: list[PdfBlock], job_dir: Path, *,
                        engine: str | None = None) -> dict[str, list]:
    """스트립들을 비전 CLI로 스캔해 {크롭명: [{text, y}, ...]}를 돌려준다.

    결과는 transcripts.json에 **JSON 문자열 값**으로 캐시한다 — 기존
    캐시 로더가 str만 남기므로 형식이 살아남고, 재번역이 재스캔 비용을
    내지 않는다. 실패한 배치는 빈 결과로 둔다(다음 런이 다시 시도)."""
    crops = job_dir / _CROPS_DIRNAME
    cache_path = job_dir / _CACHE_NAME
    done: dict[str, str] = {}
    if cache_path.exists():
        try:
            done = {k: v for k, v in json.loads(
                cache_path.read_text(encoding="utf-8")).items()
                if isinstance(v, str)}
        except (json.JSONDecodeError, OSError):
            done = {}
    names = [crop_name(b) for b in strips]
    todo = [n for n in names if n not in done and (crops / n).exists()]
    changed = False
    for i in range(0, len(todo), _STRIP_BATCH):
        batch = todo[i:i + _STRIP_BATCH]
        try:
            parsed = _extract_json_object(
                _run_cli(_build_strip_prompt(batch), crops, engine))
        except TranscribeFatalError:
            raise
        except Exception as exc:  # noqa: BLE001 — 배치 실패는 다음 런 몫
            logger.warning("xsheet-strip: 스캔 배치 실패(%s): %s",
                           batch[0], exc)
            continue
        for k, v in parsed.items():
            if k in batch and isinstance(v, list):
                done[k] = json.dumps(v, ensure_ascii=False)
                changed = True
    if changed:
        cache_path.write_text(json.dumps(done, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    out: dict[str, list] = {}
    for n in names:
        v = done.get(n, "")
        parsed_list: list = []
        if isinstance(v, str) and v.lstrip().startswith("["):
            try:
                loaded = json.loads(v)
                if isinstance(loaded, list):
                    parsed_list = [x for x in loaded if isinstance(x, dict)]
            except ValueError:
                pass
        out[n] = parsed_list
    return out


def _expand_to_ink(arr, px0: int, py0: int, px1: int,
                   py1: int) -> tuple[int, int, int, int]:
    """상자에 걸친 **잉크 덩어리를 통째로** 담도록 넓힌 상자를 돌려준다.

    ⛔손글씨가 잘린 크롭은 번역 이전에 원문이 사라진 것이라 절대 허용하지
    않는다(사용자 확정 2026-08-20). RapidOCR 줄 상자는 글리프에 딱 붙는 데다
    흐린 줄은 짧게 잡혀, 고정 여백 5pt로는 마지막 줄이 잘린다(실측:
    `CONT, TREMBLE CYCLE` → `TREMBLE`, 작은 크롭 20장 중 5장이 아랫줄 잘림).

    변에 잉크가 닿았는지로 한 픽셀씩 넓히는 방식은 실패했다 — 시트 인쇄
    괘선과 그 흐린 경계가 늘 '글자 걸침'으로 읽혀, 300장 중 44%가 좌우
    상한까지 부풀고도(면적 1.35배) 여전히 잘림 판정이었다. 덩어리(연결
    성분) 단위로 보면 "내 노트의 획"과 "옆 노트·괘선"이 구조적으로 갈린다.
    """
    return ink_bounds(arr, px0, py0, px1, py1)[0]


def ink_bounds(arr, px0: int, py0: int, px1: int, py1: int,
               ) -> tuple[tuple[int, int, int, int], bool]:
    """(잉크 덩어리를 포함하도록 넓힌 상자, 상한에 걸려 잘림이 남았나).

    상자와 **겹치는** 덩어리만 내 노트의 획으로 본다 — 옆 노트는 별개
    성분이라 겹치지 않아 자연히 배제된다. 인쇄 괘선은 미리 지운다(남기면
    페이지 전체가 한 덩어리로 이어져 판정이 무의미해진다)."""
    import cv2
    import numpy as np

    h, w = arr.shape[:2]
    nx0, ny0 = max(px0 - _MAX_GROW_PX, 0), max(py0 - _MAX_GROW_PX, 0)
    nx1, ny1 = min(px1 + _MAX_GROW_PX, w), min(py1 + _MAX_GROW_PX, h)
    region = _dark(arr[ny0:ny1, nx0:nx1])
    if region.size == 0:
        return (px0, py0, px1, py1), False
    ink = region.astype(np.uint8)
    ink[region.mean(axis=1) >= _LINE_RATIO, :] = 0   # 가로 프레임 줄
    ink[:, region.mean(axis=0) >= _LINE_RATIO] = 0   # 세로 칸 구분선

    n, _labels, stats, _c = cv2.connectedComponentsWithStats(ink, connectivity=8)
    ox0, oy0, ox1, oy1 = px0 - nx0, py0 - ny0, px1 - nx0, py1 - ny0
    bx0, by0, bx1, by1 = ox0, oy0, ox1, oy1
    outside = False
    for i in range(1, n):
        x, y, cw, ch, area = (int(v) for v in stats[i])
        if not _is_textlike(cw, ch, area):
            continue
        if x >= ox1 or x + cw <= ox0 or y >= oy1 or y + ch <= oy0:
            continue
        bx0, by0 = min(bx0, x), min(by0, y)
        bx1, by1 = max(bx1, x + cw), max(by1, y + ch)
        # 덩어리가 탐색 구역 끝까지 이어지면(=상한에 닿음) 잘림이 남는다
        if x <= 0 or y <= 0 or x + cw >= nx1 - nx0 or y + ch >= ny1 - ny0:
            outside = True
    bx0, by0 = max(bx0, 0), max(by0, 0)
    bx1, by1 = min(bx1, nx1 - nx0), min(by1, ny1 - ny0)
    return (nx0 + bx0, ny0 + by0, nx0 + bx1, ny0 + by1), outside


def _is_textlike(cw: int, ch: int, area: int) -> bool:
    """이 덩어리가 **글자**인가(화살표·잡티가 아니라).

    실측(200장): 이 필터 없이 덩어리를 통째로 담으면 내 노트 글자 잘림은
    2.0%까지 떨어지지만 화살표를 따라 상자가 상한까지 부푼다. 필터를 넣으면
    같은 2.0%를 면적 1.17배로 얻는다. 여백만 키우는 대안은 오히려 나빠진다
    (20pt에서 76% — 상자가 커지며 옆 노트 글자를 반쯤 물기 때문)."""
    if cw < _MIN_INK_SIDE or ch < _MIN_INK_SIDE:
        return False
    if max(cw, ch) > _MAX_TEXT_SIDE:
        return False
    return area / float(cw * ch) >= _MIN_INK_FILL


def _dark(arr):
    """RGB든 그레이든 '검은 픽셀' 불리언 배열로."""
    return (arr.min(axis=-1) if arr.ndim == 3 else arr) < 128


def _render_retry(crops: Path, name: str) -> str | None:
    """빈 전사 크롭의 확대본을 만들고 그 파일명을 돌려준다(실패 시 None —
    손상 크롭 한 장이 전사 전체를 죽이면 안 된다)."""
    from PIL import Image
    try:
        with Image.open(crops / name) as im:
            up = im.resize((im.width * _RETRY_SCALE, im.height * _RETRY_SCALE),
                           Image.LANCZOS)
        dest = crops / (_RETRY_PREFIX + name)
        up.save(dest)
        return dest.name
    except Exception as exc:  # noqa: BLE001
        logger.warning("xsheet-transcribe: 재판독 확대 실패(%s): %s", name, exc)
        return None


def transcribe(blocks: list[PdfBlock], job_dir: Path, *,
               should_continue: Callable[[], bool] | None = None,
               on_progress: Callable[[float], None] | None = None,
               engine: str | None = None) -> list[PdfBlock]:
    """크롭들을 배치 전사해 블록 text를 교체하고, 번역할 거리가 없는
    블록(마커·숫자·판독 불가)은 버린다. 취소가 감지되면
    asyncio.CancelledError를 던진다(pdf_run의 on_progress와 같은 규약).
    on_progress에는 전체 크롭 대비 전사 완료 비율(0~1)이 배치마다 온다 —
    캐시로 건너뛴 몫도 분자에 포함해 재개 런의 진행률이 이어져 보인다."""
    crops = job_dir / _CROPS_DIRNAME
    cache_path = job_dir / _CACHE_NAME
    done: dict[str, str] = {}
    if cache_path.exists():
        try:
            done = {k: v for k, v in json.loads(
                cache_path.read_text(encoding="utf-8")).items()
                if isinstance(v, str)}
        except (json.JSONDecodeError, OSError):
            logger.warning("xsheet-transcribe: 캐시 파싱 실패 — 새로 시작")

    names = [crop_name(b) for b in blocks]
    all_names = {n for n in names if (crops / n).exists()}
    todo = sorted(n for n in all_names if n not in done)

    # 빈 전사 재판독 준비 — 이전 런이 캐시에 남긴 ""도 대상이다(이 경로가
    # 없으면 실물 잡의 빈 전사는 재번역을 해도 영원히 안 읽힌다). 같은 런
    # 안에서는 이름당 한 번만 — retried가 막는다.
    retried: set[str] = set()
    retry_ready: list[str] = []

    def _queue_retry(name: str) -> None:
        if name in retried:
            return
        retried.add(name)
        up = _render_retry(crops, name)
        if up is not None:
            retry_ready.append(up)

    for n in sorted(all_names):
        if n in done and not (done[n] or "").strip():
            _queue_retry(n)
    # 동시 워커 + 실패 배치 반토막 재시도.
    #
    # 동시성: A1 전량 실측(2026-08-20)에서 크롭이 4,700장(=배치 235개)
    # 나왔다 — agy 세션이 배치당 1~2분이라 직렬이면 전사만 4시간+.
    # 배치끼리는 완전 독립(서로 다른 파일)이라 CLI 세션 몇 개를 나란히
    # 띄우는 게 안전한 지름길이다. 워커 결과 병합·캐시 쓰기는 메인
    # 스레드만 한다(락 불필요).
    #
    # 반토막 재시도: 같은 실측에서 노트 53개짜리 밀집 페이지(p182)의
    # 20장 배치 2개가 600초 타임아웃으로 통째 죽었다(나머지 15배치 전부
    # 성공). agy는 배치가 클수록 세션이 길어지므로 쪼개면 대부분
    # 회복된다. 반토막은 크기가 단조 감소해 _SPLIT_MIN 미만에서 종결 —
    # 무한 재시도가 불가능하다.
    from collections import deque
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

    queue = deque(todo[i:i + _BATCH] for i in range(0, len(todo), _BATCH))
    while retry_ready:                      # 캐시의 빈 전사 몫 재판독 배치
        queue.append(retry_ready[:_BATCH])
        del retry_ready[:_BATCH]
    failed_batches = 0
    workers = _workers()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures: dict = {}

        def _pump() -> None:
            while queue and len(futures) < workers:
                b = queue.popleft()
                futures[ex.submit(_run_cli, _build_prompt(b), crops, engine)] = b

        while queue or futures:
            # 취소 검사는 반드시 제출(_pump)보다 먼저 — 취소가 이미 도착한
            # 상태에서 CLI를 한 번이라도 더 띄우면 안 된다. 도는 중인 CLI는
            # 제 타임아웃까지 알아서 끝나고 결과는 버려진다.
            if should_continue is not None and not should_continue():
                ex.shutdown(wait=False, cancel_futures=True)
                raise asyncio.CancelledError
            _pump()
            finished, _ = wait(set(futures), timeout=5.0,
                               return_when=FIRST_COMPLETED)
            for fut in finished:
                batch = futures.pop(fut)
                try:
                    parsed = _extract_json_object(fut.result())
                    for k, v in parsed.items():
                        if k not in batch or not isinstance(v, str):
                            continue
                        if k.startswith(_RETRY_PREFIX):
                            # 확대 재판독은 **읽어냈을 때만** 채택 — 빈값이면
                            # 원래의 "" 기록이 남는다(같은 런 재시도 없음).
                            orig = k[len(_RETRY_PREFIX):]
                            if orig in all_names and v.strip():
                                done[orig] = v
                            continue
                        done[k] = v
                        if not v.strip():
                            _queue_retry(k)
                except TranscribeFatalError:
                    # 쿼터·인증 거절은 재시도가 무의미 — 남은 세션을 접고
                    # 그대로 올린다. 여기까지의 전사는 캐시에 남아 있어
                    # (쿼터 회복 후) 재번역이 이어받는다.
                    ex.shutdown(wait=False, cancel_futures=True)
                    cache_path.write_text(
                        json.dumps(done, ensure_ascii=False, indent=1),
                        encoding="utf-8")
                    raise
                except Exception as exc:  # noqa: BLE001 — 배치 하나 실패로 전체를 죽이지 않는다
                    if len(batch) >= _SPLIT_MIN:
                        mid = len(batch) // 2
                        queue.append(batch[:mid])
                        queue.append(batch[mid:])
                        logger.warning(
                            "xsheet-transcribe: 배치 실패(%s, %d장) — "
                            "반으로 나눠 재시도: %s", batch[0], len(batch), exc)
                    else:
                        failed_batches += 1
                        logger.warning("xsheet-transcribe: 배치 실패(%s): %s",
                                       batch[0], exc)
            while retry_ready:              # 이번 런에서 새로 나온 빈 전사 몫
                queue.append(retry_ready[:_BATCH])
                del retry_ready[:_BATCH]
            if finished:
                cache_path.write_text(
                    json.dumps(done, ensure_ascii=False, indent=1),
                    encoding="utf-8")
                if on_progress is not None and all_names:
                    on_progress(
                        sum(1 for n in all_names if n in done) / len(all_names))
            _pump()
    for n in retried:                       # 임시 확대본 정리(캐시 키는 원본명)
        (crops / (_RETRY_PREFIX + n)).unlink(missing_ok=True)
    if failed_batches:
        logger.warning("xsheet-transcribe: %d개 배치 실패 — 해당 노트는 "
                       "주석이 빠진다(편집기 수동 추가 대상)", failed_batches)
    # 조직적 실패 안전망: 응답 자체를 못 받은 크롭이 너무 많으면 반쪽짜리
    # 결과를 done으로 흘리지 않는다(캐시는 남으므로 재번역이 이어받는다).
    answered = sum(1 for n in all_names if n in done)
    if all_names and answered / len(all_names) < _MIN_ANSWERED:
        raise RuntimeError(
            f"손글씨 판독이 대부분 실패했습니다 ({answered}/{len(all_names)}장만 "
            "응답) — 전사 CLI 상태(쿼터·로그인)를 확인한 뒤 재번역하세요")

    out: list[PdfBlock] = []
    for b, name in zip(blocks, names):
        text = (done.get(name) or "").strip()
        if _USABLE.search(text):
            out.append(replace(b, text=text))
    return out


def _pick_cli(engine: str | None) -> str:
    """전사에 쓸 CLI: 환경변수 > 잡이 고른 엔진(비전 가능할 때) > agy."""
    override = os.environ.get(ENV_CLI, "").strip()
    if override:
        return override
    if engine and engine.strip().lower() in VISION_CLIS:
        return engine.strip().lower()
    return "agy"


def _argv_for(name: str, path: str, prompt: str) -> list[str]:
    """CLI별 호출 형태 — 플래그가 서로 다르다(translate_cli._BACKENDS와 같은
    이유로 표를 둔다). `--print-timeout`은 agy 전용이라 claude에 넘기면
    인자 오류로 즉사한다.

    실측(2026-08-20): agy·claude 모두 `--add-dir`로 준 폴더의 PNG를 읽고
    JSON으로 답한다. **claude는 권한 플래그 없이도 이미지를 읽는다**(읽기
    전용 도구는 기본 승인) — agy만 read_file 허용 설정이 선행돼야 한다.
    codex는 아직 미실측이라 목록에 넣지 않는다."""
    if name == "codex":  # 미실측 경로 — 형태만 맞춰 둔다(translate_cli 미러)
        return [path, "exec", "--skip-git-repo-check", prompt]
    argv = [path, "-p", prompt, "--add-dir", "."]
    if name == "agy":
        argv += ["--print-timeout", "8m"]
    if name == "claude":
        # agy에는 이 플래그들이 없다 — 넘기면 인자 오류로 즉사한다
        # (`--print-timeout`을 claude에 넘길 수 없는 것과 같은 이유).
        argv += ["--model", _model(), "--effort", _effort()]
    return argv


def _build_prompt(batch: list[str]) -> str:
    # "셸 명령 금지" 지시는 헤드리스 권한 방어다 — agy 1.1.17이 파일을 읽기
    # 전에 `find`·`pwd`부터 실행하려다 권한 거부로 즉사하는 것을 실측
    # (2026-08-24, 08-21 런은 정상이었으니 CLI 업데이트로 인한 행동 드리프트).
    # read_file 허용 규칙과 함께 걸어야 한다(둘 중 하나만으론 불충분).
    return (
        "Open each of these PNG files in this directory and transcribe the "
        "handwritten all-caps English text in each, exactly as written. They "
        "are director notes from animation exposure sheets; a crop may contain "
        "circled single letters, arrows or stray marks - transcribe the "
        "readable words only, use \"\" if nothing readable. If a word you "
        "read seems unusual for animation timing notes, re-examine the "
        "strokes carefully before committing to it. Read each file "
        "directly with your file-reading tool using the exact filename given "
        "below; do NOT run shell commands (no ls, find or pwd). Reply ONLY as "
        "a JSON object mapping each filename to its transcription (\\n for "
        "line breaks). Files: " + ", ".join(batch)
    )


def _run_cli(prompt: str, cwd: Path, engine: str | None = None) -> str:
    from apps.server.domain.video_captions.translate_cli import resolve_cli

    name = _pick_cli(engine)
    path = resolve_cli(name)
    if path is None:
        raise RuntimeError(
            f"전사 CLI({name})를 찾지 못했습니다 — 설치/로그인 후 다시 시도")
    argv = [*_argv_for(name, path, prompt),
            *shlex.split(os.environ.get(ENV_EXTRA_ARGS, ""))]
    # encoding 명시: Windows 한글 로케일에서 UTF-8 출력이 cp949 디코딩에
    # 실패하면 stdout이 None이 된다(report_summary.py 선례, f004487)
    result = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
        timeout=_CALL_TIMEOUT, check=False, **_NO_WINDOW)
    out = (result.stdout or "").strip()
    detail = (result.stderr or out or "").strip()[:200]
    # 쿼터 소진·미로그인은 rc=0 + 평문 한 줄로 온다(agy 실측:
    # "Error: Individual quota reached. ... Resets in 28m13s."). JSON 파싱
    # 실패로 흘려보내면 쪼개기 재시도가 같은 거절을 반복하며 큐를 태우고,
    # 사용자는 노트가 대부분 빠진 PDF를 조용히 받는다.
    if _FATAL_RE.search(detail):
        hint = ""
        if re.search(r"permission", detail, re.IGNORECASE):
            hint = (" · 이 CLI는 헤드리스에서 파일 읽기 권한이 필요합니다"
                    f" (허용 규칙 등록 또는 {ENV_CLI}=claude 로 전환)")
        raise TranscribeFatalError(
            f"전사 CLI({name})가 요청을 거절했습니다 — {detail}{hint}")
    if result.returncode != 0 or not out:
        raise RuntimeError(f"전사 CLI 응답 없음(rc={result.returncode}): {detail}")
    return result.stdout


def _extract_json_object(stdout: str) -> dict:
    """출력에서 **첫 JSON 객체만** 떼어낸다 — 통째로 json.loads 하지 않는다.

    CLI마다 말투가 달라서 코드펜스 앞뒤에 설명이 붙는다(claude 실측:
    펜스 닫은 뒤 요약 문단을 덧붙여 "Extra data" 파싱 실패). 문자열 안의
    중괄호·이스케이프를 세면서 균형 잡힌 끝을 찾는다.
    translate_cli._extract_json_array와 같은 방어다."""
    text = _strip_fences(stdout)
    start = text.find("{")
    if start == -1:
        raise ValueError("응답에서 JSON 객체를 찾지 못했습니다")
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("JSON 객체가 닫히지 않았습니다")


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        stripped = stripped[first_newline + 1:] if first_newline != -1 else stripped[3:]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()
