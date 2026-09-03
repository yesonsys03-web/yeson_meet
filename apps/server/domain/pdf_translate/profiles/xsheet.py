"""엑스시트(exposure sheet) 프로파일 — 스캔 손글씨 노트를 찾아 옆에 병기.

원본은 텍스트 레이어가 없는 300dpi 스캔이다(KOTH_1401 실측: 188p 전체
텍스트 0자). RapidOCR은 손글씨의 **위치**는 사실상 전부 찾지만 **판독**은
못 한다(2026-08-20 실측: SUBTLE→SUBnE, +OFFSET→40f=SET) — 그래서 이
프로파일의 extract는 위치·클러스터링만 책임지고, 블록 텍스트는
handwriting_transcribe(비전 CLI, 기본 agy)가 채운다. 번역·배치·굽기는
공통부 그대로다. ⛔Gemini API는 쓰지 않는다(2026-08-20 사용자 확정, 비용).

사람 납품본 관례(KOTH_1401_A1 번역본, FreeText 주석 3,701개 실측):
- 9pt 파랑 FreeText를 원문 손글씨를 가리지 않고 옆/아래 여백에 병기
- DIALOG/EXP 립싱크 음소 컬럼(A·M·EH…)·셀 번호·서클 마커는 번역하지 않음
- 스파이크 커버리지 96.3%(표본 16p, 사람 주석 273개 중 263 대응). 남은
  누락은 헤더 밴드의 손글씨 화자명 라벨 한 부류(문서화된 한계 — 편집기로
  수동 추가).
"""
from __future__ import annotations

import io
import json
import logging
import math
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from .base import Overlay, PdfBlock, has_hangul
from .storyboard import _clamp_nondegenerate

if TYPE_CHECKING:
    from ..backend import PdfDocument

logger = logging.getLogger("yeson.pdf_translate")

NOTE_KIND = "xsheet_note"

_DETECT_DPI = 100   # 헤더 토큰만 읽으면 되므로 저해상으로 충분(페이지당 ~1s)
_OCR_DPI = 200      # panel_ocr._OCR_DPI와 동일 — 손글씨 탐지엔 300 불필요
_CROP_DPI_CUT = 300  # = handwriting_transcribe._CROP_DPI (경계 절단 회수용
                     # 렌더). 값이 갈리면 크롭 사각형 판정이 어긋나므로
                     # 아래 테스트가 두 상수의 일치를 잠근다.
_DETECT_PAGES = 3
_SCAN_COVER = 0.5    # 페이지 면적의 이 비율 이상을 덮는 이미지 = 스캔본
_FONTSIZE = 9.0     # 사람 납품본 9pt 실측
_MIN_FONTSIZE = 7.2      # 사람 하한 20pt/24pt(1605 실측: 24pt 87%·22pt 13%·20pt 이하 0)
# 글꼴 사다리의 가운데 단. ⚠리터럴 8.0을 쓰면 페이지 스케일을 안 탄다 — 2200pt
# 판형(1605_A1 실측) 계획 3,706건 중 473건(12.8%)이 8pt(정상 25pt)로 굽혀 사실상
# 안 읽혔다. 스케일 대상 이름으로 두면 792 판형에선 예전과 같은 8pt다.
_MID_FONTSIZE = 8.0

# ⛔템플릿 좌표를 박지 않는다(2026-08-20). 작품마다 시트 양식이 다르다 —
# KOTH(792×1224pt, 대사 컬럼 1쌍)와 BM802(A3 841.92×1189.92pt, titmouse
# 양식, DIAL 2~5까지 대사 컬럼 5쌍)는 판형·비율·칸 배치가 전부 다르다.
# 페이지 크기 비율로만 늘린 좌표는 엉뚱한 칸을 가리킨다(실측: KOTH 기준
# 음소 밴드가 BM802에서는 DIAL2~3 위로 떨어져, 나머지 대사 컬럼의 립싱크
# 음소가 통째로 번역 대상이 됐다).
#
# 대신 **페이지에서 읽어 유도한다** — 칸 머리글(ACTION·DIALOG·EXP·DIAL n·
# TRUCK·CAMERA NOTES)은 인쇄 활자라 OCR이 conf 0.91~1.00으로 읽는다(양쪽
# 샘플 실측). 그 x·y가 곧 칸 경계다.
_HEADER_LABELS = {"ACTION", "CAMERANOTES", "TRUCK"}
_DIALOG_RE = re.compile(r"^(DIAL[A-Z0-9]{0,3}|EXP)$")
_FOOTER_LABELS = {"PRODNO", "FOOTAGE", "ANIMATOR", "SCENENO", "SHEETNO",
                  "PRODUCTIONNO", "SCENEDIRECTOR", "APPROVED"}
_HEADER_ROW_TOL = 8.0    # 같은 머리글 줄로 볼 y 오차(pt)
_BAND_PAD = 3.0          # 칸 경계 여유(pt)
_NUM_BIN_PT = 12.0       # 번호 컬럼 탐지용 x 버킷 폭
_NUM_BIN_MIN = 12        # 이 개수 이상이 모여야 컬럼으로 인정
_NUM_BIN_RATIO = 0.8     # 그중 숫자 비율
_PHONETIC_MAX_LEN = 3    # 대사 칸에서 이 길이 이하는 립싱크 음소(번역 안 함)
_CLUSTER_PAD = 6.0   # 세로로 쌓인 손글씨 단어들을 노트 하나로 묶는 근접 반경
_AXIS_TOL = 2.0      # 같은 스택·같은 줄로 볼 축 방향 허용 오차(대각선 배제)
# 전사 보내기 전에 버리는 잡티 크롭 기준. 전사는 CLI 세션(=구독 쿼터)을
# 쓰므로 어차피 버려질 것을 보내지 않는 게 곧 처리량이다.
#
# A1 전량 런의 실전사 380장 실측: 버려진 126장(33%)의 중앙값이 10×11pt·원시
# OCR 1글자인 반면, 살아남은 254장은 56×27pt·10글자였다. `면적>=300pt² 또는
# 원시 OCR 영숫자 2자 이상` 규칙이 쓰레기 90/126을 걷어내면서 진짜 노트는
# 254장 중 1장만 잃었다(0.4% — RapidOCR이 `CUT`을 `M`으로 읽은 작은 칸).
# 문턱을 더 올리면(면적 500·폭높이 20×14) 손실이 16~20%로 급등한다.
_MIN_NOTE_AREA = 300.0
_MIN_RAW_ALNUM = 2
_ALNUM_RE = re.compile(r"[A-Za-z0-9]")

# 놓을 자리로 인정하는 최소 폭. 사람 납품본 실측(KOTH_1401_A2 전수, 주석
# 2,015개)에서 상자 폭은 중앙값 30pt·1분위 21pt였다 — 사람은 이만한 틈에도
# 끼워 넣는다. 옛 값 45pt는 그 틈을 통째로 버리고 더 먼 자리로 밀어냈고,
# 24pt도 사람이 실제 쓴 21pt 틈(p23 '바비', 원문 오른쪽 +13pt)을 버렸다.
_MIN_BOX_W = 18.0
# 빈 자리 판정(place_with_doc). 사람 납품본 실측에서 주석 밑 손글씨 면적
# 비율은 중앙값 6.34%였다 — 사람도 여백이 없으면 겹쳐 쓴다. 2%는 "글자
# 위는 아니다"에 해당하는 선이고, 여기 못 미치는 자리가 없으면 가장 덜
# 덮는 자리를 쓴다(무조건 피하려다 원문에서 멀어지는 게 더 나쁘다).
_INK_OK = 0.02
# ⛔손글씨 겹침을 **하드 게이트**로 올리는 안은 실측 기각(2026-08-26).
# 겹침은 13%→3%로 잘 떨어지지만, `_BLOCKED`가 "막힘" 신호를 켜서 넓은
# 사다리(±80~260pt) 탈출을 유발한다 — A1 실측 거리 99분위 **207pt·최대
# 249pt**. 원문에서 200pt 떨어진 주석은 겹치는 것보다 나쁘다. 대신
# 사다리를 건드리지 않는 **강한 소프트 페널티**(_W_INK 상향)를 쓴다.
_INK_DPI = 100        # 잉크 유무 판정에는 이 정도면 충분하다(OCR용 200과 별개)
_INK_DARK = 150       # 이보다 어두우면 잉크
_RULE_FILL = 0.45     # 행·열의 이 비율 이상이 어두우면 인쇄 괘선으로 본다

# 빈 자리를 찾아 세로로 밀어 보는 폭. **절대값이어야 한다** — 처음엔 노트
# 높이에 비례시켰는데(±0.5·±1배), 여러 줄짜리 긴 노트에서는 그 절반이
# 165pt여서 번역문이 원문에서 통째로 떨어져 나갔다(시뮬레이션에서 실제
# 발생: y362 노트의 번역이 y530으로). 사람 납품본 실측(A2 전수)에서
# 원문 상단 대비 주석 상단의 세로 편차는 중앙 15.6pt·8분위 38.7·9분위
# 53.7pt였다 — 사다리를 그 분포 안에 가둔다.
_DY_LADDER = (0.0, 16.0, -16.0, 32.0, -32.0, 52.0, -52.0)
# 아래 배치의 세로 틈 사다리 — 원문 하단에서 이만큼 띄워 본다. 사다리와
# 같은 분포 안(최대 50pt)에 가둔다.
_BELOW_GAPS = (2.0, 18.0, 34.0, 50.0)

# 배치 채점(2026-08-25 전수 감사 근거). 옛 방식은 "잉크 2% 이하인 첫
# 후보"라서 원문 옆이 조금만 지저분해도 사다리 끝(±52pt)이나 최후의
# 아래 자리로 도망갔다 — 사람 대비 above 16%/9%·left 22%/10%로 몰리고
# 사람과 같은 자리(30pt 이내)는 21.5%뿐이었다. 사람은 주석 밑 잉크
# 중앙값 6.34%를 감수하면서 원문 곁에 남는다(잉크에 겹쳐 쓰되 주석
# 끼리는 절대 안 겹침). 그래서 잉크 회피를 하드 게이트에서 **거리와
# 교환 가능한 비용**으로 강등한다: 잉크 1% ≈ 2.5pt 도주와 등가(500에서
# 시작해 시뮬 벤치로 낮췄다 — 클수록 옆 잉크 몇 %p 차이가 방향을 뒤집어
# left 쏠림이 생긴다: rework1 실측 left 481 대 사람 153).
_W_INK = 3000.0   # ⚠2026-08-26 250→3000 (사용자 지적: "원문과 겹치면 가독성이 망가진다")
# 250일 때 우리는 **사람보다 더 겹쳤다** — A1 5페이지 픽셀 실측(인쇄 괘선을
# 지운 손글씨 마스크): 사람 17/0/0/0/0% 대 우리 23/10/0/0/12%. 예전 "사람도
# 40.9%(사각형)·78.1%(2%+잉크) 겹친다"는 **잣대가 틀린 것**이었다 — 사각형은
# 여러 줄 노트의 빈칸을 겹침으로 세고, 그 '잉크'엔 인쇄 괘선이 들어간다.
# 2D 스윕(A1, 표본 447건, **손글씨 전용 마스크**): INK 250→3000·LEFT 0→0.5
#     손글씨겹침 13%→8% · 왼쪽 37%→12%(사람 9%) · 주석겹침 0 유지
#     거리 90/99분위 **3/34pt**(대부분 여전히 원문에 딱 붙는다)
# INK를 10000까지 올려도 겹침은 8%에서 안 내려간다 — 남은 8%는 어느 후보든
# 글씨를 덮는 자리들이라 후보 생성을 늘려야 풀린다(위 배치·줄 사이 병기).
# 폰트 한 단계 축소(9→8→7)의 비용 — 근처에 옅은 잉크 자리가 있으면
# 글자를 줄여 먼 빈자리로 가느니 제 크기로 곁에 앉는 게 사람 관례다.
_W_FS = 12.0
# 아래 배치의 변위는 블록 **상단** 기준이다 — 읽기는 위에서 시작하므로
# 긴 세로 스택(대사 노트)의 아래 자리는 틈이 2pt여도 사실상 멀다(A2
# 실물 지적: SMU 9줄 스택의 번역이 아래로 밀려 사람(좌측 병기)과 어긋남).
# 블록 높이의 일부를 변위로 치되, 짧은 노트가 손해 보지 않게 상한을 둔다.
_BELOW_H_W = 0.25
_BELOW_H_CAP = 100.0
# 넓은(문장형·과병합) 클러스터의 왼쪽 배치 페널티. 사람은 가로로 긴
# 원문에는 **끝(오른쪽)이나 그 아래**에 단다 — 시작점 왼쪽 바깥은 내용어
# 에서 원문 폭만큼 떨어진 자리다(rework2 최악 꼬리 실측: 폭 336pt 클러스터
# 의 번역이 x20에 앉아 사람(x250~410)과 245~384pt 어긋남, p127·p23·p15).
# 폭이 이 기준을 넘는 만큼만 비율로 얹는다 — 좁은 노트는 영향 없다.
# 왼쪽 배치는 상자가 **폭만큼 더 멀어지는데** 그 가로 거리가 변위에 안 들어
# 갔다 — 오른쪽은 `bx1+3`에 붙지만 왼쪽은 `bx0-3-폭`에서 시작하므로 읽기
# 시작점이 폭만큼 떨어진다. 그래서 잉크만으로 승부가 갈려 프레임 번호 여백
# (잉크 ≈0)이 계속 이겼다(A2 실측: 우리 왼쪽 436 대 오른쪽 3 / 사람은 154 대
# 167로 고르다. A1 사람 납품본도 오른쪽 352·왼쪽 305). 이 계수로 그 거리를
# 변위에 되돌린다 — 0이면 옛 동작.
# **전수 3문서 실측**(A1·A2·A3 전 항목, 블록 bbox 기준 왼쪽 비율):
#     0.0  A1 37% · A2 34% · A3 35%
#     0.5  A1  9% · A2 11% · A3 10%      ← 사람 A1 9% · A2 10% · A3 7%
#     1.0  A1  3% · A2  4% · A3  4%      (과교정)
# A1은 튜닝에 한 번도 안 쓴 홀드아웃인데 A2와 같은 값이 나왔다 = 과적합 아님.
# ⚠**상한이 0.5다**: 그 위로 올리면 08-25 사용자 지적으로 만든 "긴 세로 스택은
# 아래보다 옆에 병기"가 깨진다(0.75부터 아래로 밀림). 테스트가 그 경계를 잠근다.
_LEFT_FAR_W = 0.5
_WIDE_FROM = 80.0
_WIDE_L_W = 0.25

# 머리글 위 손글씨 회수(A2 실측 2026-08-24: 사람이 번역한 상단 빨간 원
# 이름 27건이 `y1 < header_y` 일괄 컷에 죽었다 — p71 (409,22) 'HANK'를
# RapidOCR이 잡고도 버림). 인쇄 타이틀·고정 칸 번호와의 구분은 어휘가
# 아니라 **페이지 간 위치 반복**으로 한다 — 인쇄물은 매 페이지 같은
# 자리에 찍히고, 손글씨는 자리가 흔들린다. 작품 종속 어휘 금지 원칙.
_HDR_MIN_ALPHA = 3       # 알파벳 3자 미만 = 씬 번호·낙서 코드(354·(QH))
_HDR_POS_QUANT = 8.0     # 위치 양자화(pt) — 인쇄물의 OCR 흔들림 흡수
_HDR_REPEAT_FRAC = 0.2   # 이 비율 이상의 페이지에서 같은 자리 = 인쇄물
_HDR_REPEAT_MIN = 2      # 최소 반복 페이지 수(짧은 문서 하한)
_ALPHA_RE = re.compile(r"[A-Za-z]")

# 순수 코드 노트의 결정적 해독(A2 사람 납품본 관례 근거: 오버슛 18:0·
# 안착 50:0·표정/시선·쿠션·(CONT'D)=(계속)·머리→고개 다수 통일).
# 번역 LLM은 이런 코드를 확률적으로 에코(원문 그대로)하고, pdf_run은
# 에코를 "번역 실패"로 보아 주석을 버린다 — 사전이 있으면 block.ko
# predecode 경로(판넬 약어와 동일)로 에코-드롭을 원천 우회한다.
# OUS·DVS는 OVS의 전사 오독 실측(2026-08-24, p78·p111·p125). 업계 공용
# 엑스시트 용어만 담는다 — 작품 종속 어휘(이름) 금지, 이름은 재시도 몫.
_CODE_KO = {
    "OVS": "오버슛", "OUS": "오버슛", "DVS": "오버슛",
    "STL": "안착", "ST": "안착",
    "EXP": "표정", "EYES": "시선",
    "CUSH": "쿠션", "CONT": "계속",
    "HEAD": "고개",
    "OS": "씬밖", "O.S": "씬밖", "SAC": "포대",
}
# 해독기가 그대로 통과시키는 토큰 — 원문자·프레임/씬 번호(`C BOB`·`(H) BOB`·
# `2034B`). 1605 실측: 이런 토큰 하나 때문에 해독이 거부되고 LLM이 원문을
# 되돌려(에코) 주석 208건이 버려졌다.
_PASS_TOKEN_RE = re.compile(r"^\(?[A-Z]\)?\.?$|^#?\d+[A-Z]?\.?$")

