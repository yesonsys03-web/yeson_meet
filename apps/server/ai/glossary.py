# === ANCHOR: GLOSSARY_START ===
"""Animation-production translation glossary for live captions.

The default term map lives in code so a fresh install already renders studio
pipeline vocabulary correctly (e.g. "cleanup" -> "클린업", not the literal
"청소"). Operators can override or extend it WITHOUT rebuilding the frozen
server by dropping a plain-text file at ``{STORAGE_ROOT}/glossary.txt`` (or any
path named by ``YESON_GLOSSARY_PATH``). Each non-empty, non-comment line maps
one English term to its Korean caption form::

    cleanup => 클린업
    inbetween => 인비트윈

Lines starting with ``#`` are comments. ``=>``, a tab, or ``=`` separates the
two sides. File entries override same-key defaults (case-insensitive) and append
new terms. Changes are picked up on the next translation (mtime-checked), so a
server restart is not required.

스코프(scope) 축: 이 사전은 원래 회의 자막용으로 튜닝돼 있어서, 그대로 자막
메이커(작품 대사)에 붙이면 회의 전용 항목이 대사를 망친다(정원 장면의 "씨앗을
심어"가 "주제로 던져"로 깨지는 식). ``"meeting"``(기본)은 오늘까지의 동작
그대로이고, ``"dialogue"``는 회의 전용 항목 몇 개를 내장 기본값에서 뺀 뒤
``glossary_dialogue.txt`` / ``glossary_ko_dialogue.txt``로 마지막에 덮는다.
"""
from __future__ import annotations

import os
from pathlib import Path

GLOSSARY_PATH_ENV = "YESON_GLOSSARY_PATH"
GLOSSARY_ENABLED_ENV = "GEMINI_GLOSSARY_ENABLED"
STORAGE_ROOT_ENV = "STORAGE_ROOT"
DEFAULT_STORAGE_ROOT = "/var/lib/yeson-meet/storage"
GLOSSARY_FILENAME = "glossary.txt"
KO_CORRECTIONS_PATH_ENV = "YESON_GLOSSARY_KO_PATH"
KO_CORRECTIONS_FILENAME = "glossary_ko.txt"
# 대사(자막 메이커) 전용 오버라이드 — 회의용 파일을 상속한 뒤 마지막에 덮는다.
GLOSSARY_DIALOGUE_PATH_ENV = "YESON_GLOSSARY_DIALOGUE_PATH"
GLOSSARY_DIALOGUE_FILENAME = "glossary_dialogue.txt"
KO_CORRECTIONS_DIALOGUE_PATH_ENV = "YESON_GLOSSARY_KO_DIALOGUE_PATH"
KO_CORRECTIONS_DIALOGUE_FILENAME = "glossary_ko_dialogue.txt"

# meeting = 라이브 회의(기본). dialogue = 자막 메이커의 작품 대사.
SCOPE_MEETING = "meeting"
SCOPE_DIALOGUE = "dialogue"
_SCOPES = (SCOPE_MEETING, SCOPE_DIALOGUE)

