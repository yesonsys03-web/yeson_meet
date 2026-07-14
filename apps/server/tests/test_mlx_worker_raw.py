from __future__ import annotations

import json
import subprocess
import sys

import pytest


def _run_fake_worker(requests: list[dict]) -> list[dict]:
    """YESON_MLX_FAKE=1 워커를 서브프로세스로 띄워 요청들을 보내고 응답 JSON들을 모은다."""
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "from apps.server.ai.mlx_worker import run_worker; raise SystemExit(run_worker())"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
        env={"YESON_MLX_FAKE": "1", "PYTHONPATH": "."},
    )
    payload = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in requests)
    out, _err = proc.communicate(payload, timeout=30)
    events = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def test_raw_prompt_request_returns_text():
    events = _run_fake_worker([{"id": 1, "prompt": "Translate: hello"}])
    ready = [e for e in events if e.get("type") == "status"]
    assert ready and ready[0]["state"] == "ready"
    resp = [e for e in events if e.get("id") == 1]
    assert resp, f"no id=1 response in {events}"
    assert "text" in resp[0]
    assert "Translate: hello" in resp[0]["text"]  # fake echo


def test_structured_en_request_still_works():
    events = _run_fake_worker([{"id": 2, "en": "hello", "context": []}])
    resp = [e for e in events if e.get("id") == 2]
    assert resp and "ko" in resp[0]
    assert resp[0]["ko"] == "[fake] hello"