# ★등장인물 이름표(2026-09-03, 1605_A1 사용자 검수: `CHANE`→차네·`SAND`→샌드·
# `EMI`→에미로 직역 음역됐다 — 사람은 작품 인물표로 체인·산드라·에밀리오). 손글씨
# 이름은 줄여 쓰는 일이 많아(BOB·PEG·JOSE·CHAN) LLM 음역이 흔들린다. 내장값은
# 1605·1603 사람 납품본에서 캔 것(원문 한 단어 ↔ 사람 주석, 예: HANK 행크 26/38·
# KAHN 칸 14/14·CHANE 체인 7/8)+사용자 지정 3건. 작품이 바뀌면 운영자가
# `{STORAGE_ROOT}/xsheet_cast.txt`(`NAME => 이름` 한 줄씩, # 주석)로 덮거나 보탠다.
# 회의·자막 사전(glossary.py)에 넣지 않는다 — `SAND`→산드라가 회의 자막을 망친다.
_CAST_KO_DEFAULT = {
    "HANK": "행크", "BOBBY": "바비", "BOB": "바비", "PEGGY": "페기", "PEG": "페기",
    "DALE": "데일", "BILL": "빌", "JOSEPH": "죠셉", "JOSE": "죠셉", "KAHN": "칸",
    "CONNIE": "코니", "NANCY": "낸시", "CHANE": "체인", "CHAN": "체인", "MAX": "맥스",
    "SAND": "산드라", "SANDRA": "산드라", "EMI": "에밀리오", "EMILIO": "에밀리오",
    "BOOMHAUER": "붐하우어", "LUANNE": "루앤", "MIGUEL": "미구엘",
}
_CAST_FILENAME = "xsheet_cast.txt"
# 원문에 그 이름이 있을 때만 고치는 잘못된 음역(1605 실측). 한글 낱말 경계로
# 감싼다 — `찬`은 `찬다`의 일부일 수 있다.
_CAST_VARIANTS = {
    "체인": ("차네", "챈", "찬", "체인지"),
    "산드라": ("샌드", "샌디"),
    "에밀리오": ("에미",),          # `이미`는 흔한 낱말이라 뺀다
    "미구엘": ("미겔",),
}
# 받침 있는 이름으로 바뀌면 조사도 따라간다(차네가 → 체인이)
_PARTICLE_AFTER_BATCHIM = {"가": "이", "는": "은", "를": "을", "와": "과"}


def _has_batchim(word: str) -> bool:
    ch = word[-1]
    return "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28 != 0


def _cast_table() -> dict[str, str]:
    """내장 이름표 + 운영자 파일(있으면). 키는 대문자."""
    import os

    from apps.server.ai.glossary import DEFAULT_STORAGE_ROOT, STORAGE_ROOT_ENV

    table = dict(_CAST_KO_DEFAULT)
    path = Path(os.environ.get(STORAGE_ROOT_ENV) or DEFAULT_STORAGE_ROOT) / _CAST_FILENAME
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for sep in ("=>", "\t", "="):
                if sep in line:
                    en, ko = line.split(sep, 1)
                    break
            else:
                continue
            if en.strip() and ko.strip():
                table[en.strip().upper()] = ko.strip()
    except OSError:
        pass
    return table


def _cast_prompt_block() -> str:
    table = _cast_table()
    return ("Character names on these sheets (handwritten, often abbreviated) "
            "and their FIXED Korean renderings - always use exactly these, never "
            "another transliteration: "
            + ", ".join(f"{en} → {ko}" for en, ko in sorted(table.items())) + ".\n")

# 화자 스트립(A2 실측 2026-08-25): 화자 이름은 대사 칸 왼쪽에 연필
# 원·굵은 글씨로 쓰이는데 RapidOCR이 전 스케일(120~400dpi)에서 못 읽어
# 사람 대비 이름 누락 ~50건이 남았다. 원형 탐지는 오탐이 지배(후보
# 19.5~27.6/페이지, 3중 게이트 실측 전부 실패). 해법=대사 칸 좌우
# 스트립을 페이지당 1크롭으로 잘라 **비전 CLI가 통째로 읽는다**(원 안
# 손글씨 판독 가능 실증). 위치는 y비율로 받아 음소 런에 스냅한다.
# 구조 정보는 스트립 의사 블록의 text(JSON)에 싣는다 — 프로파일은
# 싱글턴이라 인스턴스 상태는 잡 간에 샌다.
STRIP_KIND = "xsheet_speaker_strip"
_STRIP_XPAD_L = 90.0   # 밴드 왼쪽 여유(연필 원이 놓이는 마진)
_STRIP_XPAD_R = 40.0
_RUN_GAP = 60.0        # 음소 항목 y 간격이 이보다 크면 새 런
_RUN_PH_MAXLEN = 4     # 음소로 볼 정규화 길이 상한
_SNAP_PT = 120.0       # 스캔 y를 런 시작에 스냅하는 최대 거리
_SPK_W, _SPK_H = 55.0, 16.0   # 합성 화자 블록 크기(pt)
_SPK_DEDUP_PT = 40.0   # 기존 블록과 이 거리 안이면 합성 생략
_CARRY_TOP_PT = 60.0   # 첫 런이 스트립 상단에서 이 안이면 '연속 런'
# 정밀 안전판(2026-08-25 실측): 전파를 세게 걸면(런별 무제한 배정+무기한
# 이월) 스캔이 놓친 이름 자리에 엉뚱한 이름이 들어간다 — 사람 대조에서
# 오표기 10건 실측(p83 행크 자리에 데일 등). 오표기가 누락보다 나쁘다.
_ASSIGN_MAX_PT = 150.0   # 런 위 이름이 이보다 멀면 배정하지 않는다


# 주석-주석 겹침 게이트(2026-08-25): 잉크만 피하던 배치가 이웃 블록과 같은
# 빈자리를 골라 심한 겹침 91쌍(사람 0쌍)을 만들었다 — 배치 이력을 점유
# 공간으로 넘겨 받아 피한다.
_OCC_OK = 0.05        # 작은 쪽 면적의 이 비율까지는 겹침 허용
# 겹침 후보에 얹는 벽 — 겹치지 않는 후보가 하나라도 있으면 절대 못 이긴다.
# 이 값 이상이면 "전 후보가 막혔다"는 신호이기도 하다(_far_candidates 발동).
_BLOCKED = 100000.0
# 폴백 채점 경로의 잉크 상한(하드). 옆자리 경로는 _SIDE_INK_OK(0.5%)로 손글씨를
# 안 덮는데, 폴백은 잉크가 비용일 뿐 벽이 아니었다 — 1603 실측: 손글씨 위에
# 앉은 주석 28/28건 전부 폴백 경로였고(우리 5.9% 대 사람 2.3%), 큰 다중줄
# 상자가 빈자리에 못 들어가면 작화선·타이밍 곡선 위를 "감수"해 버렸다.
# 이 값을 넘으면 _BLOCKED 벽을 세운다 → place_with_doc이 넓은 사다리
# (_far_candidates)로 탐색을 넓히는 기존 경로가 그대로 발동한다.
_FB_INK_HARD = 0.02
# 막힌 경우에만 쓰는 넓은 세로 사다리(pt). 정상 배치에는 관여하지 않는다.
_FAR_LADDER = (80.0, -80.0, 120.0, -120.0, 180.0, -180.0, 260.0, -260.0)


def _occupied_frac(rect: tuple[float, float, float, float],
                   occupied) -> float:
    """후보가 기존 주석과 겹치는 정도 — **작은 쪽 면적 기준 최대값**(0~1).

    후보 면적 기준으로 재면 큰 후보가 작은 주석(합성 화자 '행크' 22×17pt)
    을 통째로 삼켜도 비율이 낮게 나와 게이트를 통과한다(rework1 시뮬에서
    심한 겹침 1쌍 실측 — p80 행크 60% 잠식이 후보 기준 7%로 위장). 사람
    관례의 "안 겹친다"는 작은 주석이 먹히지 않는다는 뜻이다."""
    area = max((rect[2] - rect[0]) * (rect[3] - rect[1]), 1e-6)
    worst = 0.0
    for o in occupied or ():
        ix = min(rect[2], o[2]) - max(rect[0], o[0])
        iy = min(rect[3], o[3]) - max(rect[1], o[1])
        if ix > 0 and iy > 0:
            o_area = max((o[2] - o[0]) * (o[3] - o[1]), 1e-6)
            worst = max(worst, ix * iy / min(area, o_area))
    return min(worst, 1.0)


def _ink_ratio(ink, rect: tuple[float, float, float, float],
               page_h: float) -> float:
    """이 사각형 넓이에서 손글씨가 차지하는 비율."""
    scale = _INK_DPI / 72.0
    h, w = ink.shape
    x0 = max(0, int(rect[0] * scale)); y0 = max(0, int(rect[1] * scale))
    x1 = min(w, int(rect[2] * scale)); y1 = min(h, int(rect[3] * scale))
    if x1 <= x0 or y1 <= y0:
        return 1.0
    return float(ink[y0:y1, x0:x1].mean())


def _natural_width(text: str, fontsize: float) -> float:
    """이 글이 **줄바꿈 없이** 앉으려면 필요한 폭.

    번역문은 원문 손글씨의 세로 쌓기를 그대로 물려받아 이미 줄이 나뉘어
    있다(`SMU\\n남자가\\n걸어서`) — 가장 긴 줄만 담으면 사람이 쓰는 좁은
    덩어리가 그대로 나온다. `_estimate_height`와 같은 CJK 근사(글자당 ≈
    fontsize pt)를 쓴다: 두 함수가 어긋나면 폭 계산이 예상한 줄 수와 높이
    계산이 쓰는 줄 수가 달라져 마지막 줄이 상자 밖으로 잘린다."""
    longest = max((len(line) for line in text.split("\n")), default=1)
    return max(1, longest) * fontsize + 4.0

# 엑스시트 하우스 용어(KO→KO) — KOTH_1401_A2 사람 납품본 전수 대조
# (2026-08-21, 사람 주석 2,015개 vs 우리 출력 1,697개). house_style.py가
# 아니라 여기 두는 이유: `대사`는 스토리보드에선 **정답**이라(DIALOG 칸의
# 표준 역어) 전역 치환하면 다른 포맷을 망친다. 엑스시트에서만 사람이
# `대화`를 쓴다 — 포맷 스코프가 계약이다.
#
# 채택 기준은 house_style.py와 같다: 한쪽이 정확히 0인 항목만 넣는다.
#   STL(settle) 사람 `안착` 50 / 우리 0  ↔ 우리 `세틀` 9·`스틸` 6 / 사람 0
#   DIAL        사람 `대화` 29 / 우리 0  ↔ 우리 `대사` 18·`다이얼` 3 / 사람 0
#   TURN        사람 `턴한다` 57 / 우리 0 ↔ 우리 `돈다` 22 / 사람 0
#   STEP        사람 `스텝` 62 / 우리 5  ↔ 우리 `걸음` 46 / 사람 0
#   GESTURE     사람 `제스쳐` 9 / 우리 0 ↔ 우리 `제스처` 8 / 사람 0
#   OVERSHOOT   사람 `오버슛` 18 / 우리 0 ↔ 우리 `오버슈트` 2 / 사람 0
#   BLINK       사람 `눈깜박` 155 / 우리 0 ↔ 우리는 한 문서 안에서 다섯 형태로
#               흔들린다(`눈 깜빡` 59·`깜빡임` 40·`눈을 깜빡인다` 25·`눈깜빡`
#               8·`눈 깜빡임` 3) — 표기 통일이 곧 사람 표기와의 일치다.
# `스틸`·`다이얼`은 표기 흔들림이 아니라 **오역**이다(STL=settle을 still로,
# DIAL=dialogue를 dial로 읽었다). 사전이 없으면 다음 작품에서도 재발한다.
#
# ⛔`머리`→`고개`는 넣지 않는다: 사람 `고개` 156 / `머리` 9(머리카락 4 제외)로
# 사람 쪽이 0이 아니다. 실제로 사람은 `머리 기웃`·`척의 머리가`처럼 문맥에
# 따라 `머리`를 쓴다 — 위 기준 미달이라 보류한다(누락 84건, 최대 미해결 항목).
_HOUSE_KO_XSHEET: tuple[tuple[re.Pattern[str], str], ...] = (
    # 눈깜박: 긴 형태부터 — 짧은 규칙이 먼저 돌면 `눈 깜빡임`이 `눈깜박임`으로
    # 남는다. 앞 규칙이 `빡`을 `박`으로 바꾸므로 뒤 규칙과 겹치지 않는다.
    (re.compile(r"눈\s*을\s*깜[빡박]인다"), "눈깜박"),
    (re.compile(r"눈\s*깜[빡박](?:임|인다)?"), "눈깜박"),
    (re.compile(r"깜[빡박]임"), "눈깜박"),
    (re.compile(r"깜빡"), "눈깜박"),
    (re.compile(r"오버슈트"), "오버슛"),
    (re.compile(r"제스처"), "제스쳐"),
    (re.compile(r"다이얼"), "대화"),
    (re.compile(r"대사"), "대화"),
    (re.compile(r"돈다"), "턴한다"),
    # 1605 실측 인공물: 원문 끝의 홀로 선 `TO`가 `~로`(96건)·`안착로`(37건)로 남는다
    # — 사람은 무시한다(`STL TO`→`안착`). `시프트`는 사람 0/우리 72 → `이동`.
    (re.compile(r"\s*~로"), ""),
    (re.compile(r"안착로"), "안착"),
    (re.compile(r"시프트"), "이동"),
    # `걸음걸이`는 사람도 쓰는 정상 낱말이라 건드리지 않는다.
    (re.compile(r"걸음(?!걸이)"), "스텝"),
    # HEAD: 사람 `고개` 156 / `머리` 9(머리카락 4 제외) ↔ 우리 `머리` 126.
    # 한쪽이 0은 아니지만 house_style의 `신밖`→`씬밖` 선례와 같은 **다수 표기
    # 통일**이고, 그 선례(1건 소수)보다 훨씬 일방적이다(9 대 156). 사람의 9건에
    # 규칙성이 있는지 문맥까지 실측했으나 없었다 — `기웃`과 함께 쓸 때조차
    # `고개` 17 대 `머리` 2로 `고개`가 우세하다.
    # ⚠`머리카락`은 사람·우리 4건씩 정확히 일치하는 정상 낱말이라 반드시 뺀다.
    (re.compile(r"머리(?!카락)"), "고개"),
    # 2026-08-25 품질 전수 대조(사람 1,942쌍) 추가분 — 채택 기준 동일(관례
    # 압도·작품 비종속). 유리창↔창문(37:36)·애니(6:6)는 관례가 안 갈려 제외.
    (re.compile(r"앤티시페이션"), "준비동작"),
    (re.compile(r"악센트"), "액센트"),
    # STEP의 잉여 음역 `발스텝`(사람 `스텝` 61 / `발스텝` 0)만 걷는다.
    # ⚠경계가 계약이다(2026-08-25 실측): 옛 패턴 `발\s*스텝`은 `\s*`가
    # 줄바꿈을 넘어, 원문 `RT|FT|STEP`(오른발 스텝)의 번역 `오른|발|스텝`
    # 에서 **`발`(foot)을 지울 수 있었다**. 실제 잉여 표기는 언제나 붙어
    # 쓴 단일 토큰이었고(전수 47건 전부 `발스텝`), 사람이 FT를 쓴 자리엔
    # `발`이 정답이다. 앞의 `오른`·`왼`도 같은 이유로 잠근다 —
    # `오른발스텝`을 줄이면 `오른스텝`이 되어 발이 사라진다. 반면 LLM이
    # 흔히 내는 겹말 `오른|발|발스텝`은 이 규칙이 `오른|발|스텝`으로
    # 정확히 정리한다(옛 규칙이 실제로 하던 일).
    (re.compile(r"(?<!오른)(?<!왼)발스텝"), "스텝"),
    # EAST/WEST/NORTH/SOUTH = **화면 방향**이지 방위가 아니다(2026-08-26 사용자
    # 지적). 시트에서 `HEAD EAST`는 "고개를 오른쪽으로"이지 동쪽이 아니다.
    # A1 실측: 원문 EAST 299·WEST 259·NORTH 2·SOUTH 2 = 항목의 **14.6%**가
    # 방위로 오역됐다(`133 TILTS HEAD EAST`→`고개를 동쪽으로 기울인다`).
    # ⚠원문 조건부(BY_SRC)가 아니라 **무조건**인 이유: 시트 노트에 나침반
    # 방위가 정당한 문맥이 없고, 원문이 약칭일 때도 잡아야 한다 — A1에서
    # 원문에 낱말이 없는데 `서쪽`이 나온 2건은 전부 `W.`·`OFF WE` 약칭이라
    # 조건부였으면 놓쳤을 것들이다(`정지`처럼 다른 뜻으로 정당한 낱말이
    # 아니므로 BY_SRC의 근거가 여기엔 없다).
    # 조사 처리: `동/서`는 `오른쪽·왼쪽`도 `쪽`으로 끝나 어간만 갈면 모든
    # 조사형이 맞는다(`동쪽으로`→`오른쪽으로`). `북/남`은 받침이 달라져
    # `북쪽으로`→`위으로`가 되므로 조사형을 먼저 처리한다.
    (re.compile(r"북쪽으로"), "위로"),
    (re.compile(r"남쪽으로"), "아래로"),
    (re.compile(r"북쪽을"), "위를"),
    (re.compile(r"남쪽을"), "아래를"),
    (re.compile(r"북쪽과"), "위와"),
    (re.compile(r"남쪽과"), "아래와"),
    (re.compile(r"북쪽"), "위"),
    (re.compile(r"남쪽"), "아래"),
    (re.compile(r"동쪽"), "오른쪽"),
    (re.compile(r"서쪽"), "왼쪽"),
)
# 원문 코드별 규칙 — 같은 한국어라도 원문이 무엇이냐에 따라 사람 표기가
# 갈린다(TILT는 `기웃`, LEAN은 `기울인다`). 무조건 치환하면 한쪽이 깨진다.
_HOUSE_KO_XSHEET_BY_SRC: tuple[tuple[re.Pattern[str],
                                     tuple[tuple[re.Pattern[str], str], ...]], ...] = (
    # STL(settle): 사람 `안착` 50 / 우리 0 ↔ 우리 `세틀` 9·`스틸` 6·`정지` 5.
    # ⚠단어 경계가 계약이다: 부분 문자열로 찾으면 `HUSTLE`·`CASTLE`·`WRESTLE`가
    # STL 노트로 오인돼 멀쩡한 `정지`를 `안착`으로 덮는다(테스트가 잡은 함정).
    # 복수형 `STLS`도 같은 코드다(A2 계획 실측: `& STLS`의 `세틀` 잔존 1건).
    # ⚠원문이 **철자 그대로** `SETTLE`일 때도 사람은 `안착`을 쓴다 — 옛 주석은
    # "SETTLE 철자면 규칙 대상 아님"으로 넘겼는데 **오판이었다**. A1 사람 납품본
    # 실측(2026-08-26): 안착 167 · 세틀 0 · 스틸 0인데 우리 산출물엔 `세틀`이
    # 50건 남았다. 약칭이냐 철자냐는 사람 표기를 가르지 않는다.
    (re.compile(r"\b(?:STLS?|SETTLES?)\b", re.IGNORECASE), (
        (re.compile(r"[&+]\s*세틀"), "안착"),
        (re.compile(r"세틀"), "안착"),
        (re.compile(r"스틸"), "안착"),
        (re.compile(r"정지"), "안착"),
    )),
    # LEAN: 사람 `기울인다` 68 / 우리 5 ↔ 우리 `기댄다` 32·`기댐` 4 / 사람 0.
    # 기대다(lean on)가 아니라 몸을 기울이는 동작이다.
    (re.compile(r"\bLEANS?\b", re.IGNORECASE), (
        (re.compile(r"기댄다"), "기울인다"),
        (re.compile(r"기댐"), "기울인다"),
    )),
    # TILT: 사람 `기웃` 41 / 우리 0 ↔ 우리 `기울임` 43(사람 1)·`틸트` 9 / 사람 0.
    # `틸트`는 표기 흔들림이 아니라 음역이다 — 사람은 한 번도 쓰지 않는다.
    (re.compile(r"\bTILTS?\b", re.IGNORECASE), (
        (re.compile(r"기울임"), "기웃"),
        (re.compile(r"틸트"), "기웃"),
    )),
    # BOOM: 원문은 `BOOM`(약칭)인데 LLM이 지식으로 `붐하우어`(풀네임)로
    # 부풀린다 — 작품 어휘가 아니라 **원문 충실성** 규칙이다(사람 29쌍 실측).
    (re.compile(r"\bBOOM\b", re.IGNORECASE), (
        (re.compile(r"붐하우어"), "붐"),
    )),
    # STOP: 사람 `멈춤` / 우리 `정지`(품질 대조 5쌍). `정지`는 STL 규칙에서
    # `안착`으로 덮이는 문맥도 있어 원문 조건으로 가른다.
    (re.compile(r"\bSTOPS?\b", re.IGNORECASE), (
        (re.compile(r"정지"), "멈춤"),
    )),
    # ── 1605_A1 사람 대조(2026-09-03, 3,156쌍 전수) — 원문 조건부 ──
    # 사람 낱말(우리 0) ↔ 우리 낱말(사람 0)을 짝의 원문 토큰으로 검증한 것.
    # ⚠A2 번역자는 EYES→시선이었다(사람마다 관례가 다르다). 1603·1605 두 문서가
    # 두눈이라 다수를 따른다 — 작품별 표기는 xsheet_cast.txt와 같은 방식이 필요.
    (re.compile(r"\bEYES?\b", re.IGNORECASE), (
        (re.compile(r"(?<![가-힣])시선"), "두눈"),
        (re.compile(r"(?<![가-힣])눈(?![가-힣])"), "두눈"),
    )),
    (re.compile(r"\bHANDS\b", re.IGNORECASE), (
        (re.compile(r"(?:양|두)\s*손"), "두손"),
        (re.compile(r"(?<![가-힣])손(?=[을이,.\s]|$)"), "두손"),
    )),
    (re.compile(r"\bARMS\b", re.IGNORECASE), (
        (re.compile(r"(?:양|두)\s*팔"), "두팔"),
        (re.compile(r"(?<![가-힣])팔(?=[을이,.\s]|$)"), "두팔"),
    )),
    (re.compile(r"\bLEGS\b", re.IGNORECASE), (
        (re.compile(r"(?:양|두)\s*다리|(?<![가-힣])다리(?=[를이,.\s]|$)"), "두다리"),
    )),
    (re.compile(r"\bKICKS?\b", re.IGNORECASE), (
        (re.compile(r"(?:발로\s*)?찬다"), "발찬다"),
    )),
    (re.compile(r"\bUP\b", re.IGNORECASE), (
        (re.compile(r"올린다|올리며|올림"), "위로"),
    )),
    (re.compile(r"\bEXP\.?(?![A-Za-z])", re.IGNORECASE), (
        (re.compile(r"익스포저"), "표정"),
    )),
    (re.compile(r"\bW/\s*EXP", re.IGNORECASE), (
        (re.compile(r"표정과\s*함께|표정과"), "표정하며"),
    )),
    (re.compile(r"\bW/\s*ACTION\b", re.IGNORECASE), (
        (re.compile(r"(?:액션|동작)과\s*함께|(?:액션|동작)과"), "액션맞춰"),
    )),
    (re.compile(r"\bBRUSH\b", re.IGNORECASE), (
        (re.compile(r"브러시로"), "붓으로"), (re.compile(r"브러시를"), "붓을"),
        (re.compile(r"브러시"), "붓"),
    )),
    (re.compile(r"\bLACQUER\b", re.IGNORECASE), ((re.compile(r"래커|락커"), "광택제"),)),
    (re.compile(r"\bGLOW\b", re.IGNORECASE), ((re.compile(r"글로우"), "섬광"),)),
    (re.compile(r"\bPARTY\s*LIGHT", re.IGNORECASE), ((re.compile(r"파티\s*(?:조명|라이트)"), "파티조명"),)),
    (re.compile(r"\bCAST\s*S(?:HADOW)?\b", re.IGNORECASE), ((re.compile(r"캐스트\s*(?:섀도우|그림자)"), "투영그림자"),)),
    (re.compile(r"\bSHADOWS?\b", re.IGNORECASE), ((re.compile(r"섀도우"), "그림자"),)),
    (re.compile(r"\bRIM\s*LI(?:T|GHT)", re.IGNORECASE), ((re.compile(r"림\s*라이트"), "림라이트"),)),
    (re.compile(r"\bCLOTH\b", re.IGNORECASE), ((re.compile(r"(?<![가-힣])(?:천|옷)(?![가-힣])"), "행주"),)),
    (re.compile(r"\bAD[- ]?LIB", re.IGNORECASE), ((re.compile(r"애드립"), "임의로"),)),
    (re.compile(r"\bSUBTLE\b", re.IGNORECASE), ((re.compile(r"약하게|미묘하게|살짝|약간"), "은근하게"),)),
    (re.compile(r"\bSLIGHT(?:LY)?\b", re.IGNORECASE), ((re.compile(r"약간|살짝|조금"), "작게"),)),
    (re.compile(r"\bTHRU\b|\bTHROUGHOUT\b", re.IGNORECASE), ((re.compile(r"씬\s*전체(?:에\s*걸쳐)?|전체에\s*걸쳐"), "씬내내"),)),
    (re.compile(r"\bPLEDGES?\b", re.IGNORECASE), ((re.compile(r"맹세한다|맹세"), "서약자"),)),
    (re.compile(r"\bFRAT\b", re.IGNORECASE), ((re.compile(r"프랫\s*형제들"), "협회원들"), (re.compile(r"프랫"), "협회원"))),
    (re.compile(r"\bDROPPER\b", re.IGNORECASE), ((re.compile(r"드로퍼"), "스포이드"),)),
    (re.compile(r"\bCOVER\b", re.IGNORECASE), ((re.compile(r"커버"), "가리개"),)),
    # FLICKER는 불빛의 깜빡임 — 전역 `깜빡→눈깜박` 규칙(눈 깜박)을 되돌린다
    (re.compile(r"\bFLICKER", re.IGNORECASE), ((re.compile(r"눈깜박"), "깜빡임"),)),
    (re.compile(r"\bPOP\b", re.IGNORECASE), ((re.compile(r"팝\s*투|팝"), "팍"),)),
    # HP(마커)는 사람이 옮기지 않는다 — 번역문에 남은 `HP`를 지운다
    (re.compile(r"\bHP\b"), ((re.compile(r"[\s,]*(?<![A-Za-z])HP(?![A-Za-z])\.?"), ""),)),
    # W/W(with action): 사람 `액션맞춰 움직임` 21 / 우리 `따라·함께 움직임` 6.
    (re.compile(r"\bW/W\b", re.IGNORECASE), (
        (re.compile(r"(?:를|을)?\s*따라\s*움직"), " 액션맞춰 움직"),
        (re.compile(r"와\s*함께\s*움직"), " 액션맞춰 움직"),
    )),
    # ON n'S(n콤마 작화): 사람 `1콤마에`·`2콤마에` 12 / 음역 0 ↔ 우리
    # `온 원스`·`온 투스` 17 / 콤마 0 (2026-08-25 사용자 실물 지적 + A2 전수
    # 재확인). 전사 실형태는 `ON 1S`·`ON (1)S`·`ON\n2'S`, 그리고 숫자 없는
    # **`ONS`**(on ones 약칭 — A2 계획에 `온 원스` 잔존 1건으로 드러났다).
    # 숫자는 음역 쪽이 이미 담고 있어(원스=1·투스=2) 결정적으로 되돌린다.
    # `온`과 음역이 줄로 갈라진 실물(`회전|온|투스`)이 있어 공백 매칭은
    # 개행을 포함해야 한다.
    (re.compile(r"\b(?:ONS|ON\s*\(?\d\)?\s*'?S)\b", re.IGNORECASE), (
        (re.compile(r"온\s*원스"), "1콤마에"),
        (re.compile(r"온\s*투스"), "2콤마에"),
    )),
)

