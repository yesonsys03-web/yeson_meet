# apps/server/ai/mlx_worker.py
# === ANCHOR: MLX_WORKER_START ===
"""MLX 번역 워커 + 모델 다운로드 원샷 (서버 바이너리 자기-재실행 진입점).

- run_worker(): stdin JSONL 요청 → stdout JSONL 응답. 모델 로드 후 status:ready.
  mlx-lm import는 이 함수 안에서만 (인텔/리눅스 빌드에서 서버 본체 임포트 보호).
- run_download(model_id): huggingface에서 {STORAGE_ROOT}/mlx_models/<id>로 스냅샷
  다운로드, 진행 상황을 JSONL로 stdout에 출력 (콘솔이 파싱).
- YESON_MLX_FAKE=1: 모델 없이 에코 응답 — 프로토콜 테스트/번들 스모크용.
"""
from __future__ import annotations

import json
import os
import sys
import time

_SYSTEM_PROMPT = (
    "You are a professional simultaneous interpreter for a live business meeting. "
    "Translate the current English sentence into natural, fluent Korean. "
    "The English comes from live speech recognition and may contain recognition "
    "errors, disfluencies, or odd punctuation — infer the intended meaning from "
    "context and translate that meaning. Use the preceding dialogue as context. "
    "Use consistent polite Korean (합니다체). "
    "Output ONLY the Korean translation of the current sentence — no quotes, "
    "no explanations."
)


def _build_user(context: list[list[str]], en: str) -> str:
    parts: list[str] = []
    if context:
        parts.append("Preceding dialogue:")
        for c_en, c_ko in context:
            parts.append(f"EN: {c_en}")
            parts.append(f"KO: {c_ko}")
        parts.append("")
    parts.append("Current sentence:")
    parts.append(f"EN: {en}")
    return "\n".join(parts)


def _emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def _make_translate():
    """(en, context) -> ko 클로저를 만든다. 페이크/실모델 분기."""
    if os.environ.get("YESON_MLX_FAKE") == "1":
        return lambda en, context: f"[fake] {en}"

    model_path = os.environ.get("YESON_MLX_MODEL_PATH", "")
    if not model_path or not os.path.isfile(os.path.join(model_path, "config.json")):
        _emit({"type": "status", "state": "error",
               "reason": f"missing_mlx_model: {model_path or '(unset)'}"})
        raise SystemExit(1)

    # 지연 import — mlx 미설치 플랫폼에서 서버 본체를 오염시키지 않는다.
    from mlx_lm import generate, load
    from mlx_lm.sample_utils import make_sampler

    model, tokenizer = load(model_path)
    sampler = make_sampler(temp=0.0)

    def _translate(en: str, context: list[list[str]]) -> str:
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user(context, en)},
        ]
        try:
            prompt = tokenizer.apply_chat_template(
                messages, add_generation_prompt=True, enable_thinking=False)
        except TypeError:  # enable_thinking 미지원 템플릿
            prompt = tokenizer.apply_chat_template(messages, add_generation_prompt=True)
        text = generate(model, tokenizer, prompt=prompt, max_tokens=256,
                        sampler=sampler, verbose=False)
        ko = text.strip()
        if "</think>" in ko:
            ko = ko.split("</think>", 1)[1].strip()
        return ko

    return _translate


def run_worker() -> int:
    try:
        translate = _make_translate()
    except SystemExit as exc:
        return int(exc.code or 1)
    _emit({"type": "status", "state": "ready"})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            req_id = req["id"]
            en = str(req["en"])
            context = [[str(a), str(b)] for a, b in req.get("context", [])]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            print(f"mlx-worker: bad request line: {line[:120]}", file=sys.stderr, flush=True)
            continue
        t0 = time.perf_counter()
        try:
            ko = translate(en, context)
        except Exception as exc:  # noqa: BLE001 — 요청 하나의 실패가 워커를 죽이면 안 됨
            print(f"mlx-worker: translate failed: {exc}", file=sys.stderr, flush=True)
            ko = ""
        _emit({"id": req_id, "ko": ko, "gen_ms": round((time.perf_counter() - t0) * 1000)})
    return 0  # stdin EOF = 정상 종료


def run_download(model_id: str) -> int:
    """모델 스냅샷을 {STORAGE_ROOT}/mlx_models/<id>로 받는다. 진행률 JSONL 출력."""
    from apps.server.ai.mlx_live_translate import mlx_model_dir

    target = mlx_model_dir(model_id)
    target.mkdir(parents=True, exist_ok=True)
    _emit({"type": "download", "state": "start", "model": model_id, "dir": str(target)})
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(model_id, local_dir=str(target))
    except Exception as exc:  # noqa: BLE001 — 콘솔에 읽을 수 있는 실패 사유 전달
        _emit({"type": "download", "state": "error", "reason": f"{type(exc).__name__}: {exc}"})
        return 1
    if not (target / "config.json").is_file():
        _emit({"type": "download", "state": "error", "reason": "config.json missing after download"})
        return 1
    _emit({"type": "download", "state": "done", "model": model_id})
    return 0
# === ANCHOR: MLX_WORKER_END ===
