"""손글씨 전사 — 비전 CLI(기본 agy)로 크롭 이미지를 영문 텍스트로.

⛔Gemini API는 쓰지 않는다(2026-08-20 사용자 확정, 비용). RapidOCR이
손글씨 판독을 못 하는 실측(SUBTLE→SUBnE) 때문에 존재하는 모듈이다 —
xsheet 프로파일이 위치를 찾은 노트 크롭을 배치로 agy 헤드리스에 보내
JSON 매핑(파일명→전사)을 받는다. 실측(2026-08-20, 표본 152크롭): agy
전사는 Gemini API와 동일 출력, 실노트 정확도 ~90%, 마커·셀번호는 빈값
스킵(원하는 동작 — 사람도 번역하지 않는다).

운영 전제: agy 헤드리스가 이미지 파일을 읽으려면 read_file 권한 허용이
선행돼야 한다(없으면 자동 거부 → 배치 실패로 표면화). 서버 기계의
`~/.gemini/antigravity-cli/settings.json`의 permissions.allow 또는
trustedWorkspaces에 잡 폴더를 등록하는 게 정석이고, 신뢰 환경 한정으로
`YESON_PDF_XSHEET_CLI_ARGS=--dangerously-skip-permissions`도 가능하다 —
단 크롭 이미지 안 텍스트가 프롬프트 주입이 될 수 있으므로(에이전트가
지시로 오독) 전역 자동승인은 기본값이 아니다.

전사 결과는 잡 폴더 `transcripts.json`에 배치마다 저장한다 — 재번역
(retranslate)이 파이프라인을 다시 돌려도 CLI를 재호출하지 않고 캐시로
이어받는다(크롭 이름이 페이지+좌표 기반이라 재추출에도 안정).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from .profiles.base import PdfBlock

if TYPE_CHECKING:
    from .backend import PdfDocument

logger = logging.getLogger("yeson.pdf_translate")

ENV_CLI = "YESON_PDF_XSHEET_CLI"            # 기본 agy — 비전 지원 CLI만 의미 있음
ENV_EXTRA_ARGS = "YESON_PDF_XSHEET_CLI_ARGS"  # shlex 분해되어 argv 뒤에 붙는다
ENV_WORKERS = "YESON_PDF_XSHEET_CLI_WORKERS"  # 동시 CLI 세션 수(기본 3)


def _workers() -> int:
    try:
        n = int(os.environ.get(ENV_WORKERS, "3"))
    except ValueError:
        n = 3
    return max(1, min(n, 8))

_CROP_DPI = 300      # 원본 스캔 해상도와 동일 — 전사 품질 실측 기준
_MARGIN_PT = 5.0
_BATCH = 20          # 스파이크 실측 배치 크기(8배치 전부 20/20 성공)
_SPLIT_MIN = 8       # 이 크기 이상의 실패 배치만 반으로 나눠 재시도
_CALL_TIMEOUT = 600  # 배치 하나당 상한(초) — agy 세션 기동 포함
_CROPS_DIRNAME = "xsheet_crops"
_CACHE_NAME = "transcripts.json"
# 전사에서 살아남는 기준: 영문 단어(2자+)가 하나라도 있어야 번역할 거리가
# 있다 — 셀 번호·서클 마커·화살표는 빈값/숫자만 나와 여기서 떨어진다.
# (refine_ko의 [A-Za-z]{2,} 기준과 같은 근거: FL104 사람 주석 실측)
_USABLE = re.compile(r"[A-Za-z]{2,}")

# Windows에서 서버가 CLI를 띄울 때 콘솔 창 번쩍임 방지(translate_cli 미러)
_NO_WINDOW = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {})


def crop_name(block: PdfBlock) -> str:
    """블록 → 크롭 파일명. 인덱스가 아니라 (페이지, 좌표) 기반이라 재추출
    후에도 같은 노트는 같은 이름 — transcripts.json 캐시가 런을 넘어 산다."""
    x0, y0 = block.bbox[0], block.bbox[1]
    return f"p{block.page + 1:03d}_{int(x0)}_{int(y0)}.png"


def render_crops(doc: PdfDocument, blocks: list[PdfBlock],
                 job_dir: Path) -> None:
    """블록 크롭 PNG를 잡 폴더에 렌더한다 — doc 락 안에서 불리는 빠른
    단계(페이지당 렌더 1회 + PIL 크롭). 느린 CLI 호출은 transcribe가
    락 없이 수행한다."""
    from PIL import Image

    from .profiles.xsheet import _decode_png  # 지연 import(순환 방지)

    crops = job_dir / _CROPS_DIRNAME
    crops.mkdir(parents=True, exist_ok=True)
    scale = _CROP_DPI / 72.0
    by_page: dict[int, list[PdfBlock]] = {}
    for b in blocks:
        by_page.setdefault(b.page, []).append(b)
    for page, page_blocks in by_page.items():
        arr = _decode_png(doc.render_png(page, dpi=_CROP_DPI))
        h, w = arr.shape[:2]
        for b in page_blocks:
            dest = crops / crop_name(b)
            if dest.exists():
                continue
            x0, y0, x1, y1 = b.bbox
            px0 = max(int((x0 - _MARGIN_PT) * scale), 0)
            py0 = max(int((y0 - _MARGIN_PT) * scale), 0)
            px1 = min(int((x1 + _MARGIN_PT) * scale), w)
            py1 = min(int((y1 + _MARGIN_PT) * scale), h)
            if px1 <= px0 or py1 <= py0:
                continue
            Image.fromarray(arr[py0:py1, px0:px1]).save(dest)


def transcribe(blocks: list[PdfBlock], job_dir: Path, *,
               should_continue: Callable[[], bool] | None = None,
               on_progress: Callable[[float], None] | None = None,
               ) -> list[PdfBlock]:
    """크롭들을 배치 전사해 블록 text를 교체하고, 번역할 거리가 없는
    블록(마커·숫자·판독 불가)은 버린다. 취소가 감지되면
    asyncio.CancelledError를 던진다(pdf_run의 on_progress와 같은 규약).
    on_progress에는 전체 크롭 대비 전사 완료 비율(0~1)이 배치마다 온다 —
    캐시로 건너뛴 몫도 분자에 포함해 재개 런의 진행률이 이어져 보인다."""
    crops = job_dir / _CROPS_DIRNAME
    cache_path = job_dir / _CACHE_NAME
    done: dict[str, str] = {}
    if cache_path.exists():
        try:
            done = {k: v for k, v in json.loads(
                cache_path.read_text(encoding="utf-8")).items()
                if isinstance(v, str)}
        except (json.JSONDecodeError, OSError):
            logger.warning("xsheet-transcribe: 캐시 파싱 실패 — 새로 시작")

    names = [crop_name(b) for b in blocks]
    all_names = {n for n in names if (crops / n).exists()}
    todo = sorted(n for n in all_names if n not in done)
    # 동시 워커 + 실패 배치 반토막 재시도.
    #
    # 동시성: A1 전량 실측(2026-08-20)에서 크롭이 4,700장(=배치 235개)
    # 나왔다 — agy 세션이 배치당 1~2분이라 직렬이면 전사만 4시간+.
    # 배치끼리는 완전 독립(서로 다른 파일)이라 CLI 세션 몇 개를 나란히
    # 띄우는 게 안전한 지름길이다. 워커 결과 병합·캐시 쓰기는 메인
    # 스레드만 한다(락 불필요).
    #
    # 반토막 재시도: 같은 실측에서 노트 53개짜리 밀집 페이지(p182)의
    # 20장 배치 2개가 600초 타임아웃으로 통째 죽었다(나머지 15배치 전부
    # 성공). agy는 배치가 클수록 세션이 길어지므로 쪼개면 대부분
    # 회복된다. 반토막은 크기가 단조 감소해 _SPLIT_MIN 미만에서 종결 —
    # 무한 재시도가 불가능하다.
    from collections import deque
    from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

    queue = deque(todo[i:i + _BATCH] for i in range(0, len(todo), _BATCH))
    failed_batches = 0
    workers = _workers()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures: dict = {}

        def _pump() -> None:
            while queue and len(futures) < workers:
                b = queue.popleft()
                futures[ex.submit(_run_cli, _build_prompt(b), crops)] = b

        while queue or futures:
            # 취소 검사는 반드시 제출(_pump)보다 먼저 — 취소가 이미 도착한
            # 상태에서 CLI를 한 번이라도 더 띄우면 안 된다. 도는 중인 CLI는
            # 제 타임아웃까지 알아서 끝나고 결과는 버려진다.
            if should_continue is not None and not should_continue():
                ex.shutdown(wait=False, cancel_futures=True)
                raise asyncio.CancelledError
            _pump()
            finished, _ = wait(set(futures), timeout=5.0,
                               return_when=FIRST_COMPLETED)
            for fut in finished:
                batch = futures.pop(fut)
                try:
                    parsed = json.loads(_strip_fences(fut.result()))
                    for k, v in parsed.items():
                        if k in batch and isinstance(v, str):
                            done[k] = v
                except Exception as exc:  # noqa: BLE001 — 배치 하나 실패로 전체를 죽이지 않는다
                    if len(batch) >= _SPLIT_MIN:
                        mid = len(batch) // 2
                        queue.append(batch[:mid])
                        queue.append(batch[mid:])
                        logger.warning(
                            "xsheet-transcribe: 배치 실패(%s, %d장) — "
                            "반으로 나눠 재시도: %s", batch[0], len(batch), exc)
                    else:
                        failed_batches += 1
                        logger.warning("xsheet-transcribe: 배치 실패(%s): %s",
                                       batch[0], exc)
            if finished:
                cache_path.write_text(
                    json.dumps(done, ensure_ascii=False, indent=1),
                    encoding="utf-8")
                if on_progress is not None and all_names:
                    on_progress(
                        sum(1 for n in all_names if n in done) / len(all_names))
            _pump()
    if failed_batches:
        logger.warning("xsheet-transcribe: %d개 배치 실패 — 해당 노트는 "
                       "주석이 빠진다(편집기 수동 추가 대상)", failed_batches)

    out: list[PdfBlock] = []
    for b, name in zip(blocks, names):
        text = (done.get(name) or "").strip()
        if _USABLE.search(text):
            out.append(replace(b, text=text))
    return out


def _build_prompt(batch: list[str]) -> str:
    return (
        "Open each of these PNG files in this directory and transcribe the "
        "handwritten all-caps English text in each, exactly as written. They "
        "are director notes from animation exposure sheets; a crop may contain "
        "circled single letters, arrows or stray marks - transcribe the "
        "readable words only, use \"\" if nothing readable. Reply ONLY as a "
        "JSON object mapping each filename to its transcription (\\n for "
        "line breaks). Files: " + ", ".join(batch)
    )


def _run_cli(prompt: str, cwd: Path) -> str:
    from apps.server.domain.video_captions.translate_cli import resolve_cli

    name = os.environ.get(ENV_CLI, "agy").strip() or "agy"
    path = resolve_cli(name)
    if path is None:
        raise RuntimeError(
            f"전사 CLI({name})를 찾지 못했습니다 — 설치/로그인 후 다시 시도")
    extra = shlex.split(os.environ.get(ENV_EXTRA_ARGS, ""))
    argv = [path, "-p", prompt, "--add-dir", ".",
            "--print-timeout", "8m", *extra]
    # encoding 명시: Windows 한글 로케일에서 UTF-8 출력이 cp949 디코딩에
    # 실패하면 stdout이 None이 된다(report_summary.py 선례, f004487)
    result = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
        timeout=_CALL_TIMEOUT, check=False, **_NO_WINDOW)
    if result.returncode != 0 or not (result.stdout or "").strip():
        detail = (result.stderr or result.stdout or "").strip()[:200]
        raise RuntimeError(f"전사 CLI 응답 없음(rc={result.returncode}): {detail}")
    return result.stdout


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        stripped = stripped[first_newline + 1:] if first_newline != -1 else stripped[3:]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
    return stripped.strip()