# English term -> Korean caption rendering. Targeted at this studio's 2D
# pipeline (Toon Boom Harmony / Adobe Photoshop) plus the production/management
# vocabulary that comes up in meetings — deliberately NOT the full 3D /
# stop-motion / Hollywood universe, which mostly collides with plain English.
# Transliterated loanword forms are preferred over literal Korean, matching
# studio convention. Edit freely via the override file.
DEFAULT_GLOSSARY: list[tuple[str, str]] = [
    # --- Pipeline stages (drawing) ---
    ("cleanup", "클린업"),
    ("clean-up", "클린업"),
    ("layout", "레이아웃"),
    ("rough", "러프"),
    ("tie-down", "타이다운"),
    ("key animation", "키 애니메이션"),
    ("keyframe", "키프레임"),
    ("key pose", "키 포즈"),
    ("pose", "포즈"),
    ("breakdown", "브레이크다운"),
    ("inbetween", "인비트윈"),
    ("in-between", "인비트윈"),
    ("pencil test", "펜슬 테스트"),
    ("line test", "라인 테스트"),
    # --- Motion principles ---
    ("timing", "타이밍"),
    ("spacing", "스페이싱"),
    ("squash and stretch", "스쿼시 앤 스트레치"),
    ("anticipation", "앤티시페이션"),
    ("follow through", "팔로우 스루"),
    ("overlap", "오버랩"),
    ("arc", "아크"),
    ("hold", "홀드"),
    ("cycle", "사이클"),
    ("walk cycle", "워크 사이클"),
    ("loop", "루프"),
    ("lip sync", "립싱크"),
    # --- Pre-production ---
    ("storyboard", "스토리보드"),
    ("animatic", "애니매틱"),
    ("beat board", "비트 보드"),
    ("color script", "컬러 스크립트"),
    ("model sheet", "모델시트"),
    ("character sheet", "캐릭터 시트"),
    ("prop", "프롭"),
    ("thumbnail", "썸네일"),
    # --- Color / ink & paint ---
    ("coloring", "컬러"),
    ("paint", "페인트"),
    ("line", "라인"),
    ("line art", "라인아트"),
    ("line work", "라인워크"),
    ("palette", "팔레트"),
    ("color model", "컬러 모델"),
    ("color key", "컬러키"),
    ("shadow", "섀도우"),
    ("highlight", "하이라이트"),
    ("tone", "톤"),
    ("background", "배경"),
    # 이 바닥에서 environment는 "환경"이 아니라 배경/세트다(실기 2026-08-04
    # 보고서: 같은 문단에서 배경/환경으로 갈렸다).
    ("environment", "배경"),
    # --- Toon Boom Harmony specifics ---
    ("node", "노드"),
    ("node view", "노드 뷰"),
    ("peg", "페그"),
    ("pegbar", "페그바"),
    ("deformer", "디포머"),
    ("master controller", "마스터 컨트롤러"),
    ("cutter", "커터"),
    ("drawing substitution", "드로잉 서브스티튜션"),
    ("brush", "브러시"),
    ("pencil", "펜슬"),
    ("stroke", "스트로크"),
    ("onion skin", "어니언 스킨"),
    ("timeline", "타임라인"),
    ("library", "라이브러리"),
    ("template", "템플릿"),
    ("rig", "리그"),
    ("rigging", "리깅"),
    ("bone", "본"),
    ("curve", "커브"),
    ("cut-out", "컷아웃"),
    ("exposure sheet", "엑스시트"),
    ("x-sheet", "엑스시트"),
    ("dope sheet", "엑스시트"),
    # --- Compositing / camera ---
    ("compositing", "컴포지팅"),
    ("comp", "컴포지팅"),
    ("layer", "레이어"),
    ("multiplane", "멀티플레인"),
    ("camera", "카메라"),
    ("pan", "팬"),
    ("zoom", "줌"),
    ("dissolve", "디졸브"),
    ("fade", "페이드"),
    ("effects", "이펙트"),
    ("element", "엘리먼트"),
    ("render", "렌더"),
    # --- Production / management ---
    ("shot", "샷"),
    ("cut", "컷"),
    ("scene", "씬"),
    ("sequence", "시퀀스"),
    ("episode", "에피소드"),
    ("frame", "프레임"),
    ("asset", "에셋"),
    ("retake", "리테이크"),
    ("revision", "리비전"),
    ("feedback", "피드백"),
    ("approval", "승인"),
    ("deadline", "데드라인"),
    ("delivery", "딜리버리"),
    ("deliverable", "산출물"),
    ("milestone", "마일스톤"),
    ("dailies", "데일리"),
    ("push out", "밀림"),
    # 진척 계획. "예측/예상치"로 갈리던 것을 하나로 고정(실기 2026-08-04).
    ("projection", "프로젝션"),
    # 포스트 공정 이름 — 동사로 읽히면 "하나를 찍으세요" 같은 명령문이 나온다
    # (실기 2026-08-04: "take one from three or four" → "세 개 중에 하나를 찍으세요").
    ("take one", "테이크 원"),
    ("take two", "테이크 투"),
    ("picture lock", "픽처 락"),
    # STT 오인식 복구 — 연음에서 "on our" /ɒn ˈaʊər/ 와 "an hour" /ən ˈaʊər/ 가
    # 같은 소리라 3.5가 더 흔한 쪽으로 적는다(실기 2026-08-04: "17 assets to
    # receive on our side" → "...an hour side" → "한 시간 분량입니다").
    # 좌변이 3단어라 안전하다 — 정상 영어에서 "an hour" 뒤에 "side"가 올 일이
    # 없다. "an hour"만 잡으면 "in an hour"(한 시간 뒤)까지 망가진다.
    ("an hour side", "저희 쪽"),
    # 회의 관용구 — "씨앗을 심어두고" 직역 관측(2026-07-28 보고서). 뜻으로 옮긴다.
    ("plant the seed", "미리 주제로 던져 두다"),
    ("footage", "풋티지"),
    ("frame rate", "프레임레이트"),
    ("resolution", "해상도"),
    ("aspect ratio", "화면비"),
    ("pipeline", "파이프라인"),
    # 제작관리 툴(Autodesk ShotGrid, 구 Shotgun) — 띄어 말해도 툴명이라 붙인다
    # ("샷 그리드에서 찾아볼 수도" 실측 2026-07-29 보고서).
    ("ShotGrid", "샷그리드"),
    ("shot grid", "샷그리드"),
    # 의상·모자 가장자리 장식 — "그녀의 트림"(한국어 '트림'과 충돌) 실측
    # (2026-07-29). 음차('트리밍')는 사진 크롭·미용과 겹쳐 뜻으로 옮긴다
    # ("어떤 각도에서는 그녀의 테두리 장식이 안 보이도록" — 사용자 확정).
    ("trim", "테두리 장식"),
    # --- Photoshop ---
    ("layer mask", "레이어 마스크"),
    ("clipping mask", "클리핑 마스크"),
    ("channel", "채널"),
    ("alpha", "알파"),
    ("alpha channel", "알파 채널"),
    ("opacity", "불투명도"),
    ("blending mode", "블렌딩 모드"),
    ("blend mode", "블렌딩 모드"),
    ("selection", "선택영역"),
    ("lasso", "올가미"),
    ("gradient", "그라디언트"),
    ("eyedropper", "스포이드"),
    ("swatch", "스와치"),
    ("canvas", "캔버스"),
    ("crop", "크롭"),
    ("adjustment layer", "조정 레이어"),
    ("smart object", "스마트 오브젝트"),
    ("dpi", "DPI"),
    # --- Tools ---
    ("Harmony", "하모니"),
    ("Photoshop", "포토샵"),
    ("Adobe", "어도비"),
    # --- Proper nouns (steers transcription too — "Yeson" was heard as
    # "yes on" and dropped from the Korean caption in a real meeting) ---
    ("Yeson", "예손"),
]

