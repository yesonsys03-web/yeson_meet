# === ANCHOR: TRANSCRIBE_APPLE_START ===
"""Apple SpeechTranscriber 파일 전사 (whisper_model="apple" 센티널 엔진).

apple-live-translate transcribe-file이 단어 단위 token JSONL을 방출하면,
faster-whisper의 Word와 같은 (.start/.end/.word) 모양으로 감싸 기존
words_to_cues(6초/90자 큐 분할)에 그대로 물린다. Blocking — 호출자는
transcribe.transcribe_audio를 통해 asyncio.to_thread로 부른다.
"""
from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from apps.server.ai.apple_native import resolve_apple_bin
from .srt import SubSegment
from .transcribe import StaleRunCancelled, words_to_cues

logger = logging.getLogger("yeson.video.transcribe_apple")


@dataclass(frozen=True)
class _Token:
    start: float
    end: float
    word: str


def transcribe_audio_apple(
    audio_path: Path,
    progress_cb: Callable[[float], None] | None = None,
    argv: list[str] | None = None,
) -> list[SubSegment]:
    if argv is None:
        bin_path = resolve_apple_bin()
        if bin_path is None:
            raise RuntimeError(
                "apple-live-translate 바이너리를 찾을 수 없습니다 "
                "(YESON_APPLE_TRANSLATE_BIN 또는 PATH 확인)")
        argv = [bin_path, "transcribe-file", "--input", str(audio_path)]

    with tempfile.TemporaryFile() as stderr_file:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=stderr_file,
                                text=True, encoding="utf-8", errors="replace")
        tokens: list[_Token] = []
        error_reason: str | None = None
        rc: int | None = None
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("transcribe_apple: non-JSON line skipped: %r", line[:120])
                    continue
                etype = event.get("type")
                if etype == "token":
                    # 공백 word는 words_to_cues가 스킵하므로 그대로 전달
                    try:
                        tokens.append(_Token(start=float(event["t0"]), end=float(event["t1"]),
                                             word=str(event.get("text", ""))))
                    except (KeyError, ValueError, TypeError):
                        logger.warning("transcribe_apple: malformed token skipped: %r", event)
                elif etype == "progress" and progress_cb is not None:
                    progress_cb(float(event.get("frac", 0.0)))  # StaleRunCancelled 전파 가능
                elif etype == "status" and event.get("state") == "error":
                    error_reason = str(event.get("reason", "unknown"))
                    break  # 바이너리가 stdout을 계속 열어둬도 즉시 탈출
                elif etype == "done":
                    break
        except StaleRunCancelled:
            proc.kill()
            raise
        finally:
            proc.stdout and proc.stdout.close()
            # 에러로 즉시 break한 경우 바이너리가 stdout을 열어둔 채 남아있을 수
            # 있으므로 wait 전에 죽여서 대기시간 방지 (정상 완료 경로는 자연 종료를 기다림)
            if error_reason is not None and proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass
            try:
                rc = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except OSError:
                    pass
                try:
                    rc = proc.wait()
                except Exception:
                    rc = None
        if error_reason is not None or rc != 0:
            stderr_file.seek(0)
            stderr_tail = stderr_file.read()[-300:].decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Apple 전사 실패: {error_reason or f'returncode={rc}'} {stderr_tail}")
        cues = words_to_cues(tokens)
        logger.info("transcribe_apple: %d cues from %d tokens", len(cues), len(tokens))
        return cues
# === ANCHOR: TRANSCRIBE_APPLE_END ===