# 인쇄 서식 문구(정규화: 영숫자만·대문자). **업계 공통 항목만** 둔다 —
# 스튜디오·작품 로고(KOTH의 `KING`/`HILL`, BM802의 `titmouse`/`BIG MOUTH`)는
# 여기 넣지 않는다. 작품마다 다를뿐더러, 로고는 머리글 줄 위라 유도된
# header_y로 이미 걸러진다.
_TEMPLATE_WORDS = {
    # DIALOG·EXP는 여기 넣지 않는다 — _DIALOG_RE가 받되 **머리글 줄에
    # 한정**해서 거른다(_is_template). 본문에 같은 낱말의 손글씨가 있다.
    "PRODNO", "FOOTAGE", "ANIMATOR", "SCENENO", "SHEETNO",
    "CAMERANOTES", "ACTION", "CONT", "SCENEDIRECTOR", "APPROVED",
    "PRODUCTIONNO", "ACT", "TRUCK", "BG", "LEVEL",
}
# 감지 토큰도 특정 양식에 매지 않는다 — 엑스시트라면 어느 하우스 것이든
# '대사/EXP 칸 머리글'과 '서식 라벨'이 인쇄돼 있다(KOTH `DIALOG`,
# BM802 `DIALO`·`DIAL 2`처럼 표기는 달라 정규식으로 받는다).
_DETECT_LABELS = {"ANIMATOR", "CAMERANOTES", "SHEETNO", "ACTION",
                  "PRODNO", "FOOTAGE", "SCENENO"}
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")

_local = threading.local()


def _new_engine(**kwargs):  # test seam(panel_ocr/slate_ocr 미러)
    from rapidocr_onnxruntime import RapidOCR  # 지연 import(번들 무관 경로 보호)
    return RapidOCR(**kwargs)


def _reset_engines() -> None:  # test seam
    global _local
    _local = threading.local()


def _get_engine():
    engine = getattr(_local, "engine", None)
    if engine is None:
        engine = _new_engine()
        _local.engine = engine
    return engine


def _decode_png(png_bytes: bytes):
    import numpy  # RapidOCR 전이의존 — 지연 import(panel_ocr._decode_png 미러)
    from PIL import Image
    with Image.open(io.BytesIO(png_bytes)) as im:
        return numpy.array(im.convert("RGB"))


def _norm(text: str) -> str:
    return _NON_ALNUM.sub("", text).upper()


def _ocr_page(doc: PdfDocument, page: int, dpi: int) -> list[tuple[tuple[float, float, float, float], str, float]]:
    """페이지를 OCR해 (bbox_pt, text, conf) 목록으로. 좌표는 pt로 환산."""
    arr = _decode_png(doc.render_png(page, dpi=dpi, annots=False))
    result, _ = _get_engine()(arr)
    out = []
    scale = 72.0 / dpi
    for box, text, conf in (result or []):
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        rect = (min(xs) * scale, min(ys) * scale, max(xs) * scale, max(ys) * scale)
        out.append((rect, str(text), float(conf)))
    return out


@dataclass(frozen=True)
class _Geometry:
    """페이지에서 읽어낸 시트 칸 구조(작품마다 다르다)."""
    header_y: float          # 이 위는 로고·머리글 — 노트 아님
    footer_y: float          # 이 아래는 푸터 라벨 — 노트 아님
    dialog_band: tuple[float, float] | None   # 대사·EXP 칸 전체 x 구간
    num_bands: tuple[tuple[float, float], ...]  # 프레임 번호 컬럼들
    col_edges: tuple[float, ...]              # 배치 한계로 쓸 칸 경계 x

    def limit_x1(self, cx: float, page_w: float) -> float:
        for edge in self.col_edges:
            if edge > cx + _BAND_PAD:
                return edge
        return page_w - 8.0


# ── 크롭 경계 절단 회수 ─────────────────────────────────────────────
# RapidOCR이 놓친 줄은 어느 크롭에도 안 들어가 **통째로 사라지고**, 번역기가
# 잘린 원문을 그럴듯하게 완성해 오역까지 만든다(`ANIM PANTS`→`바지/애니메이션`,
# 사람은 `바지, 액션 맞춰 애니`). 출력에도 커버리지 지표에도 안 잡히는
# 손실이라 전량 탐침으로 계량했다(2026-08-26): A3 116p에서 크롭의 30.7%가
# 코앞(≤15pt)에 주인 없는 손글씨를 두고 있고 **그 89%는 탐지 자체가 없었다**
# (필터 탈락이 아니다). A2 135p도 23.3%로 재현. 회수 실증(무작위 20장을 두
# 팔로 재전사): 새 낱말 획득 70%·**내용 손실 0**.
_CUT_MAX_GAP_PT = 15.0     # 이보다 멀면 내 노트의 다음 줄이 아니라 남의 것
_CUT_MIN_W_PT = 20.0       # 낱말 한 개 이상 — 그 아래는 동그라미 마커·획 조각
_CUT_H_PT = (7.0, 22.0)    # 대문자 높이 ≈12pt. 위로 벗어나면 작화다
_CUT_MIN_HOVERLAP = 0.5    # 내 크롭과 이만큼 가로로 겹쳐야 '내 줄'
_CUT_LINE_GAP_PX = 40      # 글자 사이 가로 틈(≈10pt) — 한 줄로 묶는다


def _merge_boxes(boxes: list[list[int]]) -> list[list[int]]:
    """낱글자 덩어리를 줄 단위로 묶는다 — 낱말 폭 조건은 줄에 걸어야 한다
    (`POSE`를 글자별로 보면 어느 것도 20pt를 넘지 못한다)."""
    out = [list(b) for b in boxes]
    merged = True
    while merged:
        merged = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                a, b = out[i], out[j]
                if (min(a[3], b[3]) - max(a[1], b[1]) > 0
                        and max(a[0], b[0]) - min(a[2], b[2]) <= _CUT_LINE_GAP_PX):
                    out[i] = [min(a[0], b[0]), min(a[1], b[1]),
                              max(a[2], b[2]), max(a[3], b[3])]
                    out.pop(j)
                    merged = True
                    break
            if merged:
                break
    return out


def _hits(a, b) -> bool:
    return not (a[0] >= b[2] or a[2] <= b[0] or a[1] >= b[3] or a[3] <= b[1])


def _absorb_cut_ink(doc: PdfDocument, page: int, notes: list[PdfBlock],
                    geom: _Geometry) -> list[PdfBlock]:
    """크롭 경계에서 잘린 손글씨를 블록 bbox에 되붙인다.

    `_expand_to_ink`는 상자에 **걸친** 덩어리만 담는다 — 옆 노트를 물지
    않으려는 의도적 설계다. 그래서 바로 아랫줄이 별개 덩어리면 영영 빠진다.
    여기서는 그 빈틈만 메운다: **어느 크롭에도 안 속한**(=지금은 그냥
    버려지는) 잉크를, 코앞이고 가로로 겹칠 때만 가져온다. 최악이라야 별개
    노트 둘이 하나로 합쳐지는 것인데, 지금은 아예 사라지고 오역까지 난다.

    ⚠**bbox를 넓혀야지 렌더 픽셀만 넓히면 안 된다** — 크롭 파일명이
    `page+int(x0)+int(y0)`이라 bbox가 그대로면 `render_crops`가 `exists()`로
    건너뛰고 `transcripts.json` 캐시도 낡은 채 살아남는다. bbox를 넓히면
    바뀐 크롭만 이름이 달라져 **증분 재전사**된다.
    """
    if not notes:
        return notes
    import cv2
    import numpy as np

    from ..handwriting_transcribe import (
        _LINE_RATIO,
        _MAX_GROW_PX,
        _dark,
        _is_textlike,
        crop_rect,
    )

    arr = _decode_png(doc.render_png(page, dpi=_CROP_DPI_CUT, annots=False))
    h, w = arr.shape[:2]
    scale = _CROP_DPI_CUT / 72.0
    rects = [crop_rect(arr, b.bbox) for b in notes]
    claimed = [r for r in rects if r is not None]
    # 인쇄 숫자 컬럼·대사칸은 아예 지운다 — 프레임 번호(1,2,3…)와 립싱크
    # 음소가 고아로 잡히고, 옆 손글씨와 한 줄로 병합돼 폭 조건을 통과한다
    # (실측: `W/W ACTION` 고아가 옆 칸 '4','5'와 붙어 폭 80pt로 잡혔다).
    page_ink = _dark(arr).astype(np.uint8)
    for lo, hi in (*geom.num_bands,
                   *((geom.dialog_band,) if geom.dialog_band else ())):
        a, b = max(int(lo * scale), 0), min(int(hi * scale), w)
        if b > a:
            page_ink[:, a:b] = 0

    out: list[PdfBlock] = []
    for b, rect in zip(notes, rects):
        grown = (b.bbox if rect is None
                 else _grow_over_cut(page_ink, b, rect, claimed, notes,
                                     geom, scale, w, h,
                                     cv2=cv2, line_ratio=_LINE_RATIO,
                                     max_grow=_MAX_GROW_PX,
                                     textlike=_is_textlike))
        out.append(b if grown == b.bbox else replace(b, bbox=grown))
    return out


