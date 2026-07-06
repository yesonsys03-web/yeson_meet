"""Source acquisition: YouTube download (yt-dlp) or client upload."""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("yeson.video.ingest")

# 웹뷰 <video>가 바로 재생 가능한 H.264/mp4를 우선한다 (미리보기 트랜스코드 회피).
_YTDLP_FORMAT = (
    "bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]"
    "/best[ext=mp4]/best"
)


class IngestError(RuntimeError):
    pass


def _ytdl(opts: dict):  # test seam
    from yt_dlp import YoutubeDL

    return YoutubeDL(opts)


def download_youtube(url: str, dest_dir: Path,
                     ffmpeg_location: str | None = None) -> tuple[Path, str]:
    """Blocking download — call via asyncio.to_thread. Returns (path, title).

    ``ffmpeg_location``: video+audio 스트림 병합에 ffmpeg가 필요한데 yt-dlp는
    PATH만 보므로, 번들 설치(PATH에 ffmpeg 없음)에선 서버가 아는 번들 경로를
    넘겨줘야 한다. 없으면 yt-dlp가 PATH를 탐색한다.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": _YTDLP_FORMAT,
        "outtmpl": str(dest_dir / "source.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
    }
    if ffmpeg_location:
        opts["ffmpeg_location"] = ffmpeg_location
    try:
        with _ytdl(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as exc:  # noqa: BLE001 — yt-dlp raises many types
        raise IngestError(
            f"유튜브 다운로드 실패: {exc}. yt-dlp 업데이트가 필요할 수 있습니다."
        ) from exc
    ext = info.get("ext", "mp4")
    path = dest_dir / f"source.{ext}"
    if not path.exists():
        candidates = sorted(dest_dir.glob("source.*"))
        if not candidates:
            raise IngestError("다운로드된 파일을 찾지 못했습니다.")
        path = candidates[0]
    return path, str(info.get("title") or "YouTube video")


async def save_upload(upload, dest: Path) -> None:
    """Stream an UploadFile to disk in 1 MB chunks (no whole-file buffering)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as fh:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            fh.write(chunk)
