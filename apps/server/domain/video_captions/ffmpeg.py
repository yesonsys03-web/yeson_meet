"""Thin ffmpeg CLI wrapper for the video caption pipeline."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

FFMPEG_BIN_ENV = "YESON_FFMPEG_BIN"


class FfmpegError(RuntimeError):
    pass


def locate_ffmpeg() -> str | None:
    override = os.environ.get(FFMPEG_BIN_ENV)
    if override:
        return override if Path(override).exists() else None
    return shutil.which("ffmpeg")


def _run(cmd: list[str], *, cwd: str | None = None) -> None:
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", cwd=cwd,
    )
    if result.returncode != 0:
        tail = (result.stderr or "")[-500:]
        raise FfmpegError(f"ffmpeg failed (code={result.returncode}): {tail}")


def extract_audio(ffmpeg: str, src: Path, dst: Path) -> None:
    """16 kHz mono s16 wav — whisper 입력 포맷."""
    _run([ffmpeg, "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
          "-f", "wav", str(dst)])


def burn_subtitles(ffmpeg: str, src: Path, srt_path: Path, dst: Path,
                   force_style: str) -> None:
    """subtitles 필터는 경로 이스케이프가 취약 → cwd를 srt 디렉터리로 두고 상대 파일명 사용."""
    vf = f"subtitles={srt_path.name}:force_style='{force_style}'"
    _run([ffmpeg, "-y", "-i", str(src), "-vf", vf, "-c:a", "copy", str(dst)],
         cwd=str(srt_path.parent))


def ensure_preview(ffmpeg: str, src: Path, dst: Path) -> Path:
    """웹뷰 <video> 재생용 사본. mp4는 그대로, 그 외 컨테이너는 H.264 트랜스코드."""
    if src.suffix.lower() == ".mp4":
        return src
    _run([ffmpeg, "-y", "-i", str(src), "-c:v", "libx264", "-preset", "veryfast",
          "-crf", "23", "-c:a", "aac", "-movflags", "+faststart", str(dst)])
    return dst
