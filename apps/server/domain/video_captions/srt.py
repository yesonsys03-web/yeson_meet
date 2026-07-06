"""SRT generation and ASS style helpers for burned-in video captions."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SubSegment:
    seq: int
    start_ms: int
    end_ms: int
    text: str


def _hms_ms(ms: int) -> str:
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def segments_to_srt(segments: list[SubSegment]) -> str:
    blocks: list[str] = []
    for seg in segments:
        text = " ".join(seg.text.split())
        blocks.append(
            f"{seg.seq}\n{_hms_ms(seg.start_ms)} --> {_hms_ms(seg.end_ms)}\n{text}\n"
        )
    return "\n".join(blocks)


def hex_to_ass_color(hex_color: str) -> str:
    """`#RRGGBB` -> ASS/libass `&H00BBGGRR` (alpha 00=opaque, BGR order)."""
    if (len(hex_color) != 7 or hex_color[0] != "#"
            or not all(c in "0123456789abcdefABCDEF" for c in hex_color[1:])):
        raise ValueError(f"invalid hex color: {hex_color!r}")
    r, g, b = hex_color[1:3], hex_color[3:5], hex_color[5:7]
    return f"&H00{b.upper()}{g.upper()}{r.upper()}"


def build_force_style(position: str, margin_v: int, font_size: int,
                      color: str = "#FFFFFF") -> str:
    """ffmpeg subtitles filter force_style value. 클라 미리보기와 동일 좌표계."""
    alignment = 8 if position == "top" else 2
    ass_color = hex_to_ass_color(color)
    return (f"Alignment={alignment},MarginV={margin_v},Fontsize={font_size},"
            f"PrimaryColour={ass_color}")
