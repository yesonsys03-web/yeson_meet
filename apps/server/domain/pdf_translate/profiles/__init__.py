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
