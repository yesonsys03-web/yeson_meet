from __future__ import annotations

import sys
import textwrap

import pytest

from apps.server.ai.mlx_live_translate import MlxWorkerClient


def _script_argv(tmp_path, body: str) -> list[str]:
    script = tmp_path / "fake_raw_worker.py"
    script.write_text(textwrap.dedent(body))
    return [sys.executable, str(script)]


RAW_ECHO_WORKER = """\
    import json, sys
    print(json.dumps({"type": "status", "state": "ready"}), flush=True)
    for line in sys.stdin:
        req = json.loads(line)
        print(json.dumps({"id": req["id"], "text": "RAW:" + req["prompt"], "gen_ms": 1}), flush=True)
"""


async def test_generate_roundtrip(tmp_path):
    client = MlxWorkerClient(argv=_script_argv(tmp_path, RAW_ECHO_WORKER))
    await client.start()
    try:
        out = await client.generate("hello prompt", timeout=5.0)
        assert out == "RAW:hello prompt"
    finally:
        await client.close()


async def test_model_id_sets_env(tmp_path, monkeypatch):
    captured = {}

    async def fake_create(*argv, **kwargs):
        captured["env"] = kwargs.get("env", {})
        raise RuntimeError("stop before real spawn")

    import apps.server.ai.mlx_live_translate as mod
    monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", fake_create)
    client = MlxWorkerClient(model_id="mlx-community/Qwen3.5-4B-4bit")
    with pytest.raises(Exception):
        await client.start()
    assert "Qwen3.5-4B-4bit" in captured["env"]["YESON_MLX_MODEL_PATH"]
