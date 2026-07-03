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


def build_force_style(position: str, margin_v: int, font_size: int) -> str:
    """ffmpeg subtitles filter force_style value. 클라 미리보기와 동일 좌표계."""
    alignment = 8 if position == "top" else 2
    return f"Alignment={alignment},MarginV={margin_v},Fontsize={font_size}"
