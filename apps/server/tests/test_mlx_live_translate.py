# === ANCHOR: TEST_MLX_LIVE_TRANSLATE_START ===
from __future__ import annotations

import os
from apps.server.ai.mlx_live_translate import (
    DEFAULT_MLX_MODEL,
    guard_mlx_ko,
    mlx_live_available,
    mlx_model_dir,
    mlx_model_id,
    mlx_model_installed,
)


class TestGuardMlxKo:
    def test_clean_translation_passes(self):
        assert guard_mlx_ko(
            "And I put all of my projects in my documents folder.",
            "그리고 저는 모든 프로젝트를 문서 폴더에 저장합니다.",
        ) is None

    def test_partial_english_terms_allowed(self):
        # 기술 자막에서 흔한 부분 영어 잔존은 허용
        assert guard_mlx_ko(
            "Please turn this into a landing page.",
            "이걸 landing page로 만들어 주세요.",
        ) is None

    def test_cjk_hanzi_rejected(self):
        assert guard_mlx_ko("So this is codex.", "이것이 코다克斯입니다.") == "foreign_script"

    def test_kana_rejected(self):
        assert guard_mlx_ko("Let's do it.", "해보ましょう.") == "foreign_script"

    def test_cyrillic_rejected(self):
        assert guard_mlx_ko("Open codex.", "코드КС를 여세요.") == "foreign_script"

    def test_replacement_char_rejected(self):
        assert guard_mlx_ko("Open it.", "여세요�.") == "foreign_script"

    def test_invented_number_rejected(self):
        # 벤치 실측: EN에 숫자가 없는데 "53만 달러" 환각
        assert guard_mlx_ko(
            "I will create a new project.", "53만 달러로 새 프로젝트를 만들 것입니다."
        ) == "invented_number"

    def test_number_present_in_en_passes(self):
        assert guard_mlx_ko("On base 44.", "베이스 44에서요.") is None

    def test_en_digit_missing_in_ko_allowed(self):
        # KO가 숫자를 한글로 풀어쓴 경우 허용 (EN→KO 방향 누락은 통과)
        assert guard_mlx_ko("It takes 2 minutes.", "이 분 정도 걸립니다.") is None

    def test_empty_rejected(self):
        assert guard_mlx_ko("Hello there.", "") == "empty"
        assert guard_mlx_ko("Hello there.", "   ") == "empty"

    def test_length_explosion_rejected(self):
        assert guard_mlx_ko("Hi.", "이 문장은 원문보다 지나치게 길어진 설명 폭주 사례입니다." * 3) == "length_ratio"

    def test_length_collapse_rejected(self):
        long_en = "And I can say, please, turn this into a landing page, a good learning resource for my viewers."
        assert guard_mlx_ko(long_en, "네.") == "length_ratio"

    def test_english_leak_rejected(self):
        assert guard_mlx_ko(
            "I can mention any file created within this folder.",
            "I can mention any file 폴더.",
        ) == "english_leak"

    def test_repetition_rejected(self):
        # 벤치 실측: "분류하고 분류하여" 류 반복 붕괴
        chunk = "분류하고 정리하여 저장하는 "
        assert guard_mlx_ko(
            "Sort and organize the files in the folder now.", chunk * 4
        ) == "repetition"
# === ANCHOR: TEST_MLX_LIVE_TRANSLATE_END ===


class TestModelResolution:
    def test_default_model_id(self, monkeypatch):
        monkeypatch.delenv("YESON_MLX_MODEL", raising=False)
        assert mlx_model_id() == DEFAULT_MLX_MODEL == "mlx-community/Qwen3.5-9B-4bit"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("YESON_MLX_MODEL", "mlx-community/Qwen3.5-4B-4bit")
        assert mlx_model_id() == "mlx-community/Qwen3.5-4B-4bit"

    def test_model_dir_sanitizes_slash(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
        d = mlx_model_dir("mlx-community/Qwen3.5-9B-4bit")
        assert d == tmp_path / "mlx_models" / "mlx-community--Qwen3.5-9B-4bit"

    def test_installed_requires_config_json(self, monkeypatch, tmp_path):
        monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
        model = "mlx-community/Qwen3.5-9B-4bit"
        assert mlx_model_installed(model) is False
        d = mlx_model_dir(model)
        d.mkdir(parents=True)
        (d / "config.json").write_text("{}")
        assert mlx_model_installed(model) is True

    def test_available_needs_both_gates(self, monkeypatch, tmp_path):
        import apps.server.ai.mlx_live_translate as mod
        monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
        monkeypatch.delenv("YESON_MLX_MODEL", raising=False)
        # 모델 미설치 + apple 게이팅 True → False
        monkeypatch.setattr(mod, "apple_stt_available", lambda: True)
        assert mlx_live_available() is False
        # 모델 설치 + apple 게이팅 False → False
        d = mlx_model_dir(DEFAULT_MLX_MODEL)
        d.mkdir(parents=True)
        (d / "config.json").write_text("{}")
        monkeypatch.setattr(mod, "apple_stt_available", lambda: False)
        assert mlx_live_available() is False
        # 둘 다 → True
        monkeypatch.setattr(mod, "apple_stt_available", lambda: True)
        assert mlx_live_available() is True


import asyncio
import sys
import textwrap

import pytest

from apps.server.ai.mlx_live_translate import MlxWorkerClient, MlxWorkerUnavailable


def _script_argv(tmp_path, body: str) -> list[str]:
    script = tmp_path / "fake_worker.py"
    script.write_text(textwrap.dedent(body))
    return [sys.executable, str(script)]


ECHO_WORKER = """\
    import json, sys
    print(json.dumps({"type": "status", "state": "ready"}), flush=True)
    for line in sys.stdin:
        req = json.loads(line)
        print(json.dumps({"id": req["id"], "ko": "KO:" + req["en"], "gen_ms": 1}), flush=True)
"""

NEVER_READY_WORKER = """\
    import time
    time.sleep(60)
"""

DIES_AFTER_READY_WORKER = """\
    import json, sys
    print(json.dumps({"type": "status", "state": "ready"}), flush=True)
    sys.exit(9)
"""


class TestMlxWorkerClient:
    def test_start_and_translate(self, tmp_path):
        async def run():
            client = MlxWorkerClient(argv=_script_argv(tmp_path, ECHO_WORKER))
            await client.start()
            assert client.alive
            ko = await client.translate("Hello.", [("Hi.", "안녕.")], timeout=5.0)
            assert ko == "KO:Hello."
            await client.close()
            assert not client.alive
        asyncio.run(run())

    def test_ready_timeout_raises_unavailable(self, tmp_path):
        async def run():
            client = MlxWorkerClient(
                argv=_script_argv(tmp_path, NEVER_READY_WORKER), ready_timeout=0.5)
            with pytest.raises(MlxWorkerUnavailable):
                await client.start()
            assert not client.alive
        asyncio.run(run())

    def test_death_during_translate_raises_unavailable(self, tmp_path):
        async def run():
            client = MlxWorkerClient(argv=_script_argv(tmp_path, DIES_AFTER_READY_WORKER))
            await client.start()
            with pytest.raises(MlxWorkerUnavailable):
                await client.translate("Hello.", [], timeout=5.0)
        asyncio.run(run())
