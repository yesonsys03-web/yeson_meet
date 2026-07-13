# apps/server/tests/test_mlx_worker.py
# === ANCHOR: TEST_MLX_WORKER_START ===
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


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


class TestRunDownloadProgress:
    """run_download은 파일 단위로 progress를 emit해야 한다 (진짜 네트워크 금지).

    run_download 내부에서 `from huggingface_hub import HfApi, hf_hub_download`가
    호출 시점에 지연 import되므로, huggingface_hub 모듈 자체의 속성을
    monkeypatch해야 한다 (run_download가 가진 로컬 바인딩이 아니라).
    """

    def test_progress_events_then_done(self, tmp_path, monkeypatch, capsys):
        import huggingface_hub

        from apps.server.ai import mlx_worker
        from apps.server.ai.mlx_live_translate import mlx_model_dir

        monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
        model_id = "mlx-community/Fake-Model"
        files = ["model.safetensors", "tokenizer.json", "config.json"]
        calls: list[str] = []

        class _FakeHfApi:
            def list_repo_files(self, repo_id):
                assert repo_id == model_id
                return files

        def _fake_hf_hub_download(repo_id, filename, local_dir=None, **kwargs):
            assert repo_id == model_id
            calls.append(filename)
            d = Path(local_dir)
            d.mkdir(parents=True, exist_ok=True)
            (d / filename).write_text("{}" if filename == "config.json" else "x")
            return str(d / filename)

        monkeypatch.setattr(huggingface_hub, "HfApi", _FakeHfApi)
        monkeypatch.setattr(huggingface_hub, "hf_hub_download", _fake_hf_hub_download)

        rc = mlx_worker.run_download(model_id)
        assert rc == 0
        assert calls == files

        out = capsys.readouterr().out.strip().splitlines()
        events = [json.loads(line) for line in out]
        assert events[0] == {
            "type": "download", "state": "start", "model": model_id,
            "dir": str(mlx_model_dir(model_id)),
        }
        progress = [e for e in events if e["state"] == "progress"]
        assert [p["name"] for p in progress] == files
        assert [p["file"] for p in progress] == [1, 2, 3]
        assert all(p["of"] == 3 for p in progress)
        assert events[-1] == {"type": "download", "state": "done", "model": model_id}

    def test_download_error_reported(self, tmp_path, monkeypatch, capsys):
        import huggingface_hub

        from apps.server.ai import mlx_worker

        monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
        model_id = "mlx-community/Fake-Model"

        class _FailingHfApi:
            def list_repo_files(self, repo_id):
                raise RuntimeError("network down")

        monkeypatch.setattr(huggingface_hub, "HfApi", _FailingHfApi)

        rc = mlx_worker.run_download(model_id)
        assert rc == 1

        events = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
        assert events[-1]["type"] == "download" and events[-1]["state"] == "error"
        assert "network down" in events[-1]["reason"]
# === ANCHOR: TEST_MLX_WORKER_END ===