def _grow_over_cut(page_ink, block, rect, claimed, notes, geom, scale, w, h,
                   *, cv2, line_ratio, max_grow, textlike):
    """한 크롭이 삼켜야 할 '주인 없는 줄'을 찾아 넓힌 bbox를 돌려준다."""
    cx0, cy0, cx1, cy1 = rect
    nx0, ny0 = max(cx0 - max_grow, 0), max(cy0 - max_grow, 0)
    nx1, ny1 = min(cx1 + max_grow, w), min(cy1 + max_grow, h)
    region = page_ink[ny0:ny1, nx0:nx1]
    if region.size == 0:
        return block.bbox
    ink = region.copy()
    ink[region.mean(axis=1) >= line_ratio, :] = 0   # 가로 프레임 줄
    ink[:, region.mean(axis=0) >= line_ratio] = 0   # 세로 칸 구분선
    n, _labels, stats, _c = cv2.connectedComponentsWithStats(ink, connectivity=8)

    loose = []
    for i in range(1, n):
        x, y, cw, ch, area = (int(v) for v in stats[i])
        if not textlike(cw, ch, area):
            continue
        g = [nx0 + x, ny0 + y, nx0 + x + cw, ny0 + y + ch]
        if any(_hits(g, o) for o in claimed):
            continue                       # 주인이 있으면 내 것이 아니다
        loose.append(g)
    if not loose:
        return block.bbox

    x0, y0, x1, y1 = block.bbox
    for g in _merge_boxes(loose):
        pt = (g[0] / scale, g[1] / scale, g[2] / scale, g[3] / scale)
        if not _is_cut_line(pt, rect, scale, geom):
            continue
        cand = (min(x0, pt[0]), min(y0, pt[1]), max(x1, pt[2]), max(y1, pt[3]))
        # ⚠고아 자체는 이웃을 안 물어도 **합집합은 사각형**이라 그 사각형이
        # 이웃 노트를 덮을 수 있다(A3 p065 실측: 거대 과병합 블록이 이웃 3개를
        # 새로 물었다). 새로 겹치면 그 흡수만 버린다.
        before = sum(1 for o in notes
                     if o.bbox != block.bbox and _hits(block.bbox, o.bbox))
        after = sum(1 for o in notes
                    if o.bbox != block.bbox and _hits(cand, o.bbox))
        if after > before:
            continue
        x0, y0, x1, y1 = cand
    return (x0, y0, x1, y1)


def _is_cut_line(pt, rect, scale, geom: _Geometry) -> bool:
    """이 줄이 '내 크롭에서 잘려나간 줄'로 볼 만한가."""
    lw, lh = pt[2] - pt[0], pt[3] - pt[1]
    if lw < _CUT_MIN_W_PT or not (_CUT_H_PT[0] <= lh <= _CUT_H_PT[1]):
        return False
    cy = (pt[1] + pt[3]) / 2
    if not (geom.header_y < cy < geom.footer_y):
        return False
    cx0, cy0, cx1, cy1 = (v / scale for v in rect)
    ox = min(pt[2], cx1) - max(pt[0], cx0)
    if ox < lw * _CUT_MIN_HOVERLAP:
        return False
    if pt[1] >= cy1:
        gap = pt[1] - cy1
    elif pt[3] <= cy0:
        gap = cy0 - pt[3]
    else:
        gap = 0.0
    return gap <= _CUT_MAX_GAP_PT


def _textlike_only(ink):
    """획 덩어리 중 **글자다운 것만** 남긴 마스크(화살표·작화선 제외).

    판정은 전사 크롭 확장과 같은 `_is_textlike`를 쓴다 — 같은 페이지를 두
    잣대로 보면 "여기 잉크 있음"의 뜻이 단계마다 달라진다."""
    import cv2
    import numpy as np

    from ..handwriting_transcribe import _is_textlike

    u8 = ink.astype(np.uint8)
    n, _labels, stats, _c = cv2.connectedComponentsWithStats(u8, connectivity=8)
    out = np.zeros_like(u8)
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        if _is_textlike(w, h, area):
            out[y:y + h, x:x + w] |= u8[y:y + h, x:x + w]
    return out.astype(bool)


# ── 좌우 1순위 인접 배치(2026-08-26 사용자 설계) ────────────────────
# "손글씨가 있는 영역을 사각형으로 타이트하게 감싸고, 같은 크기 상자를
#  상하좌우로 인접하게 놓되 좌우를 1순위로. 다른 글씨와 안 겹치게."
#
# 왜 이게 맞나(측정 근거):
#  · 앵커가 지금은 **느슨한 OCR 클러스터 상자**라 '인접'이 인접이 아니었다.
#    타이트 잉크 상자는 크롭을 굽는 `crop_rect`와 같은 기계로 이미 있다.
#  · 사람 주석은 원문보다 **작다** — A1 3,271건 실측 면적비 중앙 0.44,
#    82%가 원문 면적 이하. 그래서 "같은 크기 상자"는 대개 여유가 있다.
#  · 사람은 대부분 좌 또는 우에 놓는다(사용자 확인). 우리는 위 배치가 0%고
#    아래가 사람의 두 배였다.
#  · 같은 크기 인접이라 **멀리 날아갈 수 없다** — 옛 넓은 사다리 탈출이
#    만들던 99분위 207pt·최대 243pt 꼬리가 구조적으로 사라진다.
# ★페이지 크기 정규화(2026-08-31 정식화). 배치·글꼴의 pt 상수는 전부
# 1401 시트(너비 792pt) 실측에서 왔는데, 1603 시트는 같은 양식을 2200pt로
# 스캔한 것이라(행 격자 36.7pt = 12.96의 2.83배 ≈ 너비비 2.78) 9pt 글씨가
# 티끌이 됐다. 페이지 너비 비율로 아래 상수들을 일괄 스케일한다.
# ⚠추출 상수(_CLUSTER_PAD·_CUT_* 등)는 **안 건드린다** — 스케일하면 추출
# 캐시 지문·크롭 이름이 흔들려 전사 캐시(토큰)가 전멸한다. 배치·글꼴만.
# ⚠잡은 한 번에 하나씩 돌므로(전역 세마포어) 모듈 전역 재조정이 안전하다.
_REF_PAGE_W = 792.0
_SCALED_NAMES = ("_FONTSIZE", "_MIN_FONTSIZE", "_MIN_BOX_W", "_BELOW_H_CAP",
                 "_WIDE_FROM", "_SIDE_GAP", "_SIDE_MIN_H", "_SNAP_PAD",
                 "_SIDE_MIN_W", "_WRAP_PAD", "_BELOW_GAP", "_ABOVE_GAP",
                 "_TALL_H", "_DY_LADDER", "_BELOW_GAPS", "_SIDE_DYS",
                 "_FAR_LADDER", "_LINE_DYS", "_LINE_TOP_PAD", "_FREE_RADIUS",
                 "_MID_FONTSIZE")
_BASE_PT = None            # 최초 호출 때 현재값(=1401 기준)을 원본으로 저장
_CUR_SCALE = 1.0


def _apply_page_scale(page_w: float) -> None:
    """배치·글꼴 상수를 이 페이지 너비 기준으로 맞춘다(멱등)."""
    global _BASE_PT, _CUR_SCALE
    g = globals()
    if _BASE_PT is None:
        _BASE_PT = {n: g[n] for n in _SCALED_NAMES}
    scale = max(page_w, 1.0) / _REF_PAGE_W
    if abs(scale - _CUR_SCALE) < 0.02:
        return
    _CUR_SCALE = scale
    for n, base in _BASE_PT.items():
        g[n] = (tuple(v * scale for v in base) if isinstance(base, tuple)
                else base * scale)


_SIDE_FIRST = True        # 끄면 옛 후보 생성으로 되돌아간다(A/B용)
_SIDE_GAP = 3.0           # 원문 잉크와의 틈(기존 옆자리 관례와 동일)
_SIDE_DYS = (0.0, 16.0, -16.0)   # 좌우가 막혔을 때 살짝 위아래로 밀어 본다
_SIDE_INK_OK = 0.005      # 손글씨를 사실상 안 덮는다(하드) — 사람 관례
_SIDE_MIN_H = 12.0        # 한 줄은 들어가야 한다
# ★인접 후보를 보는 순서 = 사람 관례(2026-08-27 A2 전수 실측으로 재확정).
# 사람 납품본 1,577건을 **블록 bbox 기준**으로 세어 보면 오른쪽 27% · 아래
# 26% · 원문 안쪽 26% · 위 14% · **왼쪽 8%**다. 우리는 왼쪽이 27%였다 —
# 오른쪽이 막히면 곧장 왼쪽 여백(프레임 번호 칸)으로 갔기 때문이다.
# 왼쪽을 맨 뒤로 미루고 그 자리를 아래·위에 주는 것이 이 순서다.
_SIDE_ORDER = ("right", "below", "above", "left")
# 좌우 선호를 블록마다 번갈아(사용자 설계 2026-08-26). 켜면 홀수 번째 블록은
# 위 순서에서 right↔left를 맞바꿔 본다.
_ZIGZAG = False
# 좌우 자리는 **번역문이 줄바꿈 없이 들어갈 폭**이 있을 때만 쓴다(글이 그보다
# 짧으면 그 글 폭까지만 요구한다 — "붐."은 20pt면 충분하다). 좁은 틈에 세로로
# 길게 흘리느니 사람처럼 아래로 간다. 실측 스윕에서 18→45pt까지 단조 개선,
# 60pt 이상은 사실상 "자연폭"과 같아져 포화했다(같은 자리 29.1→30.2%).
_SIDE_MIN_W = 60.0
# ★줄 단위 배치(2026-09-03): 인접 통상자가 전부 막힌 **다중줄** 번역은 원문
# 손글씨 **행마다 한 줄씩** 곁에 앉힌다 — 사람 관례(A2 사람 납품본: `STEPS/
# BACK` → `뒤로.`·`스텝.`을 각 행 옆에). 빽빽한 페이지엔 통상자 높이의 빈자리가
# 없어도 행 옆 작은 틈은 거의 늘 있다. 통상자 곁이 성립하면 손대지 않는다
# (정상 배치 지표 무영향). 줄이 행보다 많으면 시도하지 않는다(대응 불명).
_LINE_SPLIT = True
_ROW_GAP_PX = 3           # 잉크 행 분리 최소 틈(100dpi px) — 획 끊김은 잇는다
_ROW_MIN_PX = 3           # 이보다 얇은 행은 잡티
_LINE_DYS = (0.0, 4.0, -4.0)    # 행 기준 미세 이동(pt, 스케일 대상)
_LINE_TOP_PAD = 2.0             # 상자 윗변을 행보다 살짝 위로(글리프 정렬)
# ★기둥 방지: 옆자리 상자 폭은 원문 폭이 상한이지만, 그 폭에서 번역이
# max(_TOWER_LINES_MIN, 원문 행 수) 줄을 넘으면 그만큼만 넓힌다. 실측 기둥
# (4줄+·폭 40pt 이하) 1603 66건·A2 51건 — 28자 번역이 원문 폭 40pt 상자에서
# 7줄로 흘렀다. 사람은 4행 원문 옆에 4줄을 넘기지 않는다.
_TOWER_LINES_MIN = 2
# ★빈자리 직접 탐색(막혔을 때만): 잉크+주석 마스크의 적분영상으로 원문 주변
# 반경 안에서 깨끗한 사각형을 찾는다 — 고정 사다리가 전부 막히는 빽빽한
# 페이지의 최후 수단. 정상 배치에는 관여하지 않는다. 넓은 사다리(±260pt)의
# 먼 자리보다 가까운 빈자리가 있으면 그쪽이 이긴다.
_FREE_RADIUS = 120.0      # 탐색 반경(pt, 스케일 대상)
_FREE_STEP_PX = 3         # 위치 격자(100dpi px)
_SI_ONLY_RE = re.compile(r"^S[I1]$")     # 전사가 SI를 `S1`로 읽는 변형 포함
# SI가 프레임 번호와 한 블록으로 병합된 변형(`2\nSI`→`2 슬로우 인`) — 토큰이
# 전부 {숫자, SI}뿐이면 통째로 타이밍 표기다(1603 실측, 사람은 SI 미번역).
_SI_TOKEN_RE = re.compile(r"^(?:\d+|S[I1])$")
# 인쇄 서식 문구 — 번역 대상이 아니다(사용자 지정 2026-08-31: 로고·판권·
# PROD NO·FOOTAGE 류 고정 용어). 추출 단계 _is_template이 머리글 밴드로
# 거르지만, 밴드 밖(로고 옆 판권줄 등)이나 손글씨와 병합된 것이 샌다
# (1603 실측 59건: © 13·KING OF THE HILL 22·PRODUCTION NO 6…). 여기(굽기
# 경로)서 지우면 추출 캐시를 건드리지 않는다. ⚠낱말 하나짜리(ACTION·CONT)는
# 실제 노트에도 나오므로 **여러 낱말 문구·단독 매치만** 싣는다.
_TEMPLATE_PHRASE_RES = tuple(re.compile(r) for r in (
    r"(?:C)?20TH\s*TELEVISION\s*ANIMATION",
    r"ALL\s*RIGHTS\s*RESERVED",
    r"(?:KING\s*)?OF\s*THE\s*HILL", r"KING\s*OF(?:\s*THE)?",
    r"PROD(?:UCTION)?\s*N[O0]\.?", r"CAMERA\s*NOTES", r"SCENE\s*DIRECTOR",
    r"SHEET\s*N[O0]\.?", r"SCENE\s*N[O0]\.?", r"DIALOG\s*EXP",
    r"^ANIMATOR$", r"^APPROVED$", r"^FOOTAGE$", r"^S?C\.?\s*\d+\)?$",
))
_JOIN_MAX_CHARS = 12   # 이 길이까지는 한 줄이 상자도 작고 읽기도 낫다
_WRAP_PAD = 4.0           # MuPDF 어피어런스가 rect 안쪽으로 먹는 여백
# 위·아래로 갈 때 원문에서 띄우는 거리. 사람은 아래를 넉넉히 띄운다(실측 중앙
# 21.9pt 대 위 8.9pt). 다만 그대로 22pt를 쓰면 다음 노트 영역까지 내려가
# 오히려 어긋난다 — 스윕 최적은 **아래 14pt · 위 6pt**(22pt는 −1.3%p).
_BELOW_GAP = 14.0
_ABOVE_GAP = 6.0
# ★긴 세로 스택은 예외 — 아래로 보내면 **읽기 시작점**에서 노트 높이만큼
# 멀어진다. 사용자 실물 지적(2026-08-25, p5 SMU 9줄 노트): "옆의 작화선
# 때문에 번역이 스택 아래로 150pt 밀렸다, 사람은 선 위에 겹쳐 왼쪽에
# 병기한다." 그래서 이보다 높은 노트에서만 왼쪽이 아래보다 앞선다.
_TALL_H = 60.0
_SNAP_PAD = 1.0           # 칸 경계 바로 아래 여유
_GRID_MIN_PX = 8          # 이보다 촘촘하면 괘선 격자가 아니라 검출 잡음


def _rule_xs(rule_cols) -> tuple[float, ...]:
    """세로 괘선 x 좌표(pt) — 연속 픽셀은 한 줄로 뭉친다(굵은 선 대비)."""
    import numpy as np

    xs_ = np.flatnonzero(rule_cols)
    if xs_.size == 0:
        return ()
    scale = _INK_DPI / 72.0
    groups = [float(xs_[0])]
    for x in xs_[1:]:
        if x - groups[-1] > 1.5:
            groups.append(float(x))
        else:
            groups[-1] = float(x)
    return tuple(g / scale for g in groups)


def _row_grid(rule_rows) -> tuple[float, float] | None:
    """가로 괘선에서 **균일 격자**(원점 pt, 칸 간격 pt)를 유도한다.

    시트의 한 칸은 전부 같은 간격이다(사용자 확인 2026-08-26). 그래서 개별
    괘선 좌표를 그대로 쓰는 것보다 피치를 재는 편이 안정적이다 — 굵은 줄이
    두 행으로 검출되거나 흐린 줄이 빠져도 격자는 흔들리지 않는다.
    간격은 이웃 괘선 간 거리의 **최빈값**으로 잡는다(중앙값은 굵은 줄이
    만드는 1px 간격에 끌려간다)."""
    import numpy as np

    ys = np.flatnonzero(rule_rows)
    if ys.size < 8:
        return None
    scale = _INK_DPI / 72.0
    # 연속 픽셀은 한 줄로 뭉친다(굵은 괘선이 2~3px로 잡힌다)
    groups = [float(ys[0])]
    for y in ys[1:]:
        if y - groups[-1] > 1.5:
            groups.append(float(y))
        else:
            groups[-1] = float(y)
    if len(groups) < 8:
        return None
    diffs = np.diff(np.array(groups))
    diffs = diffs[diffs >= _GRID_MIN_PX]
    if diffs.size < 5:
        return None
    pitch = float(np.bincount(np.round(diffs).astype(int)).argmax())
    if pitch < _GRID_MIN_PX:
        return None
    return (groups[0] / scale, pitch / scale)


def _snap_to_row(rect, grid, page_h: float):
    """상자 윗변을 **칸 경계 바로 아래**로 옮긴다 — 괘선이 글자를 가로지르지
    않게. 반 칸 이내로만 움직인다(멀리 끌어다 맞추면 원문에서 멀어지는
    대가가 이득보다 크다)."""
    if not grid:
        return rect
    origin, pitch = grid
    if pitch <= 0:
        return rect
    x0, y0, x1, y1 = rect
    k = round((y0 - origin) / pitch)
    target = origin + k * pitch + _SNAP_PAD
    if abs(target - y0) > pitch / 2 + _SNAP_PAD:
        return rect
    dy = target - y0
    if y1 + dy > page_h - 8.0 or y0 + dy < 8.0:
        return rect
    return (x0, y0 + dy, x1, y1 + dy)


def _tight_anchor(ink, block: PdfBlock) -> tuple[float, float, float, float]:
    """블록 안에서 **손글씨가 실제로 있는** 구간만 남긴 사각형(pt).

    잉크가 없으면(전부 괘선이었거나 마스크 실패) 블록 상자를 그대로 쓴다."""
    import numpy as np

    scale = _INK_DPI / 72.0
    h, w = ink.shape
    bx0, by0, bx1, by1 = block.bbox
    x0 = max(0, int(bx0 * scale)); y0 = max(0, int(by0 * scale))
    x1 = min(w, int(bx1 * scale)); y1 = min(h, int(by1 * scale))
    if x1 <= x0 or y1 <= y0:
        return block.bbox
    sub = ink[y0:y1, x0:x1]
    rows = np.flatnonzero(sub.any(axis=1))
    cols = np.flatnonzero(sub.any(axis=0))
    if rows.size == 0 or cols.size == 0:
        return block.bbox
    return ((x0 + int(cols[0])) / scale, (y0 + int(rows[0])) / scale,
            (x0 + int(cols[-1]) + 1) / scale, (y0 + int(rows[-1]) + 1) / scale)


