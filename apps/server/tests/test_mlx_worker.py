# apps/server/tests/test_mlx_worker.py
# === ANCHOR: TEST_MLX_WORKER_START ===
from __future__ import annotations

import json
import os
import subprocess
import sys


def _spawn_fake_worker():
    return subprocess.Popen(
        [sys.executable, "-c",
         "from apps.server.ai.mlx_worker import run_worker; import sys; sys.exit(run_worker())"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env={"YESON_MLX_FAKE": "1", "PATH": "/usr/bin:/bin", "PYTHONPATH": os.getcwd()},
    )


class TestFakeWorker:
    def test_ready_then_echo_roundtrip(self):
        proc = _spawn_fake_worker()
        try:
            ready = json.loads(proc.stdout.readline())
            assert ready == {"type": "status", "state": "ready"}
            req = {"id": 7, "en": "Hello there.", "context": [["Hi.", "안녕."]], "glossary": {}}
            proc.stdin.write(json.dumps(req) + "\n")
            proc.stdin.flush()
            resp = json.loads(proc.stdout.readline())
            assert resp["id"] == 7
            assert resp["ko"] == "[fake] Hello there."   # 페이크 = 에코
            assert isinstance(resp["gen_ms"], int)
        finally:
            proc.stdin.close()
            assert proc.wait(timeout=5) == 0  # stdin EOF → 정상 종료

    def test_bad_json_line_ignored(self):
        proc = _spawn_fake_worker()
        try:
            proc.stdout.readline()  # ready
            proc.stdin.write("not-json\n")
            proc.stdin.write(json.dumps({"id": 1, "en": "A.", "context": [], "glossary": {}}) + "\n")
            proc.stdin.flush()
            resp = json.loads(proc.stdout.readline())
            assert resp["id"] == 1  # 깨진 줄은 무시하고 다음 요청 처리
        finally:
            proc.stdin.close()
            proc.wait(timeout=5)

    def test_missing_model_reports_error(self):
        proc = subprocess.Popen(
            [sys.executable, "-c",
             "from apps.server.ai.mlx_worker import run_worker; import sys; sys.exit(run_worker())"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
            env={"YESON_MLX_MODEL_PATH": "/nonexistent/model", "PATH": "/usr/bin:/bin", "PYTHONPATH": os.getcwd()},
        )
        try:
            status = json.loads(proc.stdout.readline())
            assert status["type"] == "status" and status["state"] == "error"
            assert status["reason"].startswith("missing_mlx_model")
            assert proc.wait(timeout=5) == 1
        finally:
            proc.stdin.close()

    def test_startup_exception_reports_error(self, tmp_path):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "config.json").write_text("{}")
        proc = subprocess.Popen(
            [sys.executable, "-c",
             "from apps.server.ai.mlx_worker import run_worker; import sys; sys.exit(run_worker())"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
            env={"YESON_MLX_MODEL_PATH": str(model_dir), "PATH": "/usr/bin:/bin", "PYTHONPATH": os.getcwd()},
        )
        try:
            status = json.loads(proc.stdout.readline())
            assert status["type"] == "status" and status["state"] == "error"
            assert status["reason"].startswith("mlx_startup_failed")
            assert proc.wait(timeout=5) == 1
        finally:
            proc.stdin.close()
# === ANCHOR: TEST_MLX_WORKER_END ===