# dialogue 스코프에서 DEFAULT_GLOSSARY에서 빼는 회의 전용 항목(대소문자 무시로
# EN 키와 비교). ★제외는 **내장 기본값에만** 적용한다 — 운영자가 glossary.txt에
# 같은 키를 직접 다시 적었다면 그건 운영자의 결정이므로 살린다. 대사 전용 파일이
# 기존 merge_glossary 규칙으로 언제든 다시 덮을 수 있는 게 탈출구다.
_DIALOGUE_EXCLUDE_EN: frozenset[str] = frozenset(
    s.lower()
    for s in (
        # 회의 STT의 연음 오인식("on our side" → "an hour side") 복구 전용.
        # 작품 대사엔 그 오인식 맥락이 없어 이득 없이 위험만 남는다.
        "an hour side",
        # 회의 관용구를 뜻으로 푸는 항목. 정원·농사 장면에선 정말로 씨를 심는
        # 대사라 뜻풀이가 오히려 오역이 된다.
        "plant the seed",
    )
)

_SEPARATORS = ("=>", "\t", "=")

# mtime-keyed cache so repeated translation calls don't re-read/re-parse the
# file, while a freshly edited glossary is still picked up without a restart.
# 스코프마다 독립 슬롯 — 회의용 캐시가 대사용 결과로 덮이면 안 된다.
_cache: dict[str, dict[str, object]] = {
    scope: {"key": None, "terms": None, "block": None} for scope in _SCOPES
}


