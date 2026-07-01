"""Caption markup stripping — the model sometimes emits literal <br>/HTML for
the "two short lines" hint, which used to render as visible tags in the overlay
and reports. See apps/server/ws/sidecar.py::_strip_caption_markup."""
from apps.server.ws.sidecar import _strip_caption_markup


def test_strips_br_variants_to_space():
    assert _strip_caption_markup("삭제하려면<br>delete 키를") == "삭제하려면 delete 키를"
    assert _strip_caption_markup("A<br/>B") == "A B"
    assert _strip_caption_markup("A<br />B") == "A B"
    assert _strip_caption_markup("A< BR >B") == "A B"


def test_strips_whitelisted_formatting_tags():
    assert _strip_caption_markup("<b>강조</b> 텍스트") == "강조 텍스트"
    assert _strip_caption_markup("<p>문장</p>") == "문장"


def test_preserves_plain_text_and_code_like_lt_gt():
    # No HTML tag → untouched. Guards coding-demo transcripts ("x < 3").
    assert _strip_caption_markup("펜슬 툴로 그립니다") == "펜슬 툴로 그립니다"
    assert _strip_caption_markup("if x < 3 and y > 2") == "if x < 3 and y > 2"


def test_collapses_resulting_double_spaces():
    assert _strip_caption_markup("페인트통은 선택한 색으로<br> 도형을") == "페인트통은 선택한 색으로 도형을"


def test_empty_and_none_safe():
    assert _strip_caption_markup("") == ""