def _ink_rows(ink, bbox: tuple[float, float, float, float],
              ) -> list[tuple[float, float, float, float]]:
    """블록 안 손글씨 **행** 사각형들(pt) — 위→아래. 가로 투영의 연속 구간.

    행마다 잉크의 x 범위도 재므로 "이 행의 오른쪽 끝"이 나온다 — 줄 단위
    배치가 행 옆 틈을 볼 때 쓴다. 잉크가 없으면 빈 목록."""
    import numpy as np

    scale = _INK_DPI / 72.0
    h, w = ink.shape
    x0 = max(0, int(bbox[0] * scale)); y0 = max(0, int(bbox[1] * scale))
    x1 = min(w, int(bbox[2] * scale) + 1); y1 = min(h, int(bbox[3] * scale) + 1)
    if x1 <= x0 or y1 <= y0:
        return []
    sub = ink[y0:y1, x0:x1]
    ys = np.flatnonzero(sub.any(axis=1))
    if ys.size == 0:
        return []
    runs: list[tuple[int, int]] = []
    start = prev = int(ys[0])
    for y in ys[1:]:
        y = int(y)
        if y - prev > _ROW_GAP_PX:
            runs.append((start, prev))
            start = y
        prev = y
    runs.append((start, prev))
    out = []
    for a, b in runs:
        if b - a + 1 < _ROW_MIN_PX:
            continue
        cols = np.flatnonzero(sub[a:b + 1].any(axis=0))
        out.append(((x0 + int(cols[0])) / scale, (y0 + a) / scale,
                    (x0 + int(cols[-1]) + 1) / scale, (y0 + b + 1) / scale))
    return out


def _width_for_lines(text: str, fontsize: float, max_lines: int) -> float:
    """이 글이 max_lines 줄 이하로 접히는 **가장 좁은** 폭(자연폭 상한).

    폭이 넓을수록 줄 수는 단조 감소하므로 글자 폭 단위로 넓혀 가며 찾는다."""
    want = _natural_width(text, fontsize)
    w = _MIN_BOX_W
    while w < want and len(_wrap_ko(text, w, fontsize)) > max_lines:
        w += fontsize
    return min(w, want)


def _wrap_ko(text: str, width: float, fontsize: float) -> list[str]:
    """상자 폭에 맞춰 **우리가 직접, 균형 있게** 줄을 접는다.

    ⚠MuPDF에게 맡기면 욕심껏 채워 접는다(greedy) — 마지막 낱말 하나가
    상자 오른쪽 끝에 홀로 걸리고, 그 자리가 이웃 주석과 나란하면 **그쪽
    것처럼 읽힌다**(2026-08-27 사용자 신고: `일어나 앉으며, 표정 변화.`가
    `일어나 / 앉으며, 표정 / 변화.`로 접혀 `표정`이 옆 노트 번역에 붙어
    보였다). 줄 수는 그대로 두되 **가장 긴 줄을 최소화**해 나누면
    `일어나 / 앉으며, / 표정 변화.`가 되어 낱말이 줄 첫머리에 온다.

    원문의 개행은 하드 브레이크로 지킨다(노트의 줄 구조가 의미 단위다).
    폭 근사는 `_natural_width`·`_estimate_height`와 같은 CJK 근사다."""
    usable = max(fontsize, width - _WRAP_PAD)

    def w_pt(n: int) -> float:
        return n * fontsize

    def pack(words: list[str], limit: float) -> list[str]:
        lines, cur = [], ""
        for word in words:
            cand = word if not cur else f"{cur} {word}"
            if not cur or w_pt(len(cand)) <= limit:
                cur = cand
            else:
                lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
        return lines

    out: list[str] = []
    for para in text.split("\n"):
        words = para.split()
        if not words:
            continue
        greedy = pack(words, usable)
        if len(greedy) <= 1:
            out.extend(greedy)
            continue
        # 같은 줄 수를 유지하는 가장 좁은 폭을 찾는다 = 가장 긴 줄 최소화
        lo = max(w_pt(len(w)) for w in words)
        hi = usable
        best = greedy
        while lo <= hi:
            mid = (lo + hi) / 2.0
            trial = pack(words, mid)
            if len(trial) <= len(greedy):
                best = trial
                hi = mid - 0.5
            else:
                lo = mid + 0.5
        out.extend(best)
    return out or [text]


def _wrapped_height(text: str, width: float, fontsize: float) -> float:
    """`_wrap_ko`가 실제로 만드는 줄 수로 높이를 잰다.

    옛 `_estimate_height`는 `chars_per_line = max(8, width/fontsize)`라
    좁은 상자에서 줄 수를 **과소 추정**했다 — 그래서 상자가 글보다 짧아
    마지막 줄이 밖으로 흘렀다."""
    return (len(_wrap_ko(text, width, fontsize)) + 0.5) * fontsize * 1.25


def _min_usable_w(ko_text: str, fontsize: float) -> float:
    """이 글을 넣어도 낱말이 안 쪼개지는 **최소 상자 폭**.

    글이 짧으면 그 글 폭까지만 요구한다("표정"은 18pt면 충분). 길면
    `_SIDE_MIN_W`(≈6~7자)까지 요구하고, 그보다 좁은 자리는 후보에서 뺀다.

    ⚠2026-08-27 실물 결함 2건의 공통 원인: A1 재번역 주석 3,618개 중
    **22.9%가 글 길이의 절반도 안 되는 폭**이었다(2.8%는 1/4 미만).
    p5 `인시덴탈 134 & 142가 오른쪽으로 드러난다.`(24자≈216pt)가 **폭 26pt**
    상자에 들어가 3자씩 여덟 줄로 흘러 이웃 주석과 겹쳤고, p9
    `일어나 앉으며, 표정 변화.`는 폭 67pt에서 `일어나/앉으며, 표정/변화.`로
    접히며 `표정`이 상자 오른쪽 끝에 걸려 **옆 주석에 붙어** 보였다."""
    return max(_MIN_BOX_W, min(_SIDE_MIN_W, _natural_width(ko_text, fontsize)))


def _side_candidates(anchor, block: PdfBlock, ko_text: str,
                     page_size: tuple[float, float], right_first: bool = True,
                     max_lines: int | None = None,
                     only: tuple[str, ...] | None = None):
    """앵커와 **같은 크기**의 인접 상자를 `_SIDE_ORDER` 순서로 내놓는다.

    ⛔좌우를 번갈아 보던 지그재그(2026-08-26)는 **뺐다**. 사람 대조로 재보니
    그것이 왼쪽 과다(27% 대 사람 8%)의 주범이었다 — 블록의 절반을 왼쪽부터
    보게 만드니 오른쪽에 자리가 있어도 왼쪽 여백이 먼저 걸렸다. 끄자 같은
    자리 비율이 22.8→28.4%로 올랐고, 지그재그가 막으려던 **주석끼리 충돌은
    늘지 않았다**(A2 전 구간 0쌍 유지).

    글이 같은 크기에 안 들어가면 폰트를 한 단계씩 줄이고, 그래도 넘치면
    **원문에서 멀어지는 쪽으로만** 상자를 늘린다(원문 쪽으로 늘리면 덮게 된다).
    """
    page_w, page_h = page_size
    ax0, ay0, ax1, ay1 = anchor
    aw = max(ax1 - ax0, _MIN_BOX_W)
    ah = max(ay1 - ay0, _SIDE_MIN_H)
    limit_x1 = block.limit_x1 if block.limit_x1 is not None else page_w - 8.0
    g = _SIDE_GAP
    def box(w, fontsize, min_w=_MIN_BOX_W):
        """원문과 같은 폭을 **상한**으로 두되, 자리가 좁으면 줄인다.
        같은 폭을 강요하면 넓은 노트가 칸 경계에 막혀 오른쪽을 못 쓰고
        왼쪽으로 밀린다(실측: 왼쪽 30% — 사람은 7~10%). 사람 주석 폭도
        원문의 0.61배(중앙)로 원문보다 좁다.

        단, 그 폭에서 번역이 max_lines 줄을 넘으면 넘지 않을 만큼만 넓힌다
        (_TOWER_LINES_MIN 근거) — 짧은 글은 영향 없다."""
        cap = aw
        if max_lines:
            cap = max(aw, _width_for_lines(ko_text, fontsize, max_lines))
        return max(min(cap, w), min_w) if w >= min_w else 0.0

    # 네 변 모두 같은 최소 폭을 요구한다 — 아래·위만 빠져 있던 탓에 18pt
    # 상자가 통과해 낱말이 쪼개졌다(_min_usable_w 근거 참조).
    side_min_w = _min_usable_w(ko_text, _FONTSIZE)

    for fontsize in (_FONTSIZE, _MID_FONTSIZE, _MIN_FONTSIZE):
        # ⚠오른쪽 자리를 **전부 소진한 뒤** 왼쪽으로 간다. 번갈아 내면
        # `왼쪽(dy=0)`이 `오른쪽(dy=16)`보다 먼저 걸려, 살짝 밀면 되는 자리를
        # 두고 왼쪽 빈 여백(프레임 번호 칸)으로 도망간다 — 옛 채점이 왼쪽으로
        # 몰리던 것과 같은 함정이다(실측: 왼쪽 30%→26%에서 더 안 내려갔다).
        def right_slots(fontsize=fontsize):
            w = box(limit_x1 - (ax1 + g), fontsize, side_min_w)
            if not w:
                return
            h = max(ah, _wrapped_height(ko_text, w, fontsize))
            for dy in _SIDE_DYS:
                yield (ax1 + g, ay0 + dy, ax1 + g + w, ay0 + dy + h), fontsize

        def left_slots(fontsize=fontsize):
            w = box((ax0 - g) - 8.0, fontsize, side_min_w)
            if not w:
                return
            h = max(ah, _wrapped_height(ko_text, w, fontsize))
            for dy in _SIDE_DYS:
                yield (ax0 - g - w, ay0 + dy, ax0 - g, ay0 + dy + h), fontsize

        # 아래·위는 원문과 **같은 왼쪽 끝**에서 시작한다(사람 관례: 세로로
        # 쌓아 쓴다). 아래는 그대로, 위는 상자 높이만큼 올린다.
        def below_slots(fontsize=fontsize):
            w = box(limit_x1 - ax0, fontsize, side_min_w)
            if not w:
                return
            h = max(ah, _wrapped_height(ko_text, w, fontsize))
            gb = _BELOW_GAP
            if ay1 + gb + h <= page_h - 8.0:
                yield (ax0, ay1 + gb, ax0 + w, ay1 + gb + h), fontsize

        def above_slots(fontsize=fontsize):
            w = box(limit_x1 - ax0, fontsize, side_min_w)
            if not w:
                return
            h = max(ah, _wrapped_height(ko_text, w, fontsize))
            ga = _ABOVE_GAP
            if ay0 - ga - h >= 8.0:
                yield (ax0, ay0 - ga - h, ax0 + w, ay0 - ga), fontsize

        gens = {"right": right_slots, "left": left_slots,
                "below": below_slots, "above": above_slots}
        order = (_SIDE_ORDER if ah <= _TALL_H
                 else ("right", "left", "below", "above"))
        if not right_first:                      # 지그재그 — 좌우만 맞바꾼다
            swap = {"right": "left", "left": "right"}
            order = tuple(swap.get(n, n) for n in order)
        for name in order:
            if only is not None and name not in only:
                continue
            yield from gens[name]()


def _is_scanned(doc: PdfDocument, page: int) -> bool:
    """이 페이지가 스캔본인가 — 페이지를 거의 덮는 이미지가 있으면 그렇다."""
    page_w, page_h = doc.page_size(page)
    area = page_w * page_h
    if area <= 0:
        return False
    for x0, y0, x1, y1 in doc.image_rects(page):
        if (x1 - x0) * (y1 - y0) >= area * _SCAN_COVER:
            return True
    return False


def _derive_geometry(items, page_w: float, page_h: float) -> _Geometry | None:
    """OCR 결과에서 칸 구조를 유도한다. 머리글 줄을 못 찾으면 None."""
    labelled = [(r, _norm(t)) for r, t, _c in items]
    header_hits = [(r, n) for r, n in labelled
                   if n in _HEADER_LABELS or _DIALOG_RE.match(n)]
    if not header_hits:
        return None
    # 머리글은 한 줄에 몰려 있다 — 가장 많이 모인 y 밴드를 고른다
    best, best_y = [], None
    for r0, _n0 in header_hits:
        cy = (r0[1] + r0[3]) / 2
        row = [(r, n) for r, n in header_hits
               if abs((r[1] + r[3]) / 2 - cy) <= _HEADER_ROW_TOL]
        if len(row) > len(best):
            best, best_y = row, cy
    if best_y is None or len(best) < 2:
        return None
    header_y = max(r[3] for r, _ in best)

    dialog = [r for r, n in best if _DIALOG_RE.match(n)]
    dialog_band = ((min(r[0] for r in dialog) - _BAND_PAD,
                    max(r[2] for r in dialog) + _BAND_PAD) if dialog else None)

    # 푸터: 페이지 아래쪽에 있는 서식 라벨(같은 라벨이 머리글에 있는 양식도
    # 있어서 — KOTH는 ANIMATOR가 머리글이다 — 위치로 가른다)
    foot = [r for r, n in labelled
            if n in _FOOTER_LABELS and r[1] > page_h * 0.8]
    footer_y = min((r[1] for r in foot), default=page_h)

    # 프레임 번호 컬럼: 숫자만 촘촘히 쌓인 x 구간(양식마다 위치·개수가 다르다)
    bins: dict[int, list[str]] = {}
    for r, n in labelled:
        if not (header_y < r[1] < footer_y):
            continue
        bins.setdefault(int(((r[0] + r[2]) / 2) // _NUM_BIN_PT), []).append(n)
    num_bands = []
    for b, vals in sorted(bins.items()):
        if len(vals) < _NUM_BIN_MIN:
            continue
        digits = sum(1 for v in vals if v.isdigit())
        if digits / len(vals) >= _NUM_BIN_RATIO:
            num_bands.append((b * _NUM_BIN_PT - _BAND_PAD,
                              (b + 1) * _NUM_BIN_PT + _BAND_PAD))
    merged: list[tuple[float, float]] = []
    for lo, hi in num_bands:
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], hi)
        else:
            merged.append((lo, hi))

    edges = sorted({r[0] - _BAND_PAD for r, _ in best} |
                   {lo for lo, _ in merged} |
                   ({dialog_band[0]} if dialog_band else set()))
    return _Geometry(header_y=header_y, footer_y=footer_y,
                     dialog_band=dialog_band, num_bands=tuple(merged),
                     col_edges=tuple(e for e in edges if e > 0))


# 엑스시트 줄 나누기 규칙(2026-08-27 사람 납품본 실측). 손글씨는 세로로 한두
# 낱말씩 쌓아 쓴 것이라 그 줄 구조를 그대로 옮기면 번역이 낱말 기둥이 된다 —
# 우리 주석 높이 중앙 28pt·3줄 이상 26% 대 **사람 13pt(=한 줄)·3%**, 2,105개
# 중 589개(28%)가 "3줄 이상 × 줄당 4자 이하"였다. 사람은 한 구를 이루는
# 연속 줄을 합치고(`LT`+`HAND`→`왼 손.`) 대상·동작이 바뀔 때만 줄을 바꾼다.
_XSHEET_LINE_RULE = (
    "These sources are handwritten X-sheet notes stacked VERTICALLY, one or "
    "two words per line. Do NOT mirror that stacking. Write the Korean the "
    "way a Korean animation staffer writes it on the sheet:\n"
    "- Merge consecutive source lines that form ONE Korean phrase onto a "
    "single line (e.g. \"LT\\nHAND\" → \"왼 손.\", \"NOD HEAD\\nUP\" → "
    "\"고개 위로 끄덕.\", \"RT\\nARM\\nUP\" → \"오른 팔 올린다.\").\n"
    "- Keep a line break ONLY between separate subjects or separate actions "
    "(e.g. \"AMBER\\nLEANS\" → \"앰버,\\n기울인다.\").\n"
    "- NEVER use more lines than the source. Most notes become ONE line; two "
    "lines is common; three or more is rare.\n")


