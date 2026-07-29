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
