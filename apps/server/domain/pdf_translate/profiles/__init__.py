import os

from ..backend import PdfDocument
from .base import FormatProfile
from .storyboard import StoryboardProfile
from .xsheet import XsheetProfile

# 등록 순서 = 감지 우선순위. 새 포맷은 여기 한 줄 추가.
# xsheet는 마지막 — detect가 OCR을 돌리므로(스캔 문서 한정이지만) 텍스트
# 기반 프로파일들이 먼저 싸게 판정하게 둔다.
_PROFILES: tuple[FormatProfile, ...] = (StoryboardProfile(), XsheetProfile())


def profile_names() -> tuple[str, ...]:
    """API가 format_hint 검증 패턴을 레지스트리에서 자동 도출할 때 쓴다
    (video_jobs의 엔진 목록 도출과 같은 이유 — 하드코딩 드리프트 방지)."""
    return tuple(p.name for p in _PROFILES)


# 서버 운영자가 콘솔에서 끄는 포맷 스위치 — 환경변수 이름도 레지스트리에서
# 자동 도출한다(profile_names와 같은 드리프트 방지 이유).
ENABLED_ENV: dict[str, str] = {
    p.name: f"YESON_PDF_{p.name.upper()}_ENABLED" for p in _PROFILES
}

# "꺼짐"으로 읽는 값들. 미설정·빈값·그 밖의 값은 전부 켜짐 —
# 구버전 설치본(변수 자체가 없다)이 갑자기 잠기면 안 된다.
_OFF_VALUES = frozenset({"0", "false", "no", "off"})


def profile_enabled(name: str) -> bool:
    """모르는 이름도 켜짐 — 이 모듈의 규칙은 "명시적으로 끈 것만 꺼짐"이다.
    (레지스트리와 API 패턴이 어긋나도 500이 아니라 기존 동작으로 흐른다.)"""
    env = ENABLED_ENV.get(name)
    if env is None:
        return True
    return os.environ.get(env, "").strip().lower() not in _OFF_VALUES


def enabled_formats() -> dict[str, bool]:
    """콘솔·클라이언트가 조회하는 포맷별 활성 상태(서버가 권위)."""
    return {name: profile_enabled(name) for name in ENABLED_ENV}


def disabled_message(profile: FormatProfile) -> str:
    """비활성 안내 문구의 단일 출처 — API도 파이프라인도 같은 말을 한다."""
    return f"서버 운영자가 {profile.label} 번역을 비활성화했습니다"


def detect_profile(doc: PdfDocument) -> FormatProfile | None:
    for profile in _PROFILES:
        if profile.detect(doc):
            return profile
    return None


def profile_by_name(name: str) -> FormatProfile | None:
    """이름으로 프로파일을 되찾는다 — 편집 API가 `job.format`(번역 때 이미
    DB에 기록된다)으로 프로파일을 얻어 **재감지 비용을 없앤다**. `detect_profile`은
    최대 3페이지를 훑으므로 편집 라우트마다 부르면 낭비다.

    없으면 `None` — 호출부는 409로 끝낸다(`job.format`이 비어 있는 잡, 또는
    이 버전이 모르는 포맷). 조용히 다른 프로파일로 대체하지 않는다.
    """
    for profile in _PROFILES:
        if profile.name == name:
            return profile
    return None
