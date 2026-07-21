"""Thin ffmpeg CLI wrapper for the video caption pipeline."""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
import sys
import tempfile
import wave
from pathlib import Path
from typing import Callable

logger = logging.getLogger("yeson.video.ffmpeg")

FFMPEG_BIN_ENV = "YESON_FFMPEG_BIN"
BURN_ENCODER_ENV = "YESON_BURN_ENCODER"
BURN_PRESET_ENV = "YESON_BURN_PRESET"

# libx264 굽기 프리셋 opt-in. 60s·1080p60 실측(2026-07-11): veryfast 10.8s/8.6MB,
# superfast 6.8s/18.6MB, ultrafast 4.8s/35.3MB — 빠를수록 크고 품질↓라 기본은
# veryfast, 급할 때만 운영자가 올린다. 목록 밖 값은 veryfast로 안전 폴백.
_BURN_PRESETS: tuple[str, ...] = ("veryfast", "superfast", "ultrafast")

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


def _burn_preset() -> str:
    """libx264 굽기 프리셋 — YESON_BURN_PRESET opt-in. 목록 밖/미설정은 veryfast."""
    preset = os.environ.get(BURN_PRESET_ENV, "veryfast")
    return preset if preset in _BURN_PRESETS else "veryfast"


def _encoder_args(encoder: str) -> list[str]:
    """인코더별 ffmpeg 인자. libx264는 YESON_BURN_PRESET로 프리셋을 오버라이드할 수
    있게 매 호출 계산한다(GPU 인코더는 정적)."""
    if encoder == "libx264":
        return ["-preset", _burn_preset(), "-crf", "23"]
    return _ENCODER_ARGS[encoder]

# 플랫폼별 GPU 인코더 후보(우선순위순). Linux 번들 ffmpeg(정적 빌드)는 HW 인코더가
# 없어 후보 없음. Windows 번들(BtbN GPL)은 NVENC/AMF/QSV 포함.
_HW_CANDIDATES: tuple[str, ...] = (
    ("h264_nvenc", "h264_amf", "h264_qsv") if os.name == "nt"
    else ("h264_videotoolbox",) if sys.platform == "darwin"
    else ()
)

_encoder_cache: dict[tuple[str, bool], str] = {}

# Windows에서 ffmpeg 콘솔 창이 번쩍이지 않도록 — CREATE_NO_WINDOW는 Windows 전용
# 상수라 os.name == "nt"가 아닌 분기에서는 절대 참조하지 않는다 (mac/Linux AttributeError 방지).
_SUBPROCESS_FLAGS: dict = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
)


class FfmpegError(RuntimeError):
    pass


# 활성 ffmpeg 프로세스 레지스트리 — 취소 시 다음 진행률 라인을 기다리지 않고
# 즉시 kill한다. dict/set 단일 연산은 GIL 원자성으로 충분히 스레드 안전하다.
# kill_active로 죽은 proc_key는 _KILLED에 표식이 남아, GPU 인코딩이 kill로
# 죽어 FfmpegError로 보여도 burn_subtitles가 libx264 재시도를 타지 않게 한다 —
# 새 프로세스가 등록되면(register_proc) 그 키의 표식은 지워져 다음 실행에는
# 영향을 주지 않는다.
# 키(잡)당 활성 프로세스 "집합" — 정밀화를 병렬로 돌리면 한 잡에 ffmpeg가 여러 개
# 동시에 뜬다. 하나만 추적하면 나머지가 취소를 빠져나가 계속 돈다.
_ACTIVE: dict[str, set[subprocess.Popen]] = {}
_KILLED: set[str] = set()
_ACTIVE_LOCK = threading.Lock()


def register_proc(key: str, proc: subprocess.Popen) -> None:
    with _ACTIVE_LOCK:
        _KILLED.discard(key)
        _ACTIVE.setdefault(key, set()).add(proc)


def unregister_proc(key: str, proc: subprocess.Popen | None = None) -> None:
    """정상 종료한 프로세스를 추적에서 뺀다. proc를 주면 그것만(같은 키의 다른
    동시 실행은 유지), 없으면 그 키 전체를 지운다(하위 호환)."""
    with _ACTIVE_LOCK:
        if proc is None:
            _ACTIVE.pop(key, None)
            return
        procs = _ACTIVE.get(key)
        if procs is None:
            return
        procs.discard(proc)
        if not procs:
            _ACTIVE.pop(key, None)