def _check_scope(scope: str) -> str:
    """모르는 스코프는 조용히 meeting으로 떨어뜨리지 않는다 — 오타 하나가 회의용
    사전을 작품 대사에 그대로 붙이는 사고가 이 축을 만든 이유다."""
    if scope not in _SCOPES:
        raise ValueError(f"unknown glossary scope: {scope!r} (expected {_SCOPES})")
    return scope


def _storage_path(path_env: str, filename: str) -> Path:
    explicit = os.environ.get(path_env)
    if explicit:
        return Path(explicit)
    root = os.environ.get(STORAGE_ROOT_ENV) or DEFAULT_STORAGE_ROOT
    return Path(root) / filename


def _candidate_path() -> Path:
    return _storage_path(GLOSSARY_PATH_ENV, GLOSSARY_FILENAME)


def _glossary_sources(scope: str) -> list[Path]:
    """스코프가 읽는 오버라이드 파일들 — 뒤에 오는 파일이 앞을 덮는다.

    dialogue는 회의용 파일을 '상속'한 뒤 대사 전용 파일로 마지막에 덮는다.
    """
    paths = [_candidate_path()]
    if _check_scope(scope) == SCOPE_DIALOGUE:
        paths.append(
            _storage_path(GLOSSARY_DIALOGUE_PATH_ENV, GLOSSARY_DIALOGUE_FILENAME)
        )
    return paths


def _ko_sources(scope: str) -> list[Path]:
    """사후 교정 쪽 동일 규칙 — 회의용 파일 뒤에 대사 전용 파일."""
    paths = [_ko_corrections_path()]
    if _check_scope(scope) == SCOPE_DIALOGUE:
        paths.append(
            _storage_path(
                KO_CORRECTIONS_DIALOGUE_PATH_ENV, KO_CORRECTIONS_DIALOGUE_FILENAME
            )
        )
    return paths


def parse_glossary_file(text: str) -> list[tuple[str, str]]:
    """Parse ``en <sep> ko`` lines, skipping blanks and ``#`` comments."""
    entries: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        for sep in _SEPARATORS:
            if sep in line:
                en, _, ko = line.partition(sep)
                en, ko = en.strip(), ko.strip()
                if en and ko:
                    entries.append((en, ko))
                break
    return entries


