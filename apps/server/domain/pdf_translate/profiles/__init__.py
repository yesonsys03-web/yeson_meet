from ..backend import PdfDocument
from .base import FormatProfile
from .storyboard import StoryboardProfile

# 등록 순서 = 감지 우선순위. 새 포맷은 여기 한 줄 추가.
_PROFILES: tuple[FormatProfile, ...] = (StoryboardProfile(),)


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