def kill_active(key: str) -> bool:
    """등록된 활성 프로세스가 있으면 즉시 kill한다.

    Windows에서도 동일하게 동작하도록 proc.kill()/proc.wait()만 사용한다
    (kill()은 Windows에서 TerminateProcess로 매핑된다) — os.killpg, 시그널,
    프로세스 그룹 등 POSIX 전용 API는 쓰지 않는다. 예외는 삼킨다(이미 죽은
    프로세스 등). 등록된 프로세스가 있었으면 True.
    """
    with _ACTIVE_LOCK:
        procs = _ACTIVE.pop(key, None)
        if not procs:
            return False
        _KILLED.add(key)
        targets = list(procs)
    for proc in targets:  # 동시 실행 중이던 것을 하나도 남기지 않는다
        try:
            proc.kill()
            proc.wait()
        except Exception:  # noqa: BLE001 — 이미 종료된 프로세스 등은 무시
            pass
    return True


def _was_killed(key: str | None) -> bool:
    return key is not None and key in _KILLED


def locate_ffmpeg() -> str | None:
    override = os.environ.get(FFMPEG_BIN_ENV)
    if override:
        return override if Path(override).exists() else None
    return shutil.which("ffmpeg")


_FPS_RE = re.compile(r"(\d+(?:\.\d+)?)\s+fps")
_PTS_RE = re.compile(r"pts_time:(\d+(?:\.\d+)?)")


def _fps_from_display(ffmpeg: str, src: Path) -> float | None:
    """`ffmpeg -i` stderr의 표시 fps('… 23.98 fps …'). 반올림값이라 폴백 전용."""
    try:
        result = subprocess.run(
            [ffmpeg, "-i", str(src)], capture_output=True, text=True,
            encoding="utf-8", errors="replace", **_SUBPROCESS_FLAGS,
        )
    except OSError:
        return None
    m = _FPS_RE.search(result.stderr or "")
    if not m:
        return None
    try:
        fps = float(m.group(1))
    except ValueError:
        return None
    return fps if fps > 0 else None


def video_fps(ffmpeg: str, src: Path) -> float | None:
    """소스의 정확한 프레임레이트(fps). 실패 시 None.

    표시 fps('23.98 fps')는 소수 2자리 반올림이라 실제 24000/1001(23.976)과
    어긋난다 — 컷 프레임 수 N=round((end-start)×fps/1000)를 긴 클립(수천 프레임)에
    쓰면 이 오차가 0.5프레임 넘게 누적돼 N이 ±1 틀리고, 다음 세그 첫 프레임이 꼬리에
    섞인다(실기: 23.98이면 1862→1863). 그래서 표시값 대신 앞 3초를 디코드해 실제
    프레임 PTS 간격의 중앙값으로 fps를 측정한다(ffprobe 미번들). showinfo가 프레임을
    못 내면 표시값으로 폴백한다."""
    try:
        result = subprocess.run(
            [ffmpeg, "-i", str(src), "-map", "0:v:0", "-vf", "showinfo",
             "-t", "3", "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            **_SUBPROCESS_FLAGS,
        )
    except OSError:
        return None
    pts = [float(x) for x in _PTS_RE.findall(result.stderr or "")]
    deltas = sorted(b - a for a, b in zip(pts, pts[1:]) if b > a)
    if len(deltas) >= 2:
        median = deltas[len(deltas) // 2]
        if median > 0:
            return 1.0 / median
    return _fps_from_display(ffmpeg, src)


def _run(cmd: list[str], *, cwd: str | None = None,
        proc_key: str | None = None) -> None:
    if proc_key is None:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", cwd=cwd,
            **_SUBPROCESS_FLAGS,
        )
        if result.returncode != 0:
            tail = (result.stderr or "")[-500:]
            raise FfmpegError(f"ffmpeg failed (code={result.returncode}): {tail}")
        return

    # proc_key가 있으면 취소 시 즉시 kill 가능하도록 Popen으로 실행하고 레지스트리에
    # 등록한다 — 동작(인자·에러 처리)은 위 subprocess.run 경로와 동일하게 유지.
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        **_SUBPROCESS_FLAGS,
    )
    register_proc(proc_key, proc)
    try:
        _, stderr = proc.communicate()
    finally:
        unregister_proc(proc_key, proc)
    if proc.returncode != 0:
        tail = (stderr or "")[-500:]
        raise FfmpegError(f"ffmpeg failed (code={proc.returncode}): {tail}")


def extract_audio(ffmpeg: str, src: Path, dst: Path,
                  proc_key: str | None = None) -> None:
    """16 kHz mono s16 wav — whisper 입력 포맷."""
    _run([ffmpeg, "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
          "-f", "wav", str(dst)], proc_key=proc_key)