class XsheetProfile:
    name = "xsheet"
    label = "엑스시트 (Exposure Sheet)"
    prompt_line_rule = _XSHEET_LINE_RULE

    @staticmethod
    def prompt_line_rule_now() -> str:
        """잡 시작 시점의 줄 규칙 = 고정 규칙 + 등장인물 이름표(운영자 파일 포함).
        pdf_run이 이 훅을 우선한다 — 정적 `prompt_line_rule`은 계약·테스트용."""
        return _XSHEET_LINE_RULE + _cast_prompt_block()

    def detect(self, doc: PdfDocument) -> bool:
        """텍스트 레이어가 없고(스캔) 저해상 OCR에서 시트 헤더 활자가 2개
        이상 읽히면 엑스시트로 본다. 텍스트가 있는 문서는 OCR 없이 즉시
        False — 스토리보드가 레지스트리에서 먼저 검사되므로 여기 오는
        텍스트 문서는 미지원 포맷뿐이지만, 그래도 OCR 비용은 아낀다."""
        pages = min(doc.page_count, _DETECT_PAGES)
        if pages == 0:
            return False
        for p in range(pages):
            # 판정 기준은 "텍스트 레이어가 없다"가 아니라 **페이지가 스캔
            # 이미지다**. 개정본·부분 번역본은 스캔 위에 텍스트가 얹혀 있어
            # (BM802_O005 실측: 한글 주석 70~138자 + 파일명 스탬프) 텍스트
            # 유무로 자르면 멀쩡한 엑스시트를 통째로 거른다. 스캔이 아닌
            # 페이지는 OCR 비용도 쓰지 않고 건너뛴다.
            if not _is_scanned(doc, p):
                continue
            seen: set[str] = set()
            for _rect, text, _conf in _ocr_page(doc, p, _DETECT_DPI):
                n = _norm(text)
                if n in _DETECT_LABELS:
                    seen.add(n)
                elif _DIALOG_RE.match(n):
                    seen.add("DIALOGCOL")      # 표기가 달라도 한 종류로
            if len(seen) >= 2:
                return True
        return False

    def extract_cache_key(self) -> str:
        """추출 결과 캐시의 지문(선택 훅 — pdf_run이 `getattr`로 찾는다).

        왜 필요한가: 엑스시트 추출은 전 페이지 RapidOCR이라 문서당 10~17분
        인데(A3 116p 10.7분·A1 188p 17분), 재번역은 배치·용어·번역만 다시
        하려는 것이라 같은 원본을 매번 다시 읽는다. 결과는 결정적이므로
        캐시가 성립한다. 페이지 병렬화는 실측 기각(ONNX가 이미 전 코어를
        쓴다 — 4스레드가 순차의 0.53배).

        ⚠**지문이 계약이다**. 추출 로직이나 상수가 바뀌었는데 지문이 같으면
        수정이 조용히 무시된다(오늘 동결본 혼선과 같은 계열의 함정). 그래서
        ①추출 경로 함수들의 **바이트코드**(로직 변경 포착)와 ②추출을
        좌우하는 **상수값**(값 변경 포착)을 함께 해시한다. 어느 한쪽만으로는
        새는 구멍이 있다 — 상수는 전역 이름으로 로드되므로 값만 바꾸면
        바이트코드가 그대로고, 로직만 바꾸면 상수 문자열이 그대로다.
        """
        import hashlib

        from .. import handwriting_transcribe as ht
        from ..handwriting_transcribe import _is_textlike, crop_rect, ink_bounds
        def _deep(code):
            """⚠`co_code`만 보면 **람다·컴프리헨션 안의 변경이 안 보인다**.
            2026-08-26 실측: `blocks.sort(key=lambda b: b.page)`를
            `(b.page, b.bbox[1], b.bbox[0])`로 바꿨는데 지문이 그대로여서
            추출 캐시가 적중했고, 정렬이 조용히 우회됐다. 람다는 별도 코드
            객체라 `co_consts`에 들어간다 — 재귀로 함께 해싱한다."""
            out = [code.co_code]
            for c in code.co_consts:
                if hasattr(c, "co_code"):
                    out.extend(_deep(c))
            return out

        logic = b"".join(
            part for fn in (
                XsheetProfile.extract, _ocr_page, _derive_geometry, _cluster,
                _is_template, _make_speaker_strip, _recover_header_notes,
                # 경계 절단 회수도 추출 결과를 바꾼다 — 전사 모듈에서 빌려
                # 쓰는 것까지 지문에 넣어야 한다(crop_rect가 1픽셀만 달라져도
                # '주인 있음' 판정이 뒤집혀 블록 bbox가 바뀐다).
                _absorb_cut_ink, _grow_over_cut, _is_cut_line, _merge_boxes,
                crop_rect, ink_bounds, _is_textlike,
            ) for part in _deep(fn.__code__))
        values = "|".join(str(v) for v in (
            _OCR_DPI, _SCAN_COVER, _HEADER_ROW_TOL, _BAND_PAD, _NUM_BIN_PT,
            _NUM_BIN_MIN, _NUM_BIN_RATIO, _PHONETIC_MAX_LEN, _CLUSTER_PAD,
            _AXIS_TOL,
            _MIN_NOTE_AREA, _MIN_RAW_ALNUM, _HDR_MIN_ALPHA, _HDR_POS_QUANT,
            _HDR_REPEAT_FRAC, _HDR_REPEAT_MIN, _STRIP_XPAD_L, _STRIP_XPAD_R,
            _RUN_GAP, _RUN_PH_MAXLEN, _DIALOG_RE.pattern,
            sorted(_HEADER_LABELS), sorted(_FOOTER_LABELS),
            sorted(_TEMPLATE_WORDS),
            _CROP_DPI_CUT, _CUT_MAX_GAP_PT, _CUT_MIN_W_PT, _CUT_H_PT,
            _CUT_MIN_HOVERLAP, _CUT_LINE_GAP_PX,
            ht._MARGIN_PT, ht._MAX_GROW_PX, ht._LINE_RATIO, ht._MIN_INK_SIDE,
            ht._MAX_TEXT_SIDE, ht._MIN_INK_FILL,
        ))
        return hashlib.sha256(logic + values.encode()).hexdigest()[:16]

    def extract(self, doc: PdfDocument) -> list[PdfBlock]:
        """전 페이지 OCR → 템플릿/음소/번호 컬럼 제외 → 근접 클러스터링.

        블록 text는 RapidOCR 원시 판독(손글씨라 신뢰 불가)이다 — 표시·번역
        전에 반드시 transcribe_blocks가 비전 CLI 전사로 교체한다. 원시
        판독을 그대로 두는 이유는 한글 재투입 안전장치(has_hangul)와
        디버깅 단서로 쓰기 위해서다."""
        blocks: list[PdfBlock] = []
        geom: _Geometry | None = None
        # (page, rect, raw_text, limit_x1) — 머리글 위 손글씨 회수 후보
        header_pool: list[tuple[int, tuple[float, float, float, float],
                                str, float | None]] = []
        pages_with_geom = 0
        for page in range(doc.page_count):
            page_w, page_h = doc.page_size(page)
            items = _ocr_page(doc, page, _OCR_DPI)
            # 양식은 문서 안에서 일정하다 — 머리글이 안 읽힌 페이지(표지 등)는
            # 직전 페이지에서 얻은 구조를 그대로 쓴다.
            geom = _derive_geometry(items, page_w, page_h) or geom
            if geom is None:
                continue
            pages_with_geom += 1
            candidates = []
            for rect, text, _conf in items:
                if rect[3] < geom.header_y:
                    # 머리글 위 — 본문이 아니라 회수 후보 풀로 보낸다.
                    # (옛 동작은 _is_template의 y-컷으로 전량 폐기였다.)
                    # 머리글 줄 밴드에 걸친 항목은 텍스트 무관 배제 — OCR이
                    # `DIALOG EXP`를 한 덩어리로 읽으면 어휘도 반복 게이트도
                    # 피한다(A2 실측 17건: 분절이 페이지마다 달라 반복
                    # 계수가 분산).
                    n = _norm(text)
                    if (rect[3] < geom.header_y - _HEADER_ROW_TOL
                            and n not in _TEMPLATE_WORDS
                            and n not in _FOOTER_LABELS
                            and not _DIALOG_RE.match(n)
                            and len(_ALPHA_RE.findall(text)) >= _HDR_MIN_ALPHA
                            and not has_hangul(text)):
                        header_pool.append(
                            (page, rect, text,
                             geom.limit_x1((rect[0] + rect[2]) / 2, page_w)))
                    continue
                if _is_template(rect, text, geom):
                    continue
                candidates.append((rect, text))
            page_notes: list[PdfBlock] = []
            for grp in _cluster(candidates):
                x0 = min(r[0] for r, _ in grp)
                y0 = min(r[1] for r, _ in grp)
                x1 = max(r[2] for r, _ in grp)
                y1 = max(r[3] for r, _ in grp)
                raw = " ".join(
                    t for _, t in sorted(grp, key=lambda it: (it[0][1], it[0][0])))
                if has_hangul(raw):
                    continue  # 번역 완료본 재투입 안전장치(공통 규칙)
                if ((x1 - x0) * (y1 - y0) < _MIN_NOTE_AREA
                        and len(_ALNUM_RE.findall(raw)) < _MIN_RAW_ALNUM):
                    continue  # 잡티(서클 마커·셀 번호 한 글자) — 전사 낭비
                page_notes.append(PdfBlock(
                    page=page, kind=NOTE_KIND, text=raw, bbox=(x0, y0, x1, y1),
                    limit_x1=geom.limit_x1((x0 + x1) / 2, page_w)))
            # 크롭 경계에서 잘린 줄을 되붙인다 — 탐지가 놓쳐 어느 크롭에도
            # 안 들어간 잉크는 지금 그냥 버려진다(전량 실측: 크롭의 23~31%).
            blocks.extend(_absorb_cut_ink(doc, page, page_notes, geom))
            blocks.extend(_make_speaker_strip(page, items, geom, page_w, page_h))
        blocks.extend(_recover_header_notes(header_pool, pages_with_geom))
        # 페이지 순서 불변식 복원 — place_with_doc의 잉크 캐시(_ink_cache)가
        # 페이지당 1회 렌더로 성립하는 전제다.
        blocks.sort(key=lambda b: b.page)
        return blocks

    # ---- 전사 훅(pdf_run이 getattr로 발견하는 optional 계약) ------------
    #
    # doc 락을 쥐는 렌더 단계와, 락이 필요 없는 느린 CLI 단계를 **반드시**
    # 분리한다 — 전사는 문서당 수십 분이라 한 훅으로 합치면 그동안 페이지
    # 미리보기 라우트(GET /page)가 doc 락에 통째로 막힌다.

    def render_transcribe_crops(self, doc: PdfDocument,
                                blocks: list[PdfBlock], job_dir: Path) -> None:
        from ..handwriting_transcribe import render_crops, render_strips
        strips = [b for b in blocks if b.kind == STRIP_KIND]
        render_crops(doc, [b for b in blocks if b.kind != STRIP_KIND], job_dir)
        render_strips(doc, strips, job_dir)

    # 에코(번역=원문) 그룹을 전용 프롬프트로 한 번 재번역한다(pdf_run이
    # getattr로 읽는 선택 플래그). 이름(HANK)은 LLM 기분에 따라 에코돼
    # 확률적으로 증발했다 — A2 실측 16건(2026-08-24).
    retry_echoed_groups = True

    def transcribe_blocks(self, blocks: list[PdfBlock], job_dir: Path,
                          should_continue: Callable[[], bool] | None = None,
                          on_progress: Callable[[float], None] | None = None,
                          engine: str | None = None) -> list[PdfBlock]:
        from ..handwriting_transcribe import scan_speaker_strips, transcribe
        strips = [b for b in blocks if b.kind == STRIP_KIND]
        notes = [b for b in blocks if b.kind != STRIP_KIND]
        out = transcribe(notes, job_dir, should_continue=should_continue,
                         on_progress=on_progress, engine=engine)
        if strips:
            scans = scan_speaker_strips(strips, job_dir, engine=engine)
            out = out + _synthesize_speakers(strips, scans, out)
            out.sort(key=lambda b: b.page)
        # 손글씨 약어 정규화 — `WT`(with)를 LLM이 wait로 읽어 `대기`가 됐다(1605
        # 실측 10건, 사람은 `음식들고 포즈로`).
        out = [replace(b, text=re.sub(r"\bWT\b", "W/", b.text)) for b in out]
        # 순수 코드 노트는 번역기 대신 결정적 해독(block.ko predecode 경로,
        # 판넬 약어와 동일) — 에코-드롭을 원천 우회한다.
        return [replace(b, ko=decoded) if (decoded := _decode_code_note(b.text))
                else b for b in out]

    @staticmethod
    def _finalize(ov: Overlay) -> Overlay:
        """고른 상자에 맞춰 **우리 줄바꿈을 텍스트에 박아** 돌려준다.

        MuPDF에게 접기를 맡기면 접는 자리를 우리가 모른다(_wrap_ko 근거).
        이미 그렇게 접힐 줄 수로 높이를 잡았으므로 상자는 그대로 둔다."""
        lines = _wrap_ko(ov.text, ov.rect[2] - ov.rect[0], ov.fontsize)
        text = "\n".join(lines)
        return ov if text == ov.text else replace(ov, text=text)

    def place_with_doc(self, block: PdfBlock, ko_text: str,
                       page_size: tuple[float, float],
                       doc: PdfDocument,
                       occupied: tuple | list = ()) -> Overlay:
        """전 후보를 (변위 + 잉크 + 폰트축소) 비용으로 채점해 최소를 고른다.

        사람 관례의 우선순위를 그대로 옮긴 것(2026-08-25 배치 전수 감사):
        ① 주석끼리는 **절대** 안 겹친다(하드 게이트 — 사람 심한 겹침 0쌍)
        ② 원문 곁이 최우선 — 옅은 잉크(작화선·괘선 흔적)는 감수한다(사람
           주석 밑 잉크 중앙 6.34%). "잉크 없는 첫 자리" 방식은 곁이 조금만
           지저분해도 사다리 끝으로 도망가 사람과 같은 자리 비율이 21.5%에
           그쳤다 — 채점 전환 + 아래 정식 후보로 39%(단독 크롭 쌍)로 올랐다.
        ③ 폰트 축소보다 옅은 잉크 위가 낫다(_W_FS).

        `place`(문서 없이)는 그대로 남긴다: 첫 후보를 돌려주므로 기존
        호출자·테스트의 계약이 바뀌지 않는다."""
        _apply_page_scale(page_size[0])
        try:
            ink = self._page_ink(doc, block.page)
        except Exception:  # noqa: BLE001 — 그림을 못 얻으면 옛 경로로
            logger.warning("pdf-translate: page %d 잉크 마스크 실패 — 기본 배치",
                           block.page)
            return self.place(block, ko_text, page_size)
        if _SIDE_FIRST:
            # 사람 순서: ①통상자를 원문 오른쪽에 → ②행마다 한 줄씩 곁에 →
            # ③통상자를 아래·위·왼쪽에. 옛 코드는 ①③만 있어서 다중줄 번역이
            # 오른쪽만 막히면 곧장 위·아래에 기둥으로 쌓였다(A2 p36 실물).
            side = self._first_clean_side(block, ko_text, page_size, ink,
                                          occupied, only=("right",))
            if side is not None:
                return self._finalize(side)
            if _LINE_SPLIT:
                split = self._place_lines(block, ko_text, page_size, ink,
                                          occupied)
                if split:
                    return [self._finalize(ov) for ov in split]
            side = self._first_clean_side(block, ko_text, page_size, ink,
                                          occupied,
                                          only=("below", "above", "left"))
            if side is not None:
                return self._finalize(side)
        best = self._score_candidates(
            self._candidates(block, ko_text, page_size),
            block, ko_text, page_size, ink, occupied)
        if best is None:
            return self.place(block, ko_text, page_size)
        # 전 후보가 이웃 주석을 침범할 때만(=벽 점수) 탐색을 넓힌다. 실측
        # 계기(A3 116p): 거대 주석(높이 557pt — 과병합 클러스터)이 칸을 메운
        # 페이지에서 작은 주석의 후보가 전멸해, 폴백이 그 안에 앉으며 심한
        # 겹침 4쌍이 났다(A2는 0쌍). 이 층은 **막혔을 때만** 켜지므로 정상
        # 배치(=사람 대조 지표)에는 영향이 없다.
        if best[0] >= _BLOCKED:
            wide = self._score_candidates(
                self._far_candidates(block, ko_text, page_size),
                block, ko_text, page_size, ink, occupied)
            if wide is not None and wide[0] < best[0]:
                best = wide
            # 사다리 밖의 가까운 빈자리 — 먼 사다리 자리·겹치는 자리보다 낫다
            free = self._free_slot_near(block, ko_text, page_size, ink,
                                        occupied)
            if free is not None and free[0] < best[0]:
                best = free
        return self._finalize(best[1])

    def _first_clean_side(self, block: PdfBlock, ko_text: str,
                          page_size: tuple[float, float], ink,
                          occupied, only: tuple[str, ...] | None = None,
                          ) -> Overlay | None:
        """좌우(→상하) 인접 자리 중 **아무것도 덮지 않는 첫 자리**.

        점수 최소화가 아니라 순서다 — 사람 관례가 "곁에, 좌우 먼저"라서
        그 순서를 그대로 따르는 편이 채점보다 재현이 정확하다. 하나도
        성립하지 않으면 None을 돌려 옛 채점 경로로 넘긴다."""
        anchor = _tight_anchor(ink, block)
        page_h = page_size[1]
        grid = getattr(self, "_row_grid", None)
        # 칸 경계는 세로 괘선에서 — geom.col_edges는 머리글 라벨 기반이라
        # 칸 한복판을 경계로 잡는 경우가 있다(위 _page_ink 주석 참조).
        edges = [x for x in getattr(self, "_col_edges", ()) if x > anchor[2] + 1]
        limit = min(edges) - 1.0 if edges else None
        blk = block if limit is None else replace(block, limit_x1=limit)
        right_first = (not _ZIGZAG) or len(occupied or ()) % 2 == 0
        rows = _ink_rows(ink, block.bbox)
        max_lines = max(_TOWER_LINES_MIN, len(rows)) if rows else None
        for rect, fontsize in _side_candidates(anchor, blk, ko_text,
                                               page_size, right_first,
                                               max_lines=max_lines, only=only):
            rect = _clamp_nondegenerate(
                *_snap_to_row(rect, grid, page_h), page_h)
            if _ink_ratio(ink, rect, page_h) > _SIDE_INK_OK:
                continue          # 손글씨를 덮으면 탈락(하드)
            if _occupied_frac(rect, occupied) > _OCC_OK:
                continue          # 이웃 주석과 겹치면 탈락(하드)
            return Overlay(page=block.page, rect=rect, text=ko_text,
                           fontsize=fontsize)
        return None

    def _score_candidates(self, candidates, block: PdfBlock, ko_text: str,
                          page_size: tuple[float, float], ink,
                          occupied) -> tuple[float, Overlay] | None:
        page_h = page_size[1]
        best: tuple[float, Overlay] | None = None
        for rect, fontsize, dpen in candidates:
            ink_score = _ink_ratio(ink, rect, page_h)
            occ = _occupied_frac(rect, occupied)
            # 전 후보 채점 후 전역 최소를 고른다 — "잉크 없는 첫 자리"로
            # 즉시 반환하던 옛 방식은 원문 곁이 조금만 지저분해도 사다리
            # 끝으로 도망갔다(전수 감사: 30pt 이내 21.5%·above 2배 과다).
            # 잉크는 _INK_OK까지 공짜, 그 위는 거리와 교환(_W_INK 근거 참조).
            score = (dpen + _W_INK * max(ink_score - _INK_OK, 0.0)
                     + _W_FS * (_FONTSIZE - fontsize))
            if ink_score > _FB_INK_HARD:
                # 손글씨를 이만큼 덮는 자리는 겹침과 같은 급의 벽 — 깨끗한
                # 자리가 하나라도 있으면 절대 못 이기고, 전멸일 때만 남는다.
                score += _BLOCKED + ink_score * 1000.0
            if occ > _OCC_OK:
                # 주석끼리 겹침은 **하드 게이트** — 사람은 잉크엔 겹쳐 써도
                # 주석끼리는 절대 안 겹친다(A2 실측: 사람 심한 겹침 0쌍 대
                # 우리 91쌍). 겹침 없는 후보가 하나라도 있으면 절대 못 이기는
                # 크기의 벽을 세우고, 전멸일 때만 가장 덜 겹치는 자리로.
                score += _BLOCKED + occ * 1000.0
            if best is None or score < best[0]:
                best = (score, Overlay(page=block.page, rect=rect,
                                       text=ko_text, fontsize=fontsize))
        return best

    def _column_limit(self, block: PdfBlock, page_w: float) -> float:
        """이 블록이 넘어가면 안 되는 오른쪽 x — 칸 경계는 세로 괘선에서."""
        limit_x1 = (block.limit_x1 if block.limit_x1 is not None
                    else page_w - 8.0)
        edges = [x for x in getattr(self, "_col_edges", ())
                 if x > block.bbox[2] + 1]
        return min(limit_x1, min(edges) - 1.0) if edges else limit_x1

    def _place_lines(self, block: PdfBlock, ko_text: str,
                     page_size: tuple[float, float], ink,
                     occupied) -> list[Overlay] | None:
        """다중줄 번역을 원문 **행마다 한 줄씩** 곁에 앉힌다(전부 성립할 때만).

        ko 줄 i ↔ 잉크 행 ⌊i·m/k⌋ (줄이 행보다 적으면 비례로 띄운다 — 번역
        규칙이 연속 원문 줄을 한 구절로 합치므로 앞 줄부터 순서가 보존된다).
        행마다 오른쪽 → 왼쪽, 미세 상하 이동, 폰트 한 단계 축소 순으로 손글씨
        (_SIDE_INK_OK)·이웃 주석(_OCC_OK)을 덮지 않는 첫 자리를 고른다. 한
        줄이라도 자리가 없으면 None — 반쪽 배치는 통상자 경로보다 못 읽힌다."""
        lines = [ln.strip() for ln in ko_text.split("\n") if ln.strip()]
        if len(lines) < 2:
            return None
        rows = _ink_rows(ink, block.bbox)
        if len(rows) < 2 or len(lines) > len(rows):
            return None
        page_w, page_h = page_size
        limit_x1 = self._column_limit(block, page_w)
        m, k = len(rows), len(lines)
        picks = [rows[(i * m) // k] for i in range(k)]
        placed: list[Overlay] = []
        local = list(occupied or ())
        for text, (rx0, ry0, rx1, _ry1) in zip(lines, picks):
            found: Overlay | None = None
            for fontsize in (_FONTSIZE, _MIN_FONTSIZE):
                w = _natural_width(text, fontsize)
                h = _wrapped_height(text, w, fontsize)
                for dy in _LINE_DYS:
                    top = ry0 - _LINE_TOP_PAD + dy
                    if top < 8.0 or top + h > page_h - 8.0:
                        continue
                    cands = []
                    if rx1 + _SIDE_GAP + w <= limit_x1:
                        cands.append((rx1 + _SIDE_GAP, top,
                                      rx1 + _SIDE_GAP + w, top + h))
                    if rx0 - _SIDE_GAP - w >= 8.0:
                        cands.append((rx0 - _SIDE_GAP - w, top,
                                      rx0 - _SIDE_GAP, top + h))
                    for rect in cands:
                        if _ink_ratio(ink, rect, page_h) > _SIDE_INK_OK:
                            continue
                        if _occupied_frac(rect, local) > _OCC_OK:
                            continue
                        found = Overlay(page=block.page, rect=rect,
                                        text=text, fontsize=fontsize)
                        break
                    if found is not None:
                        break
                if found is not None:
                    break
            if found is None:
                return None
            placed.append(found)
            local.append(found.rect)
        return placed

    def _free_slot_near(self, block: PdfBlock, ko_text: str,
                        page_size: tuple[float, float], ink,
                        occupied) -> tuple[float, Overlay] | None:
        """막혔을 때의 최후 수단 — 마스크 위에서 원문에 가장 가까운 빈 사각형.

        잉크(손글씨)와 기존 주석을 한 마스크에 올리고 적분영상으로 모든
        위치의 덮임을 한꺼번에 센다(격자 _FREE_STEP_PX). 비용 = 원문 상단과의
        세로 거리 + 가로 틈(+ 왼쪽이면 폭의 절반 — 기존 왼쪽 후보의 페널티와
        같은 취지). 돌려주는 비용은 채점 경로의 변위 점수와 같은 자(pt)라
        `_score_candidates` 결과와 직접 비교할 수 있다."""
        import numpy as np

        page_w, page_h = page_size
        scale = _INK_DPI / 72.0
        bx0, by0, bx1, by1 = block.bbox
        limit_x1 = self._column_limit(block, page_w)
        wx0 = max(8.0, bx0 - _FREE_RADIUS); wx1 = min(limit_x1, bx1 + _FREE_RADIUS)
        wy0 = max(8.0, by0 - _FREE_RADIUS); wy1 = min(page_h - 8.0, by1 + _FREE_RADIUS)
        ih, iw = ink.shape
        px0, py0 = max(0, int(wx0 * scale)), max(0, int(wy0 * scale))
        px1, py1 = min(iw, int(wx1 * scale)), min(ih, int(wy1 * scale))
        if px1 - px0 < 4 or py1 - py0 < 4:
            return None
        mask = ink[py0:py1, px0:px1].astype(np.uint8)
        for o in occupied or ():
            ox0 = max(0, int(o[0] * scale) - px0); oy0 = max(0, int(o[1] * scale) - py0)
            ox1 = min(px1 - px0, int(o[2] * scale) + 1 - px0)
            oy1 = min(py1 - py0, int(o[3] * scale) + 1 - py0)
            if ox1 > ox0 and oy1 > oy0:
                mask[oy0:oy1, ox0:ox1] = 1
        mh, mw = mask.shape
        integ = np.zeros((mh + 1, mw + 1), dtype=np.int32)
        integ[1:, 1:] = mask.cumsum(0).cumsum(1)
        best: tuple[float, Overlay] | None = None
        for fontsize in (_FONTSIZE, _MIN_FONTSIZE):
            widths = {_natural_width(ko_text, fontsize),
                      _width_for_lines(ko_text, fontsize, 2),
                      _width_for_lines(ko_text, fontsize, 3)}
            for wpt in sorted(widths, reverse=True):
                wpt = max(wpt, _min_usable_w(ko_text, fontsize))
                hpt = _wrapped_height(ko_text, wpt, fontsize)
                wp, hp = math.ceil(wpt * scale), math.ceil(hpt * scale)
                if wp >= mw or hp >= mh:
                    continue
                ys = np.arange(0, mh - hp, _FREE_STEP_PX)
                xs = np.arange(0, mw - wp, _FREE_STEP_PX)
                total = (integ[np.ix_(ys + hp, xs + wp)] - integ[np.ix_(ys, xs + wp)]
                         - integ[np.ix_(ys + hp, xs)] + integ[np.ix_(ys, xs)])
                ok = total <= _SIDE_INK_OK * wp * hp
                if not ok.any():
                    continue
                yy, xx = np.nonzero(ok)
                tops = (py0 + ys[yy]) / scale
                lefts = (px0 + xs[xx]) / scale
                rights = lefts + wpt
                dx = np.maximum(0.0, np.maximum(bx0 - rights, lefts - bx1))
                cost = (dx + np.abs(tops - by0)
                        + np.where(rights <= bx0, 0.5 * wpt, 0.0)
                        + _W_FS * (_FONTSIZE - fontsize))
                i = int(cost.argmin())
                if best is None or cost[i] < best[0]:
                    rect = _clamp_nondegenerate(
                        float(lefts[i]), float(tops[i]), float(rights[i]),
                        float(tops[i]) + hpt, page_h)
                    best = (float(cost[i]), Overlay(
                        page=block.page, rect=rect, text=ko_text,
                        fontsize=fontsize))
        return best

    def _far_candidates(self, block: PdfBlock, ko_text: str,
                        page_size: tuple[float, float]):
        """막힌 경우에만 쓰는 넓은 사다리 — 원문 위아래로 더 멀리 밀어 본다.

        변위를 그대로 비용으로 실어 보내므로(먼 자리는 그만큼 나쁜 점수),
        겹치지 않는 자리가 있으면 그쪽이 이기고 없으면 원래 후보가 남는다."""
        bx0, by0, bx1, _by1 = block.bbox
        page_w, page_h = page_size
        limit_x1 = block.limit_x1 if block.limit_x1 is not None else page_w - 8.0
        want = _natural_width(ko_text, _FONTSIZE)
        height = _wrapped_height(ko_text, want, _FONTSIZE)
        for dy in _FAR_LADDER:
            top = by0 + dy
            if top < 8.0 or top + height > page_h - 8.0:
                continue
            for x0 in (bx1 + 3.0, bx0 - 3.0 - want, bx0):
                if x0 < 8.0 or x0 + want > limit_x1:
                    continue
                yield (_clamp_nondegenerate(x0, top, x0 + want, top + height,
                                            page_h), _FONTSIZE, abs(dy))

    def _page_ink(self, doc: PdfDocument, page: int):
        """이 페이지의 손글씨 마스크. 블록이 페이지 순서로 오므로 한 장만 캔다."""
        cached = getattr(self, "_ink_cache", None)
        if cached is not None and cached[0] == page:
            return cached[1]
        arr = _decode_png(doc.render_png(page, dpi=_INK_DPI, annots=False))
        gray = arr[:, :, :3].mean(axis=2) if arr.ndim == 3 else arr
        ink = gray < _INK_DARK
        # 인쇄 괘선(가로 줄·세로 칸선)은 어디에나 있어서 빼지 않으면 모든
        # 자리가 '잉크 있음'이 된다 — 실측에서 우리·사람 모두 100%로 나왔다.
        rule_rows = ink.mean(axis=1) > _RULE_FILL
        rule_cols = ink.mean(axis=0) > _RULE_FILL
        ink[rule_rows, :] = False
        ink[:, rule_cols] = False
        # 세로 괘선 = **진짜 칸 경계**. `geom.col_edges`는 머리글 라벨의 글자
        # 시작 x에서 뽑는데 `ACTION` 라벨이 칸 가운데 정렬이라 그 시작점이
        # 칸 한복판에 가짜 경계를 만든다 — A1 p54 실측: limit_x1이 163pt인데
        # 노트가 x=201까지 뻗어 **오른쪽 여유가 음수**였다. 그래서 오른쪽
        # 배치가 구조적으로 막혀 주석이 왼쪽으로 몰렸다.
        self._col_edges = _rule_xs(rule_cols)
        # 지우기 전에 가로 괘선 y를 챙겨 둔다 — 주석을 **칸 사이**에 넣으면
        # 줄이 글자를 가로지르지 않아 읽기 쉬워진다(사용자 지적 2026-08-26).
        # 시트 행 피치(≈12.5pt)와 9pt 글줄 높이가 거의 같아, 윗변만 괘선
        # 바로 아래에 맞추면 여러 줄도 자연히 칸마다 한 줄씩 앉는다.
        self._row_grid = _row_grid(rule_rows)
        # ★손글씨만 남긴다 — 화살표·작화 웨이브선처럼 길고 성긴 획은 뺀다.
        # 이걸 안 하면 마스크가 "무엇이든 획"이라, 배치가 **작화 선을 피하는
        # 것과 남의 손글씨를 덮지 않는 것을 구분하지 못한다**. 두 사용자
        # 지적이 정확히 그 경계에서 갈렸다:
        #   08-25 "긴 스택 번역이 아래로 150pt 밀렸다 — 사람은 **작화 선 위에
        #          겹쳐** 왼쪽에 병기한다"      → 선은 감수해야 한다
        #   08-26 "원문과 겹치는 순간 가독성이 망가진다" → 글씨는 덮으면 안 된다
        # 전사 경로(`_is_textlike`)와 같은 잣대를 쓴다.
        ink = _textlike_only(ink)
        self._ink_cache = (page, ink)
        return ink

    def place(self, block: PdfBlock, ko_text: str,
              page_size: tuple[float, float]) -> Overlay:
        """사람 관례(원문 옆 병기) 재현: 오른쪽 여백 → 왼쪽 여백 → 아래 순.

        열 경계(block.limit_x1)를 넘지 않는다 — 액션 노트의 번역이 DIALOG
        음소 컬럼 위로 넘어가면 립싱크 표를 가린다.

        **상자는 남은 자리가 아니라 글에 맞춘다**(`_natural_width`). 예전에는
        양쪽 다 가용 폭을 통째로 먹었고(오른쪽 = 칸 경계까지, 왼쪽 = 페이지
        좌단까지) 그 결과 A2 전수에서 폭 중앙값이 사람 30pt 대 우리 104.8pt,
        폭 100pt 초과가 사람 2.5% 대 우리 52.7%였다. 넓은 상자에 글이 왼쪽
        정렬로 그려지니 번역문이 원문에서 멀리 떨어져 앉거나 옆 노트의
        손글씨 위로 올라탔다(사용자 실물 지적, 2026-08-21).

        왼쪽 배치는 **원문에 붙인다**. 예전 코드는 상자를 페이지 좌단
        `8.0`에 고정해서, 원문이 오른쪽에 있을수록 번역문이 멀어졌다 —
        전수에서 우리 주석의 45.4%가 페이지 가장자리에 붙었고 사람은
        0.1%(2,015개 중 2개)뿐이었다."""
        rect, fontsize, _dpen = next(iter(
            self._candidates(block, ko_text, page_size)))
        return Overlay(page=block.page, rect=rect, text=ko_text,
                       fontsize=fontsize)

    def _candidates(self, block: PdfBlock, ko_text: str,
                    page_size: tuple[float, float]):
        """놓을 만한 자리를 (rect, fontsize, 변위pt)로 흘린다 — 오른쪽·왼쪽·아래.

        `place`는 첫 후보를 쓰고, `place_with_doc`는 변위·잉크·겹침을
        묶어 채점한다. 한 곳에서 만들어야 두 경로가 갈라지지 않는다.

        변위 = 원문 **상단** 기준 이동량. 옆 배치는 |dy|, 아래 배치는
        틈 + 블록 높이 절반(`_BELOW_H_W` 근거 참조) — 읽기 시작점에서
        얼마나 떨어졌는지가 사람 관례의 축이다.

        아래 배치는 최후 예비가 아니라 **정식 후보**다(전수 감사: 사람
        below 13% 대 우리 5% — 옛 코드는 아래를 최소 폰트 딱 한 자리만
        만들었다). 같은 변에서도 세로로 조금씩 밀어 본다: 엑스시트는
        노트가 세로로 빽빽해서, 옆자리가 막혀도 반 줄 아래는 흔히 빈다.
        """
        _apply_page_scale(page_size[0])
        page_w, page_h = page_size
        bx0, by0, bx1, by1 = block.bbox
        limit_x1 = block.limit_x1 if block.limit_x1 is not None else page_w - 8.0
        below_pen = _BELOW_H_W * min(by1 - by0, _BELOW_H_CAP)
        # 넓은 클러스터의 좌단 바깥은 내용어에서 원문 폭만큼 먼 자리다
        wide_pen = _WIDE_L_W * max((bx1 - bx0) - _WIDE_FROM, 0.0)

        for fontsize in (_FONTSIZE, _MID_FONTSIZE, _MIN_FONTSIZE):
            want = _natural_width(ko_text, fontsize)
            need = _min_usable_w(ko_text, fontsize)
            for dy in _DY_LADDER:
                top = by0 + dy
                if top < 8.0:
                    continue
                # 오른쪽: 원문 끝에 붙여 필요한 만큼만
                avail = limit_x1 - (bx1 + 3.0)
                if avail >= need:
                    width = min(want, avail)
                    height = _wrapped_height(ko_text, width, fontsize)
                    if top + height <= page_h - 8.0:
                        yield (_clamp_nondegenerate(
                            bx1 + 3.0, top, bx1 + 3.0 + width, top + height,
                            page_h), fontsize, abs(dy))
                # 왼쪽: 원문 시작에 붙여 왼쪽으로 필요한 만큼만
                avail = (bx0 - 3.0) - 8.0
                if avail >= need:
                    width = min(want, avail)
                    right = bx0 - 3.0
                    height = _wrapped_height(ko_text, width, fontsize)
                    if top + height <= page_h - 8.0:
                        yield (_clamp_nondegenerate(
                            right - width, top, right, top + height,
                            page_h), fontsize,
                            abs(dy) + wide_pen + _LEFT_FAR_W * (width + 3.0))
            # 아래: 좌단 정렬 + (넓은 원문은) 우단 정렬 변형, 글에 맞춘 폭
            avail = limit_x1 - bx0
            if avail >= need:
                width = min(want, avail)
                height = _wrapped_height(ko_text, width, fontsize)
                right_x1 = min(bx1, limit_x1)
                for gap in _BELOW_GAPS:
                    top = by1 + gap
                    if top + height > page_h - 8.0:
                        continue
                    yield (_clamp_nondegenerate(
                        bx0, top, bx0 + width, top + height,
                        page_h), fontsize, gap + below_pen)
                    # 우단 정렬: 사람은 문장형 원문의 끝자락 아래에 단다
                    if bx1 - bx0 > _WIDE_FROM and right_x1 - width > bx0:
                        yield (_clamp_nondegenerate(
                            right_x1 - width, top, right_x1, top + height,
                            page_h), fontsize, gap + below_pen)
        # 최후 예비 — 양옆·아래가 전부 성립 안 해도(좁은 칸·페이지 끝) 후보
        # 0개가 되면 안 된다(place는 첫 후보를 무조건 쓴다). 옛 꼬리 배치
        # 그대로, 변위 페널티만 뒤로 밀어 정상 후보가 있으면 절대 안 이긴다.
        # ⚠블록 폭으로 자르지 않는다(2026-08-31): 좁은 원문(세로 손글씨) 폭에
        # 맞추면 긴 번역이 낱말 기둥으로 흘렀다(1603 `손끝으로/털실을/…` 7줄).
        width = max(_MIN_BOX_W, min(_natural_width(ko_text, _MIN_FONTSIZE),
                                    _min_usable_w(ko_text, _FONTSIZE) * 2.0))
        x1 = min(limit_x1, bx0 + width)
        height = _wrapped_height(ko_text, x1 - bx0, _MIN_FONTSIZE)
        yield (_clamp_nondegenerate(bx0, by1 + 2.0, x1, by1 + 2.0 + height,
                                    page_h), _MIN_FONTSIZE,
               _BELOW_GAPS[-1] + below_pen + 1.0)

    def refine_ko(self, block: PdfBlock, ko_text: str) -> str:
        """엑스시트 하우스 용어로 KO→KO 교정(`_HOUSE_KO_XSHEET` 근거 참조).

        `FormatProfile` Protocol에는 등록하지 않는다 — storyboard.refine_ko와
        같은 선택 훅이고, overlay_plan이 `getattr`로 발견한다.

        왜 house_style.py(전역)가 아닌가: `대사`는 스토리보드 DIALOG 칸의
        정답이라 전역 치환하면 다른 포맷이 깨진다. 반대로 엑스시트에서는
        사람이 예외 없이 `대화`를 쓴다(29/0). 포맷마다 정답이 다른 용어는
        프로파일이 들고 있어야 한다.

        `_HOUSE_KO_XSHEET_BY_SRC`가 원문 조건부인 이유: 같은 한국어라도 원문에
        따라 사람 표기가 갈린다(TILT는 `기웃`, LEAN은 `기울인다`). `정지`·`스틸`
        처럼 다른 문맥에선 정당한 낱말도 있어서, 원문 없이 무조건 치환하면
        멀쩡한 말을 덮는다."""
        # ★SI(slow-in)는 번역하지 않는다 — 1603 사람 납품본 실측(2026-08-31):
        # STL→안착 81·OVS→오버슛 56·REF→참고 53은 꼬박꼬박 옮기면서 **SI는
        # 2,868건 중 0건**. 타이밍 기호로 보고 남겨 두는 관례다. 우리는 75건
        # 전부 `슬로우 인`으로 달아 B서클 옆 좁은 자리를 어지럽혔다. 빈 문자열
        # 반환 = build_plan이 주석을 만들지 않는 설계된 드롭 경로.
        src_norm = _norm(block.text or "")
        if _SI_ONLY_RE.match(src_norm):
            return ""
        tokens = [t for t in re.split(r"[^A-Za-z0-9]+", (block.text or "").upper()) if t]
        if tokens and any(t in ("SI", "S1") for t in tokens) and all(
                _SI_TOKEN_RE.match(t) for t in tokens):
            return ""          # `2 SI`류 — 프레임 번호+타이밍 표기 묶음
        # 인쇄 서식 문구를 걷어내고 알파벳·숫자가 3자 미만 남으면 서식이다.
        # (문구 "일부"만 서식이면 남은 손글씨는 살린다 — `FOOTAGE WALK CONT.`)
        stripped = " ".join((block.text or "").upper().split())
        for pat in _TEMPLATE_PHRASE_RES:
            stripped = pat.sub(" ", stripped)
        if len(_ALNUM_RE.findall(stripped)) < 3:
            return ""
        out = ko_text
        for pat, rep in _HOUSE_KO_XSHEET:
            out = pat.sub(rep, out)
        src = block.text or ""
        for src_pat, rules in _HOUSE_KO_XSHEET_BY_SRC:
            if not src_pat.search(src):
                continue
            for pat, rep in rules:
                out = pat.sub(rep, out)
        out = _clean_leftover_codes(src, out)
        out = _fix_cast_names(src, out)
        if "_" in src:
            # 손글씨의 밑줄 연결(`SAND_CLOTH`)이 번역문에 남는다 — 사람은 `&`로 잇는다
            out = re.sub(r"(?<=\S)\s*_\s*(?=\S)", "&", out)
        out = _tidy_lines(out)
        # ★짧은 번역은 한 줄로 — 사람은 주석을 사실상 전부 한 줄로 쓴다
        # (1603 실측 2,868건 중 **1줄 100%**, 상자 높이 중앙 9pt). 우리는 원문
        # 세로 쌓기를 따라 41%만 1줄이라 상자가 3배 높았고, 큰 상자는 빈자리에
        # 못 들어가 폴백으로 밀리며 손글씨를 덮었다. 합쳐서 낱말 몇 개 수준
        # (_JOIN_MAX_CHARS)이면 개행을 공백으로 접는다.
        lines = [ln.strip() for ln in out.split("\n") if ln.strip()]
        if len(lines) > 1 and sum(len(ln) for ln in lines) <= _JOIN_MAX_CHARS:
            out = " ".join(lines)
        return out


# 번역문에 **원문 코드가 그대로** 남는 경우(1605_A1 실측 117건/3,706: `STL\n뒤로.`,
# `OVS 약간`, `제스쳐, SI.`). 코드만 있는 노트는 `_decode_code_note`가 해독하지만
# 낱말과 섞이면 LLM이 코드를 복사한다. 사람은 STL→안착·OVS→오버슛으로 옮기고
# SI는 아예 쓰지 않는다(1603·1605 납품본 동일).
_LEFT_STL_RE = re.compile(r"(?<![A-Za-z가-힣])STLS?(?![A-Za-z])")
_LEFT_OVS_RE = re.compile(r"(?<![A-Za-z가-힣])OVS(?![A-Za-z])")
_LEFT_SI_RE = re.compile(r"[\s,]*\(?(?<![A-Za-z])S[I1](?![A-Za-z0-9])\)?")
_DANGLING_RE = re.compile(r"\s+([.,])")


def _tidy_lines(ko: str) -> str:
    """규칙이 낱말을 지운 뒤의 찌꺼기 정리 — ` .`→`.`, 부호만 남은 줄 삭제."""
    lines = []
    for ln in ko.split("\n"):
        ln = _DANGLING_RE.sub(r"\1", ln).strip()
        ln = re.sub(r"^[,.]\s*", "", ln)
        if ln and not re.fullmatch(r"[.,&/\-\s]+", ln):
            lines.append(ln)
    return "\n".join(lines)


def _fix_cast_names(src: str, ko: str) -> str:
    """원문에 인물 이름이 있으면 그 이름의 알려진 오음역을 표기로 바꾼다.

    두 글자 이상 오음역은 뒤에 조사가 붙을 수 있어(`차네가`) 뒤쪽 경계를 열고,
    한 글자(`찬`)는 `찬다` 같은 낱말 안을 건드리지 않게 양쪽을 막는다."""
    for en, canon in _cast_table().items():
        if not re.search(rf"(?<![A-Za-z]){re.escape(en)}(?![A-Za-z])", src):
            continue
        for bad in _CAST_VARIANTS.get(canon, ()):
            tail = r"(?![가-힣])" if len(bad) == 1 else r"(?!\w*[A-Za-z])"
            pat = rf"(?<![가-힣]){bad}{tail}"
            if _has_batchim(canon):
                pat += r"([가는를와])?"
                ko = re.sub(pat, lambda m, c=canon: c + _PARTICLE_AFTER_BATCHIM.get(
                    m.group(1) or "", m.group(1) or ""), ko)
            else:
                ko = re.sub(pat, canon, ko)
    return ko


def _clean_leftover_codes(src: str, ko: str) -> str:
    if re.search(r"\bSTLS?\b", src):
        ko = _LEFT_STL_RE.sub("안착", ko)
    if re.search(r"\bOVS\b", src):
        ko = _LEFT_OVS_RE.sub("오버슛", ko)
    if re.search(r"\bS[I1]\b", src) and _LEFT_SI_RE.search(ko):
        lines = []
        for ln in ko.split("\n"):
            ln = _DANGLING_RE.sub(r"\1", _LEFT_SI_RE.sub("", ln)).strip()
            if ln and ln not in (".", ","):
                lines.append(ln)
        if lines and lines[-1].endswith(","):       # `포즈로,` + `SI.` → `포즈로.`
            lines[-1] = lines[-1][:-1] + "."
        ko = "\n".join(lines)
    return ko


def _is_template(rect: tuple[float, float, float, float], text: str,
                 geom: _Geometry) -> bool:
    """인쇄 서식·번역 대상이 아닌 것을 걸러낸다(좌표는 geom에서 온다)."""
    x0, y0, x1, y1 = rect
    if y1 < geom.header_y or y0 > geom.footer_y:
        return True
    n = _norm(text)
    if n in _TEMPLATE_WORDS or n in _FOOTER_LABELS:
        return True
    # DIAL/EXP 텍스트 매치는 **머리글 줄에 한정**한다 — 본문에도 같은 낱말의
    # 손글씨가 있다(`EYES EXP`→시선.표정, `#78 DIAL`→78,대화. A2 실측: 위치
    # 무관 필터가 사람이 번역한 노트 33건을 지웠고, p47 (177,215) 'EXP'는
    # OCR이 잡은 것을 필터가 죽이는 걸 직접 확인). 인쇄 라벨은 머리글 줄
    # 안에 있어 y0가 header_y(줄의 최대 y1)보다 작다.
    if _DIALOG_RE.match(n) and y0 <= geom.header_y:
        return True
    cx = (x0 + x1) / 2
    for lo, hi in geom.num_bands:
        if lo <= cx <= hi and (n.isdigit() or len(n) <= 2):
            return True
    if geom.dialog_band is not None:
        lo, hi = geom.dialog_band
        # 대사 칸에서 짧은 토큰 = 립싱크 음소(aa·ay·uw…) — 사람도 번역하지
        # 않는다. 화자 이름(HARRIS…)은 길어서 살아남는다(BM802 실측: 사람이
        # `해리슨`으로 옮겼다).
        if lo <= cx <= hi and len(n) <= _PHONETIC_MAX_LEN:
            return True
    return False


def _make_speaker_strip(page: int, items, geom: _Geometry,
                        page_w: float, page_h: float) -> list[PdfBlock]:
    """음소 런이 있는 페이지의 화자 스트립 의사 블록(0 또는 1개).

    text에 구조(JSON: 런 시작 y들·밴드·스트립 상단)를 싣는다 — 전사
    단계(_consume_speaker_strips)가 읽고 소비한다."""
    if geom.dialog_band is None:
        return []
    lo, hi = geom.dialog_band
    ph = sorted((r for r, t, _c in items
                 if lo <= (r[0] + r[2]) / 2 <= hi
                 and r[1] > geom.header_y + 10
                 and len(_norm(t)) <= _RUN_PH_MAXLEN),
                key=lambda r: r[1])
    runs: list[float] = []
    last_y1 = None
    for r in ph:
        if last_y1 is None or r[1] - last_y1 > _RUN_GAP:
            runs.append(round(r[1], 1))
        last_y1 = r[3]
    # 런이 없어도 스트립은 낸다 — 음소가 OCR에 안 잡힌 페이지에도 이름은
    # 있을 수 있고(A2 실측: 이름 누락 10건이 런-미검출 페이지), 그때
    # 합성은 스캔된 이름의 제 위치를 그대로 쓴다.
    x0 = max(lo - _STRIP_XPAD_L, 0.0)
    y0 = geom.header_y + 2.0
    x1 = min(hi + _STRIP_XPAD_R, page_w)
    y1 = min(geom.footer_y - 2.0, page_h)
    ctx = {"runs": runs, "band": [lo, hi], "top": round(y0, 1)}
    return [PdfBlock(page=page, kind=STRIP_KIND, text=json.dumps(ctx),
                     bbox=(x0, y0, x1, y1), limit_x1=None)]


def _synthesize_speakers(strips: list[PdfBlock], scans: dict,
                         existing: list[PdfBlock]) -> list[PdfBlock]:
    """스트립 스캔 결과 → 런에 스냅한 화자 노트 블록.

    이름이 없는 페이지의 첫 런이 스트립 상단에 붙어 있으면(=앞 페이지에서
    이어지는 대사) 직전 화자를 이어 기재한다 — 사람 번역자의 관례를
    형식화한 것. 새 화자 선언 없이 런이 중간에서 시작하면 추측하지
    않는다(오표기가 누락보다 나쁘다)."""
    from ..handwriting_transcribe import crop_name

    by_page_existing: dict[int, list[PdfBlock]] = {}
    for b in existing:
        by_page_existing.setdefault(b.page, []).append(b)
    out: list[PdfBlock] = []
    last_speaker: str | None = None
    last_speaker_page = -99
    for sb in sorted(strips, key=lambda b: b.page):
        try:
            ctx = json.loads(sb.text)
        except (TypeError, ValueError):
            continue
        runs = [float(v) for v in ctx.get("runs", [])]
        lo = float(ctx.get("band", [0, 0])[0])
        top = float(ctx.get("top", sb.bbox[1]))
        y0s, y1s = sb.bbox[1], sb.bbox[3]
        names: list[tuple[str, float]] = []
        for it in (scans.get(crop_name(sb)) or [])[:6]:
            t = str(it.get("text") or "").strip()
            if len(_ALPHA_RE.findall(t)) < 2 or has_hangul(t):
                continue
            y_abs = y0s + float(it.get("y") or 0.0) * (y1s - y0s)
            names.append((t, y_abs))
        names.sort(key=lambda nv: nv[1])
        # 런에 화자 배정 — 정밀 안전판: ①이름이 런 위 _ASSIGN_MAX_PT 안에
        # 있을 때만(멀리서 전파하면 스캔이 놓친 이름 자리에 엉뚱한 이름이
        # 들어간다 — 오표기 10건 실측) ②이월은 직전 페이지에서 본 화자를
        # 페이지-톱 연속 런에만.
        found: list[tuple[str, float]] = []
        for i, run_y in enumerate(runs):
            above = [(t, y) for t, y in names if y <= run_y + 30.0]
            if above and run_y - above[-1][1] <= _ASSIGN_MAX_PT:
                found.append((above[-1][0], run_y))
            elif (i == 0 and last_speaker is not None
                  and last_speaker_page == sb.page - 1
                  and run_y - top <= _CARRY_TOP_PT):
                found.append((last_speaker, run_y))
        # 어느 런에도 안 붙는 이름(런 미검출 페이지 포함)은 제 위치에
        for t, y in names:
            if all(abs(y - r) > _SNAP_PT for r in runs):
                found.append((t, y))
        if names:
            last_speaker = names[-1][0]
            last_speaker_page = sb.page
        for t, y in found:
            cx, cy = lo - 70.0 + _SPK_W / 2, y + _SPK_H / 2
            near = any(
                abs((b.bbox[0] + b.bbox[2]) / 2 - cx) <= _SPK_DEDUP_PT
                and abs((b.bbox[1] + b.bbox[3]) / 2 - cy) <= _SPK_DEDUP_PT
                for b in by_page_existing.get(sb.page, []))
            last_speaker = t
            if near:
                continue
            out.append(PdfBlock(
                page=sb.page, kind=NOTE_KIND, text=t,
                bbox=(lo - 70.0, y, lo - 70.0 + _SPK_W, y + _SPK_H),
                limit_x1=lo))
    return out


def _decode_code_note(text: str) -> str | None:
    """노트의 **모든** 토큰이 코드 사전에 있을 때만 결정적 해독을 돌려준다.

    부분 해독 금지 — `LT ARM`처럼 사전 밖 토큰이 섞이면 통째로 번역기
    몫이다(반쪽 해독은 어순·조사가 깨진다). 줄 구조는 보존한다."""
    if not (text or "").strip():
        return None
    out_lines = []
    for line in text.splitlines():
        toks = line.split()
        if not toks:
            continue
        decoded = []
        cast = _cast_table()
        for t in toks:
            key = t.strip(".,").upper()
            ko = _CODE_KO.get(key) or cast.get(key)
            if ko is None and _PASS_TOKEN_RE.match(t.strip(",")):
                ko = t.strip(",.")
            if ko is None:
                return None
            decoded.append(ko)
        out_lines.append(" ".join(decoded))
    return "\n".join(out_lines) if out_lines else None


def _recover_header_notes(pool, pages_with_geom: int) -> list[PdfBlock]:
    """머리글 위 손글씨(페이지 상단 캐릭터 이름 등)를 회수한다.

    구분 원리: 인쇄 타이틀·고정 칸의 글은 **매 페이지 같은 자리**에 찍히고
    손글씨는 자리가 흔들린다. 같은 양자화 위치가 문턱 이상의 페이지에서
    반복되면 서식으로 보고 버린다. 1페이지 문서는 반복 판별이 불가능하므로
    회수를 끈다(타이틀이 통째로 새는 것보다 옛 동작 유지가 낫다)."""
    if pages_with_geom < 2:
        return []

    def qkey(rect):
        return tuple(round(v / _HDR_POS_QUANT) for v in rect)

    pages_by_pos: dict[tuple, set[int]] = {}
    for page, rect, _text, _lx in pool:
        pages_by_pos.setdefault(qkey(rect), set()).add(page)
    thresh = max(_HDR_REPEAT_MIN, round(pages_with_geom * _HDR_REPEAT_FRAC))
    out: list[PdfBlock] = []
    for page, rect, text, lx in pool:
        if len(pages_by_pos[qkey(rect)]) >= thresh:
            continue
        out.append(PdfBlock(page=page, kind=NOTE_KIND, text=text,
                            bbox=rect, limit_x1=lx))
    return out


def _cluster(items: list[tuple[tuple[float, float, float, float], str]],
             pad: float = _CLUSTER_PAD):
    """줄 단위 OCR 박스를 노트 하나로 재조립한다 — 엑스시트 노트는 세로로
    단어를 쌓아 쓰는 관례라(SUBTLE/TREMBLE/ON/HANK…) 그래야 사람 주석 1개와
    1:1이 된다.

    ★**대각선으로만 가까운 것은 잇지 않는다**(2026-08-27). 옛 규칙은 패딩
    rect 겹침이라 세로로도 가로로도 안 겹치면서 비스듬히 가까운 다른 칼럼의
    글자를 체인으로 끌어왔다 — A2 p36 실측: `DRT`(x135-181)와
    `AMBERS`(x76-129)가 가로 6.2pt·세로 5.5pt 대각선으로 붙어, 서로 다른
    두 세로 스택 13박스가 144×162pt 한 덩어리가 됐다(사람 기준 노트 4개).
    그 덩어리는 번역도 13줄짜리 한 뭉치로 나와 여백에 낱말 기둥으로 쌓인다.
    이제는 **같은 세로 스택**(가로가 겹침)이나 **같은 줄**(세로가 겹침)만
    잇는다. 비용은 거의 없다(밀집 10페이지 실측 블록 294→302)."""
    n = len(items)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    span = 2.0 * pad          # 이어 붙일 수 있는 최대 틈(옛 규칙과 동일)
    for i in range(n):
        xi0, yi0, xi1, yi1 = items[i][0]
        for j in range(i + 1, n):
            xj0, yj0, xj1, yj1 = items[j][0]
            gx = max(xj0 - xi1, xi0 - xj1, 0.0)
            gy = max(yj0 - yi1, yi0 - yj1, 0.0)
            same_col = gx <= _AXIS_TOL and gy <= span
            same_row = gy <= _AXIS_TOL and gx <= span
            if same_col or same_row:
                parent[find(i)] = find(j)
    groups: dict[int, list] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(items[i])
    return list(groups.values())