def merge_glossary(
    defaults: list[tuple[str, str]], overrides: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    """Override same-key (case-insensitive) defaults in place; append new keys."""
    result = list(defaults)
    index = {en.lower(): i for i, (en, _) in enumerate(result)}
    for en, ko in overrides:
        key = en.lower()
        if key in index:
            result[index[key]] = (en, ko)
        else:
            index[key] = len(result)
            result.append((en, ko))
    return result


def _read_overrides(path: Path) -> tuple[object, list[tuple[str, str]]]:
    try:
        mtime: object = path.stat().st_mtime
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None, []
    return mtime, parse_glossary_file(text)


def _read_chain(paths: list[Path]) -> tuple[tuple, list[tuple[str, str]]]:
    """오버라이드 파일들을 순서대로 읽어 (캐시키, 병합할 항목)을 돌려준다.

    캐시키에 **모든** 파일의 mtime이 들어가야 파일이 둘인 dialogue도 재시작 없이
    다음 번역부터 반영된다. 항목을 이어 붙여 merge_glossary를 한 번만 호출하는
    것은 파일마다 순차 병합하는 것과 결과가 같다(뒤에 온 같은 키가 이긴다).
    """
    key: list[tuple[str, object]] = []
    entries: list[tuple[str, str]] = []
    for path in paths:
        mtime, overrides = _read_overrides(path)
        key.append((str(path), mtime))
        entries.extend(overrides)
    return tuple(key), entries


def load_glossary(scope: str = SCOPE_MEETING) -> list[tuple[str, str]]:
    """Return the effective glossary (defaults merged with the override file)."""
    cache = _cache[_check_scope(scope)]
    cache_key, overrides = _read_chain(_glossary_sources(scope))
    if cache["key"] == cache_key and cache["terms"] is not None:
        return cache["terms"]  # type: ignore[return-value]
    defaults = DEFAULT_GLOSSARY
    if scope == SCOPE_DIALOGUE:
        defaults = [
            (en, ko) for en, ko in DEFAULT_GLOSSARY
            if en.lower() not in _DIALOGUE_EXCLUDE_EN
        ]
    terms = merge_glossary(defaults, overrides)
    cache["key"] = cache_key
    cache["terms"] = terms
    cache["block"] = _format_block(terms)
    return terms


def _format_block(terms: list[tuple[str, str]]) -> str:
    pairs = "; ".join(f"{en} → {ko}" for en, ko in terms)
    return (
        "Animation-production terminology — when these English terms appear in "
        "the studio/pipeline sense, render them in Korean exactly as shown "
        "instead of translating literally: " + pairs + "."
    )


def glossary_block(scope: str = SCOPE_MEETING) -> str:
    """Prompt-ready instruction block listing every term mapping.

    Returns "" when disabled via ``GEMINI_GLOSSARY_ENABLED`` (0/false/no/off) so
    the glossary can be isolated as a variable without rebuilding the prompts.
    """
    # 스코프 검증은 킬스위치보다 먼저 — 오타가 env 상태에 따라 조용히 넘어가면 안 된다.
    _check_scope(scope)
    if os.environ.get(GLOSSARY_ENABLED_ENV, "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return ""
    load_glossary(scope)
    return _cache[scope]["block"]  # type: ignore[return-value]


# Korean-output corrections for providers that accept no prompt/instructions
# (gemini-3.5-live-translate is "pure translation"), so the EN→KO prompt
# glossary above cannot steer them. Applied as literal substring replacement on
# the Korean caption text. Deliberately tiny and phrase-scoped: each left side
# must be specific enough that it can only be a mistranslated studio term
# (e.g. "연필 테스트"), never a phrase a speaker could legitimately mean.
# Operators extend it via ``{STORAGE_ROOT}/glossary_ko.txt`` — same
# ``wrong => right`` line syntax and no-restart mtime pickup as glossary.txt.
DEFAULT_KO_CORRECTIONS: list[tuple[str, str]] = [
    ("연필 테스트", "펜슬 테스트"),
    ("연필테스트", "펜슬 테스트"),
    ("청소 팀", "클린업 팀"),
    ("청소팀", "클린업팀"),
    ("청소 작업", "클린업 작업"),
    ("중간 프레임", "인비트윈"),
    ("선 테스트", "라인 테스트"),
    # "deliver"가 동사까지 음차되면 어색하다("애니매틱을 딜리버리할 수 있어요",
    # 실제 보고서). 한글 음절은 완성형이라 어간 "딜리버리하"로는 "딜리버리할/했"을
    # 못 잡는다(할≠하+ㄹ) — 활용형을 열거한다. 명사 "딜리버리"(딜리버리 일정 등)는
    # 뒤에 '하' 계열 음절이 붙지 않으므로 안 다친다.
    ("딜리버리하", "전달하"),
    ("딜리버리할", "전달할"),
    ("딜리버리했", "전달했"),
    ("딜리버리한", "전달한"),
    ("딜리버리해", "전달해"),
    # 일정 "push out"의 음차. "범프"는 넣지 않는다 — 3D 논의에서 범프 맵(텍스처)이
    # 정당하게 나올 수 있어 문자열 치환 자격("오역일 수밖에 없는 표현")이 안 된다.
    # "순연"은 격식체라 어렵다는 사용자 피드백 → 현장 회화체 "밀림"으로.
    ("푸시 아웃", "밀림"),
    ("푸시아웃", "밀림"),
    # "plant the seed" 직역("일단 씨앗을 심어두고") — 관용구를 뜻으로. 자막메이커
    # (작품 대사)는 dialogue 스코프가 이 항목을 빼므로 별도 조치가 필요 없다.
    ("씨앗을 심어", "주제로 던져"),
]

# dialogue 스코프에서 DEFAULT_KO_CORRECTIONS에서 빼는 회의 전용 항목(좌변 정확
# 일치). 제외 범위 규칙은 _DIALOGUE_EXCLUDE_EN과 같다 — 내장 기본값에만 적용.
_DIALOGUE_EXCLUDE_KO: frozenset[str] = frozenset({
    # 작품에 청소부·청소 장면이 나오면 평범한 대사가 "클린업 팀/작업"으로 오염된다.
    "청소 팀",
    "청소팀",
    "청소 작업",
    # 정원 가꾸기 장면에서 문자 그대로의 "씨앗을 심어"가 깨진다.
    "씨앗을 심어",
})

_ko_cache: dict[str, dict[str, object]] = {
    scope: {"key": None, "terms": None} for scope in _SCOPES
}


def _ko_corrections_path() -> Path:
    return _storage_path(KO_CORRECTIONS_PATH_ENV, KO_CORRECTIONS_FILENAME)


def load_ko_corrections(scope: str = SCOPE_MEETING) -> list[tuple[str, str]]:
    """Effective KO corrections (defaults merged with the override file)."""
    cache = _ko_cache[_check_scope(scope)]
    cache_key, overrides = _read_chain(_ko_sources(scope))
    if cache["key"] == cache_key and cache["terms"] is not None:
        return cache["terms"]  # type: ignore[return-value]
    defaults = DEFAULT_KO_CORRECTIONS
    if scope == SCOPE_DIALOGUE:
        defaults = [
            (wrong, right) for wrong, right in DEFAULT_KO_CORRECTIONS
            if wrong not in _DIALOGUE_EXCLUDE_KO
        ]
    terms = merge_glossary(defaults, overrides)
    cache["key"] = cache_key
    cache["terms"] = terms
    return terms


def apply_ko_corrections(text: str, scope: str = SCOPE_MEETING) -> str:
    """Rewrite known-bad literal renderings in Korean caption text."""
    if not text:
        return text
    for wrong, right in load_ko_corrections(scope):
        if wrong in text:
            text = text.replace(wrong, right)
    return text


def glossary_file_path(scope: str = SCOPE_MEETING) -> Path:
    """용어집 오버라이드 파일 경로 — 편집 API가 로더와 같은 해석을 쓰게 공개.

    체인의 마지막 = 그 스코프가 직접 쓰는 파일(dialogue면 대사 전용 파일).
    """
    return _glossary_sources(scope)[-1]


def ko_corrections_file_path(scope: str = SCOPE_MEETING) -> Path:
    """사후 교정 오버라이드 파일 경로 — 편집 API 공용."""
    return _ko_sources(scope)[-1]


def invalid_glossary_lines(text: str) -> list[tuple[int, str]]:
    """parse_glossary_file이 조용히 버릴 (줄번호, 원문) 목록 — 편집기 저장 검증용.

    파서는 잘못된 줄을 무시하므로, 편집기에선 오타 한 줄이 소리 없이
    사전에서 빠지는 사고를 저장 시점에 알려야 한다."""
    bad: list[tuple[int, str]] = []
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not parse_glossary_file(line):
            bad.append((i, raw))
    return bad
# === ANCHOR: GLOSSARY_END ===