def _probe_encoder(ffmpeg: str, encoder: str) -> bool:
    """-encoders 목록에 있어도 실제로는 안 열리는 경우가 있어(드라이버/HW 미노출 등)
    1초짜리 실제 인코딩으로 검증한다."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
           "-i", "testsrc2=size=640x360:rate=30:duration=1",
           "-c:v", encoder, *_encoder_args(encoder), "-f", "null", "-"]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30,
                                **_SUBPROCESS_FLAGS)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def detect_burn_encoder(ffmpeg: str, use_gpu: bool) -> str:
    """굽기용 비디오 인코더 선택 — GPU 후보를 프로브해 첫 성공을 쓰고, 없으면
    libx264. 결과는 (ffmpeg, use_gpu) 조합으로 프로세스 수명 동안 캐시.
    YESON_BURN_ENCODER로 강제 지정 가능(운영자 명시 오버라이드는 use_gpu보다 우선).

    use_gpu=False면 GPU 토글이 꺼져 있다는 뜻 — 프로브를 아예 하지 않고
    즉시 libx264를 반환한다(순수 함수 유지 — 전역 상태는 호출자가 결정해
    넘긴다, gpu_pack.is_enabled() 등).
    """
    override = os.environ.get(BURN_ENCODER_ENV)
    if override:
        return override if override in _ENCODER_ARGS else "libx264"
    if not use_gpu:
        return "libx264"
    key = (ffmpeg, use_gpu)
    if key not in _encoder_cache:
        chosen = "libx264"
        for cand in _HW_CANDIDATES:
            if _probe_encoder(ffmpeg, cand):
                chosen = cand
                break
        logger.info("burn encoder: %s (gpu=%s)", chosen, use_gpu)
        _encoder_cache[key] = chosen
    return _encoder_cache[key]


def burn_subtitles(ffmpeg: str, src: Path, srt_path: Path, dst: Path,
                   force_style: str,
                   progress_cb: Callable[[float], None] | None = None,
                   proc_key: str | None = None,
                   use_gpu: bool = True) -> None:
    """자막 굽기. GPU 인코더가 감지되면 사용하고, 도중 실패하면 libx264로 1회 재시도.

    단, kill_active로 취소돼 죽은 실행은 재시도하지 않고 그대로 전파한다 —
    취소 후에도 CPU 인코딩이 새로 시작되는 낭비를 막기 위함.

    use_gpu는 GPU 팩 토글을 그대로 반영한다(기본 True는 하위호환 — 실제 배선은
    pipeline.py가 gpu_pack.is_enabled()를 명시적으로 넘긴다). False면 GPU
    프로브 자체를 건너뛰고 libx264로 굽는다.
    """
    encoder = detect_burn_encoder(ffmpeg, use_gpu)
    try:
        _burn_once(ffmpeg, src, srt_path, dst, force_style, encoder, progress_cb,
                   proc_key)
    except FfmpegError:
        if encoder == "libx264" or _was_killed(proc_key):
            raise
        logger.warning("burn: %s 인코딩 실패 — libx264로 재시도", encoder)
        _encoder_cache[(ffmpeg, use_gpu)] = "libx264"  # 이후 작업도 CPU로
        _burn_once(ffmpeg, src, srt_path, dst, force_style, "libx264", progress_cb,
                   proc_key)


def _burn_once(ffmpeg: str, src: Path, srt_path: Path, dst: Path,
               force_style: str, encoder: str,
               progress_cb: Callable[[float], None] | None = None,
               proc_key: str | None = None) -> None:
    """subtitles 필터는 경로 이스케이프가 취약 → cwd를 srt 디렉터리로 두고 상대 파일명 사용.

    progress_cb가 주어지면 ``-progress pipe:1 -nostats``로 stdout을 스트리밍해
    ``out_time_ms=``(마이크로초) 라인을 초 단위로 변환해 전달한다. stderr는
    데드락 방지를 위해 PIPE로 받지 않고 임시 파일로 받는다.

    proc_key가 주어지면 Popen 직후 레지스트리에 등록해, 취소 시 다음 진행률
    라인을 기다리지 않고 kill_active로 즉시 kill할 수 있게 한다.
    """
    vf = f"subtitles={srt_path.name}:force_style='{force_style}'"
    cmd = [ffmpeg, "-y", "-i", str(src), "-vf", vf,
           "-c:v", encoder, *_encoder_args(encoder), "-c:a", "copy"]
    if progress_cb is not None:
        cmd += ["-progress", "pipe:1", "-nostats"]
    cmd.append(str(dst))
    cwd = str(srt_path.parent)

    if progress_cb is None:
        _run(cmd, cwd=cwd, proc_key=proc_key)
        return

    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stderr_f:
        proc = subprocess.Popen(
            cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=stderr_f,
            text=True, encoding="utf-8", errors="replace",
            **_SUBPROCESS_FLAGS,
        )
        assert proc.stdout is not None
        try:
            if proc_key is not None:
                register_proc(proc_key, proc)
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("out_time_ms="):
                    try:
                        progress_cb(int(line.split("=", 1)[1]) / 1_000_000.0)
                    except ValueError:
                        pass
        except BaseException:
            # progress_cb가 예외(취소 신호: StaleRunCancelled 등)를 던지면 ffmpeg을
            # 즉시 종료한다 — 그대로 두면 인코딩이 끝까지 돌며 CPU/GPU를 태운다.
            proc.kill()
            proc.wait()
            raise
        finally:
            proc.stdout.close()
            if proc_key is not None:
                unregister_proc(proc_key)
        returncode = proc.wait()
        if returncode != 0:
            stderr_f.seek(0)
            tail = stderr_f.read()[-500:]
            raise FfmpegError(f"ffmpeg failed (code={returncode}): {tail}")


def wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / wf.getframerate()


def ensure_preview(ffmpeg: str, src: Path, dst: Path,
                   proc_key: str | None = None) -> Path:
    """웹뷰 <video> 재생용 사본. mp4는 그대로, 그 외 컨테이너는 H.264 트랜스코드.

    proc_key가 주어지면 extract_audio와 동일하게 레지스트리에 등록돼 취소 시
    즉시 kill 가능하다 — 비-mp4 소스(.mov 등)는 이 트랜스코드 단계가 오래 걸릴
    수 있어 다음 진행률 라인을 기다리지 않아도 된다."""
    if src.suffix.lower() == ".mp4":
        return src
    _run([ffmpeg, "-y", "-i", str(src), "-c:v", "libx264", "-preset", "veryfast",
          "-crf", "23", "-c:a", "aac", "-movflags", "+faststart", str(dst)],
         proc_key=proc_key)
    return dst


# OCR 영역 = (x, y, w, h) 프레임 대비 비율. 비율이라 해상도가 달라도 그대로 쓰고,
# 쇼마다 다른 슬레이트 위치를 코드 가정 없이 처리한다(사용자가 드래그로 지정).
OcrRegion = tuple[float, float, float, float]


def crop_filter(region: OcrRegion) -> str:
    """비율 영역 → ffmpeg crop 필터. in_w/in_h 식이라 해상도에 무관하다."""
    x, y, w, h = region
    return (f"crop=in_w*{w:.4f}:in_h*{h:.4f}:in_w*{x:.4f}:in_h*{y:.4f}")


def extract_frames(ffmpeg: str, src: Path, out_dir: Path,
                   interval_s: float = 1.0, proc_key: str | None = None,
                   region: OcrRegion | None = None) -> None:
    """OCR용 프레임을 interval_s 간격으로 out_dir/frame_%05d.png에 추출.

    frame_00001.png ≈ t=0, frame_00002.png ≈ t=interval_s … (fps 필터 기준).
    호출자는 인덱스(1-based)로 t_ms = (index-1)*interval_ms를 부여한다.
    region을 주면 그 영역만 잘라 판독 입력을 줄인다(속도↑, 무관한 텍스트 배제).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    vf = f"fps=1/{interval_s}"
    if region:
        vf += "," + crop_filter(region)
    _run([ffmpeg, "-y", "-i", str(src), "-vf", vf,
          str(out_dir / "frame_%05d.png")], proc_key=proc_key)


