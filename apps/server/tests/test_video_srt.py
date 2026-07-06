from __future__ import annotations

import pytest

from apps.server.domain.video_captions.srt import (
    SubSegment, build_force_style, hex_to_ass_color, segments_to_srt,
)


def test_segments_to_srt_formats_timestamps_and_numbering():
    srt = segments_to_srt([
        SubSegment(seq=1, start_ms=0, end_ms=1500, text="안녕하세요"),
        SubSegment(seq=2, start_ms=61_230, end_ms=3_599_999, text="두 번째 줄"),
    ])
    assert srt == (
        "1\n00:00:00,000 --> 00:00:01,500\n안녕하세요\n\n"
        "2\n00:01:01,230 --> 00:59:59,999\n두 번째 줄\n"
    )


def test_srt_strips_newlines_inside_text():
    srt = segments_to_srt([SubSegment(seq=1, start_ms=0, end_ms=1000, text="a\nb")])
    assert "a b" in srt


def test_build_force_style_bottom_and_top():
    assert build_force_style("bottom", 40, 18) == (
        "Alignment=2,MarginV=40,Fontsize=18,PrimaryColour=&H00FFFFFF")
    assert build_force_style("top", 20, 24) == (
        "Alignment=8,MarginV=20,Fontsize=24,PrimaryColour=&H00FFFFFF")


def test_build_force_style_with_custom_color():
    assert build_force_style("bottom", 40, 18, "#FF0000") == (
        "Alignment=2,MarginV=40,Fontsize=18,PrimaryColour=&H000000FF")


def test_hex_to_ass_color_converts_rgb_to_bgr():
    assert hex_to_ass_color("#FFFF00") == "&H0000FFFF"
    assert hex_to_ass_color("#FFFFFF") == "&H00FFFFFF"
    assert hex_to_ass_color("#ff0000") == "&H000000FF"


def test_hex_to_ass_color_rejects_invalid_input():
    for bad in ("red", "#FFF", "#GGGGGG", "FFFFFF", "#FFFFFF0"):
        with pytest.raises(ValueError):
            hex_to_ass_color(bad)
