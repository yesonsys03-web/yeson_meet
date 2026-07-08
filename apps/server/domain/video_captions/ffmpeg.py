"""Thin ffmpeg CLI wrapper for the video caption pipeline."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Callable

logger = logging.getLogger("yeson.video.ffmpeg")

FFMPEG_BIN_ENV = "YESON_FFMPEG_BIN"
BURN_ENCODER_ENV = "YESON_BURN_ENCODER"

# 굽기 인코더별 품질 인자. libx264 veryfast는 medium 대비 -35% 시간에
# VMAF -1.3점(실측 2026-07-08, docs/video-caption-gpu-plan-2026-07-08.md)이라
# 자막 굽기 용도로 충분. GPU 인코더는 crf 23 상당의 품질 파라미터로 매핑.
_ENCODER_ARGS: dict[str, list[str]] = {
    "libx264": ["-preset", "veryfast", "-crf", "23"],
    "h264_nvenc": ["-preset", "p5", "-rc", "vbr", "-cq", "23", "-b:v", "0"],
    "h264_amf": ["-quality", "balanced", "-rc", "cqp", "-qp_i", "21", "-qp_p", "23"],
    "h264_qsv": ["-preset", "veryfast", "-global_quality", "23"],
    "h264_videotoolbox": ["-q:v", "55"],
}

# 플랫폼별 GPU 인코더 후보(우선순위순). Linux 번들 ffmpeg(정적 빌드)는 HW 인코더가
# 없어 후보 없음. Windows 번들(BtbN GPL)은 NVENC/AMF/QSV 포함.
_HW_CANDIDATES: tuple[str, ...] = (
    ("h264_nvenc", "h264_amf", "h264_qsv") if os.name == "nt"
    else ("h264_videotoolbox",) if sys.platform == "darwin"
    else ()
)

_encoder_cache: dict[str, str] = {}

# Windows에서 ffmpeg 콘솔 창이 번쩍이지 않도록 — CREATE_NO_WINDOW는 Windows 전용
# 상수라 os.name == "nt"가 아닌 분기에서는 절대 참조하지 않는다 (mac/Linux AttributeError 방지).
_SUBPROCESS_FLAGS: dict = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
)


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
        **_SUBPROCESS_FLAGS,
    )
    if result.returncode != 0:
        tail = (result.stderr or "")[-500:]
        raise FfmpegError(f"ffmpeg failed (code={result.returncode}): {tail}")


def extract_audio(ffmpeg: str, src: Path, dst: Path) -> None:
    """16 kHz mono s16 wav — whisper 입력 포맷."""
    _run([ffmpeg, "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
          "-f", "wav", str(dst)])


def _probe_encoder(ffmpeg: str, encoder: str) -> bool:
    """-encoders 목록에 있어도 실제로는 안 열리는 경우가 있어(드라이버/HW 미노출 등)
    1초짜리 실제 인코딩으로 검증한다."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
           "-i", "testsrc2=size=640x360:rate=30:duration=1",
           "-c:v", encoder, *_ENCODER_ARGS[encoder], "-f", "null", "-"]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30,
                                **_SUBPROCESS_FLAGS)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def detect_burn_encoder(ffmpeg: str) -> str:
    """굽기용 비디오 인코더 선택 — GPU 후보를 프로브해 첫 성공을 쓰고, 없으면
    libx264. 결과는 프로세스 수명 동안 캐시. YESON_BURN_ENCODER로 강제 지정 가능."""
    override = os.environ.get(BURN_ENCODER_ENV)
    if override:
        return override if override in _ENCODER_ARGS else "libx264"
    if ffmpeg not in _encoder_cache:
        chosen = "libx264"
        for cand in _HW_CANDIDATES:
            if _probe_encoder(ffmpeg, cand):
                chosen = cand
                break
        logger.info("burn encoder: %s", chosen)
        _encoder_cache[ffmpeg] = chosen
    return _encoder_cache[ffmpeg]


def burn_subtitles(ffmpeg: str, src: Path, srt_path: Path, dst: Path,
                   force_style: str,
                   progress_cb: Callable[[float], None] | None = None) -> None:
    """자막 굽기. GPU 인코더가 감지되면 사용하고, 도중 실패하면 libx264로 1회 재시도."""
    encoder = detect_burn_encoder(ffmpeg)
    try:
        _burn_once(ffmpeg, src, srt_path, dst, force_style, encoder, progress_cb)
    except FfmpegError:
        if encoder == "libx264":
            raise
        logger.warning("burn: %s 인코딩 실패 — libx264로 재시도", encoder)
        _encoder_cache[ffmpeg] = "libx264"  # 이후 작업도 CPU로
        _burn_once(ffmpeg, src, srt_path, dst, force_style, "libx264", progress_cb)


def _burn_once(ffmpeg: str, src: Path, srt_path: Path, dst: Path,
               force_style: str, encoder: str,
               progress_cb: Callable[[float], None] | None = None) -> None:
    """subtitles 필터는 경로 이스케이프가 취약 → cwd를 srt 디렉터리로 두고 상대 파일명 사용.

    progress_cb가 주어지면 ``-progress pipe:1 -nostats``로 stdout을 스트리밍해
    ``out_time_ms=``(마이크로초) 라인을 초 단위로 변환해 전달한다. stderr는
    데드락 방지를 위해 PIPE로 받지 않고 임시 파일로 받는다.
    """
    vf = f"subtitles={srt_path.name}:force_style='{force_style}'"
    cmd = [ffmpeg, "-y", "-i", str(src), "-vf", vf,
           "-c:v", encoder, *_ENCODER_ARGS[encoder], "-c:a", "copy"]
    if progress_cb is not None:
        cmd += ["-progress", "pipe:1", "-nostats"]
    cmd.append(str(dst))
    cwd = str(srt_path.parent)

    if progress_cb is None:
        _run(cmd, cwd=cwd)
        return

    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stderr_f:
        proc = subprocess.Popen(
            cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=stderr_f,
            text=True, encoding="utf-8", errors="replace",
            **_SUBPROCESS_FLAGS,
        )
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("out_time_ms="):
                    try:
                        progress_cb(int(line.split("=", 1)[1]) / 1_000_000.0)
                    except ValueError:
                        pass
        finally:
            proc.stdout.close()
        returncode = proc.wait()
        if returncode != 0:
            stderr_f.seek(0)
            tail = stderr_f.read()[-500:]
            raise FfmpegError(f"ffmpeg failed (code={returncode}): {tail}")


def wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / wf.getframerate()


def ensure_preview(ffmpeg: str, src: Path, dst: Path) -> Path:
    """웹뷰 <video> 재생용 사본. mp4는 그대로, 그 외 컨테이너는 H.264 트랜스코드."""
    if src.suffix.lower() == ".mp4":
        return src
    _run([ffmpeg, "-y", "-i", str(src), "-c:v", "libx264", "-preset", "veryfast",
          "-crf", "23", "-c:a", "aac", "-movflags", "+faststart", str(dst)])
    return dst