def extract_thumbnails(ffmpeg: str, src: Path, out_dir: Path,
                       interval_s: float = 1.0, height: int = 90,
                       proc_key: str | None = None) -> None:
    """필름스트립용 저해상도 썸네일. scale=-2:height (너비는 짝수 자동)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    _run([ffmpeg, "-y", "-i", str(src), "-vf",
          f"fps=1/{interval_s},scale=-2:{height}",
          str(out_dir / "thumb_%05d.jpg")], proc_key=proc_key)


def cut_segment(ffmpeg: str, src: Path, dst: Path, start_ms: int, end_ms: int,
                proc_key: str | None = None, fps: float | None = None) -> None:
    """[start_ms, end_ms) 구간을 재인코딩(정확)해 dst로 저장. -c copy 금지 —
    슬레이트 편집본은 컷 경계가 명확해야 하므로 프레임 정확도를 우선한다.
    -ss를 -i 앞에 둬 입력 시킹으로 빠르게 접근하되, 재인코딩이라 컷은 정확하다.
    끝은 출력측 -t(길이) — 입력측 -to는 디먹서 패킷 단위로 끊어 B-프레임
    재정렬 시 다음 세그먼트 첫 프레임(들)이 꼬리에 섞인다(실측 7/16 클립).
    출력 -t는 디코드된 타임라인(입력 -ss 이후 0 기준)에서 잘라 반열림을 지킨다.

    setpts=PTS-STARTPTS + -bf 0: 입력 시킹 + B-프레임 재정렬로 mp4에 빈 편집
    리스트(elst media time -1)가 생기는데, QuickTime은 이를 존중해 클립 앞에
    검정 프레임을 렌더한다 — 원본엔 없는데도(실기 확인: 흰색으로 여는 샷 앞에
    검정). ffmpeg·VLC는 무시한다. setpts로 첫 프레임 PTS를 0으로 리셋하고
    B-프레임을 없애면(-bf 0) 빈 편집 자체가 사라진다(edit list media time 0).

    fps(경계 프레임 중복 방지): 정밀화는 경계를 '-ss로 다음 라벨이 잡히는 시각'으로
    수렴시키는데, 그 값은 실제 전환 프레임 PTS보다 0~<1프레임 위에 놓인다. 입력측
    -ss는 그 시각 '이하'의 가장 가까운 프레임으로 스냅다운("그 시각에 보이는 프레임")
    하므로 -ss start는 이 세그의 첫 프레임을 정확히 잡는다. 문제는 끝을 -t(길이)로
    끊을 때다: 시작·끝 경계의 스냅 편차(δ)가 서로 달라, 다음 세그 첫 프레임이 이
    클립 꼬리에 1개 섞이는 일이 경계의 프레임 그리드 정렬에 따라 씬마다 제각각으로
    생긴다(실측). fps를 주면 끝을 -t 대신 정확한 프레임 수 -frames:v로 끊는다 —
    세그 프레임 수는 정수라 (end-start)를 fps로 환산해 반올림하면 δ 편차(<1프레임)가
    흡수돼 정확히 복원되고, -ss 스냅다운 첫 프레임 + 정확한 개수라 빈틈·중복이 없다."""
    ss = f"{start_ms / 1000:.3f}"
    if fps and fps > 0:
        n = max(1, round((end_ms - start_ms) * fps / 1000.0))
        tail = ["-frames:v", str(n)]
    else:
        # fps 미상: 출력측 -t(길이)로 폴백. 입력측 -to는 디먹서 패킷 단위로 끊어
        # B-프레임 재정렬 시 다음 세그 첫 프레임이 꼬리에 섞이므로 금지.
        tail = ["-t", f"{(end_ms - start_ms) / 1000:.3f}"]
    _run([ffmpeg, "-y", "-ss", ss, "-i", str(src), *tail,
          "-vf", "setpts=PTS-STARTPTS", "-af", "asetpts=PTS-STARTPTS",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-bf", "0",
          "-c:a", "aac", "-movflags", "+faststart", str(dst)],
         proc_key=proc_key)


def extract_thumbnail_at(ffmpeg: str, src: Path, t_ms: int, dst: Path,
                         height: int = 90, proc_key: str | None = None) -> None:
    """임의 시각 1프레임을 필름스트립 높이로 축소해 jpg로 저장(경계 썸네일용).
    -ss를 -i 앞에 둬 cut_segment/extract_frame과 같은 시간축을 쓴다 — 그래야
    이 썸네일이 실제로 잘려 나올 클립의 첫 프레임과 일치한다."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([ffmpeg, "-y", "-ss", f"{t_ms / 1000:.3f}", "-i", str(src),
          "-frames:v", "1", "-vf", f"scale=-2:{height}", str(dst)],
         proc_key=proc_key)


def extract_frame(ffmpeg: str, src: Path, t_ms: int, dst: Path,
                  proc_key: str | None = None,
                  region: OcrRegion | None = None) -> None:
    """t_ms 시각의 단일 프레임을 dst로 추출(경계 정밀화용). -ss를 -i 앞에 둬
    입력 시킹으로 빠르게 접근한다(OCR 판독용이라 프레임 미세오차는 무방).
    region은 스캔과 같은 값을 써야 한다 — 판독 조건이 다르면 경계가 흔들린다."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-y", "-ss", f"{t_ms / 1000:.3f}", "-i", str(src),
           "-frames:v", "1"]
    if region:
        cmd += ["-vf", crop_filter(region)]
    cmd.append(str(dst))
    _run(cmd, proc_key=proc_key)
