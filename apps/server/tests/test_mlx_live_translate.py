# === ANCHOR: TEST_MLX_LIVE_TRANSLATE_START ===
from __future__ import annotations

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

SILENT_AFTER_REQUEST_WORKER = """\
    import json, sys, time
    print(json.dumps({"type": "status", "state": "ready"}), flush=True)
    sys.stdin.readline()
    time.sleep(60)
"""

NOISY_NON_MATCHING_WORKER = """\
    import json, sys, time
    print(json.dumps({"type": "status", "state": "ready"}), flush=True)
    sys.stdin.readline()
    while True:
        print(json.dumps({"id": 999, "ko": "x", "gen_ms": 1}), flush=True)
        time.sleep(0.1)
"""

# ready 이전에 non-JSON 줄 + 무관 JSON 줄(status가 아닌 type)을 찍는 워커.
# mlx-lm/transformers가 stdout에 경고를 흘리는 상황을 모사한다.
NOISY_STARTUP_WORKER = """\
    import json, sys
    print("warning: some library banner", flush=True)
    print(json.dumps({"type": "other", "note": "irrelevant"}), flush=True)
    print(json.dumps({"type": "status", "state": "ready"}), flush=True)
    for line in sys.stdin:
        req = json.loads(line)
        print(json.dumps({"id": req["id"], "ko": "KO:" + req["en"], "gen_ms": 1}), flush=True)
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

    def test_translate_timeout_raises(self, tmp_path):
        async def run():
            client = MlxWorkerClient(
                argv=_script_argv(tmp_path, SILENT_AFTER_REQUEST_WORKER))
            await client.start()
            with pytest.raises(asyncio.TimeoutError):
                await client.translate("Hello.", [], timeout=0.4)
        asyncio.run(run())

    def test_timeout_is_total_budget_despite_noise(self, tmp_path):
        import time

        async def run():
            client = MlxWorkerClient(
                argv=_script_argv(tmp_path, NOISY_NON_MATCHING_WORKER))
            await client.start()
            start = time.monotonic()
            with pytest.raises(asyncio.TimeoutError):
                await client.translate("Hello.", [], timeout=0.5)
            elapsed = time.monotonic() - start
            assert elapsed < 2.0
        asyncio.run(run())

    def test_noisy_startup_lines_are_skipped(self, tmp_path):
        # mlx-lm/transformers 등이 stdout에 경고/무관 JSON을 찍어도 ready 이벤트가
        # 나올 때까지 스킵하고 기다려야 한다 (즉사 금지).
        async def run():
            client = MlxWorkerClient(argv=_script_argv(tmp_path, NOISY_STARTUP_WORKER))
            await client.start()
            assert client.alive
            ko = await client.translate("Hello.", [], timeout=5.0)
            assert ko == "KO:Hello."
            await client.close()
        asyncio.run(run())

    def test_ready_timeout_message_includes_stderr_tail(self, tmp_path):
        # ready 실패 시 MlxWorkerUnavailable 메시지에 stderr tail이 포함돼야 한다
        # (기존 start 실패 테스트가 여전히 통과하는지도 함께 검증).
        worker = """\
            import sys
            print("boom: fatal init error", file=sys.stderr, flush=True)
            import time
            time.sleep(60)
        """
        async def run():
            client = MlxWorkerClient(
                argv=_script_argv(tmp_path, worker), ready_timeout=0.5)
            with pytest.raises(MlxWorkerUnavailable, match="boom: fatal init error"):
                await client.start()
            assert not client.alive
        asyncio.run(run())


from datetime import datetime, timezone

from apps.server.ai.mlx_live_translate import MlxRefinedAppleProvider
from apps.server.ai.providers import TranslatedUtterance


def _utt(seq, en, ko, final, segment=1):
    now = datetime.now(timezone.utc)
    return TranslatedUtterance(seq=seq, text_en=en, text_ko=ko, started_at=now,
                               ended_at=now, is_final=final, provider_segment=segment)


class _FakeInner:
    """미리 정의된 utterance 시퀀스를 방출하는 STTProvider."""
    def __init__(self, utterances, error_after=None):
        self._utterances = utterances
        self._error_after = error_after

    async def stream(self, audio, lang_hint):
        for i, u in enumerate(self._utterances):
            if self._error_after is not None and i == self._error_after:
                raise RuntimeError("inner boom")
            # 실제 inner(subprocess stdout 읽기)는 매 utterance마다 진짜 I/O
            # 서스펜션을 겪는다 — 페이크도 체크포인트를 하나 둬서 백그라운드
            # 스폰 태스크가 끼어들 기회를 realistically 준다.
            await asyncio.sleep(0)
            yield u
        # error_after가 마지막 유효 인덱스 다음(len(utterances))을 가리키면
        # 전량 방출 직후 죽는 상황 — enumerate만으로는 도달 불가능해 별도 처리.
        if self._error_after == len(self._utterances):
            raise RuntimeError("inner boom")


class _FakeClient:
    """MlxWorkerClient 시늉: 응답 사전/지연/사망 시나리오 주입."""
    def __init__(self, responses=None, start_error=False, hang=False,
                 start_delay: float = 0.0):
        self._responses = responses or {}
        self._start_error = start_error
        self._hang = hang
        self._start_delay = start_delay
        self.requests: list[tuple[str, list]] = []
        self.closed = False
        self.alive = False

    async def start(self):
        if self._start_delay:
            await asyncio.sleep(self._start_delay)
        if self._start_error:
            raise MlxWorkerUnavailable("no model")
        self.alive = True

    async def translate(self, en, context, timeout):
        self.requests.append((en, list(context)))
        if self._hang:
            await asyncio.sleep(timeout + 1)  # wait_for가 아니라 호출자가 timeout 처리
            raise asyncio.TimeoutError()
        if en in self._responses:
            resp = self._responses[en]
            if isinstance(resp, Exception):
                self.alive = False
                raise resp
            return resp
        return f"MLX:{en}"

    async def close(self):
        self.closed = True
        self.alive = False


async def _collect(provider):
    async def _no_audio():
        return
        yield  # pragma: no cover
    return [u async for u in provider.stream(_no_audio(), "en")]


class TestMlxRefinedAppleProvider:
    def test_partial_passthrough_final_refined(self):
        inner = _FakeInner([
            _utt(1, "Hello", "안녕(파셜)", final=False),
            _utt(1, "Hello there.", "안녕하세요(애플)", final=True),
        ])
        client = _FakeClient(responses={"Hello there.": "안녕하십니까(MLX)"})
        provider = MlxRefinedAppleProvider(inner=inner, client_factory=lambda: client)

        out = asyncio.run(_collect(provider))
        assert out[0].text_ko == "안녕(파셜)" and not out[0].is_final
        finals = [u for u in out if u.is_final]
        assert finals[0].text_ko == "안녕하십니까(MLX)"
        assert finals[0].seq == 1
        assert client.closed  # 스트림 종료 시 워커 정리

    def test_guard_reject_falls_back_to_apple(self):
        inner = _FakeInner([_utt(1, "Open codex.", "코덱스를 여세요(애플)", final=True)])
        client = _FakeClient(responses={"Open codex.": "코다克斯를 여세요"})  # 한자 혼입
        provider = MlxRefinedAppleProvider(inner=inner, client_factory=lambda: client)
        out = asyncio.run(_collect(provider))
        assert out[0].text_ko == "코덱스를 여세요(애플)"

    def test_worker_start_failure_means_apple_only(self):
        inner = _FakeInner([_utt(1, "Hello there.", "안녕하세요(애플)", final=True)])
        client = _FakeClient(start_error=True)
        provider = MlxRefinedAppleProvider(inner=inner, client_factory=lambda: client)
        out = asyncio.run(_collect(provider))
        assert out[0].text_ko == "안녕하세요(애플)"  # 예외 없이 폴백

    def test_worker_death_falls_back_and_respawns(self):
        inner = _FakeInner([
            _utt(1, "One.", "하나(애플)", final=True),
            _utt(2, "Two.", "둘(애플)", final=True),
        ])
        dead_client = _FakeClient(responses={"One.": MlxWorkerUnavailable("died")})
        fresh_client = _FakeClient(responses={"Two.": "둘(MLX)"})
        clients = [dead_client, fresh_client]
        provider = MlxRefinedAppleProvider(inner=inner, client_factory=lambda: clients.pop(0))
        out = asyncio.run(_collect(provider))
        finals = {u.seq: u.text_ko for u in out if u.is_final}
        assert finals[1] == "하나(애플)"   # 사망 → 폴백
        assert finals[2] == "둘(MLX)"     # 재스폰 후 정상 정제

    def test_context_uses_emitted_finals(self):
        inner = _FakeInner([
            _utt(1, "One.", "하나(애플)", final=True),
            _utt(2, "Two.", "둘(애플)", final=True),
        ])
        client = _FakeClient(responses={"One.": "하나(MLX)", "Two.": "둘(MLX)"})
        provider = MlxRefinedAppleProvider(inner=inner, client_factory=lambda: client)
        asyncio.run(_collect(provider))
        # 두 번째 요청의 문맥에 첫 번째의 (en, 발행 ko)가 들어간다
        assert client.requests[1][1] == [("One.", "하나(MLX)")]

    def test_inner_error_flushes_holds_then_reraises(self):
        inner = _FakeInner(
            [_utt(1, "One.", "하나(애플)", final=True)], error_after=1)
        client = _FakeClient(hang=True)  # 정제가 끝나기 전에 inner가 죽는 상황
        provider = MlxRefinedAppleProvider(
            inner=inner, client_factory=lambda: client, sentence_timeout=0.2)

        async def run():
            got = []
            with pytest.raises(RuntimeError, match="inner boom"):
                async def _no_audio():
                    return
                    yield  # pragma: no cover
                async for u in provider.stream(_no_audio(), "en"):
                    got.append(u)
            return got

        got = asyncio.run(run())
        finals = [u for u in got if u.is_final]
        assert finals and finals[0].text_ko == "하나(애플)"  # 홀드 플러시 후 재전파

    def test_cold_start_spawns_single_worker(self):
        # 워밍업 태스크와 첫 _refine이 동시에 client_ready==False를 보는
        # 콜드 스타트 경합 — start_delay로 스폰 윈도우를 벌려 경합을 유발한다.
        inner = _FakeInner([_utt(1, "Hello there.", "안녕하세요(애플)", final=True)])
        created: list[_FakeClient] = []

        def factory():
            c = _FakeClient(start_delay=0.2)
            created.append(c)
            return c

        provider = MlxRefinedAppleProvider(inner=inner, client_factory=factory)
        asyncio.run(_collect(provider))
        assert len(created) == 1  # 단일 스폰으로 코얼레스
        assert all(c.closed for c in created)  # 살아남은 클라이언트도 종료 시 정리

    def test_final_during_slow_start_falls_back_immediately(self):
        # Critical: 워커 로드(콜드 최대 120s)가 진행 중일 때 도착한 파이널이
        # 스폰 완료를 기다려선 안 된다 — 즉시 Apple KO로 발행돼야 한다.
        import time

        inner = _FakeInner([_utt(1, "Hello there.", "안녕하세요(애플)", final=True)])
        created: list[_FakeClient] = []

        def factory():
            c = _FakeClient(start_delay=1.0)
            created.append(c)
            return c

        provider = MlxRefinedAppleProvider(inner=inner, client_factory=factory)

        async def run():
            start = time.monotonic()
            out = await _collect(provider)
            elapsed = time.monotonic() - start
            return out, elapsed

        out, elapsed = asyncio.run(run())
        assert elapsed < 0.5  # 스폰(1.0s)을 기다리지 않고 즉시 발행됨
        finals = [u for u in out if u.is_final]
        assert finals[0].text_ko == "안녕하세요(애플)"  # Apple KO 폴백, 예외 없음
        assert len(created) == 1
        assert all(c.closed for c in created)  # 스트림 종료 후 생성된 클라이언트도 정리됨

    def test_dead_client_closed_on_respawn(self):
        inner = _FakeInner([
            _utt(1, "One.", "하나(애플)", final=True),
            _utt(2, "Two.", "둘(애플)", final=True),
        ])
        dead_client = _FakeClient(responses={"One.": MlxWorkerUnavailable("died")})
        fresh_client = _FakeClient(responses={"Two.": "둘(MLX)"})
        clients = [dead_client, fresh_client]
        provider = MlxRefinedAppleProvider(inner=inner, client_factory=lambda: clients.pop(0))
        asyncio.run(_collect(provider))
        assert dead_client.closed is True  # 재스폰 시 죽은 클라이언트도 정리됨


from apps.server.ws.sidecar import create_ai_provider


class TestProviderRegistration:
    def test_registered_when_available(self, monkeypatch):
        import apps.server.ws.sidecar as sidecar_mod
        monkeypatch.setenv("YESON_AI_PROVIDER", "apple_mlx_live_translate")
        monkeypatch.setattr(
            "apps.server.ai.mlx_live_translate.mlx_live_available", lambda: True)
        provider = sidecar_mod.create_ai_provider()
        assert type(provider).__name__ == "MlxRefinedAppleProvider"

    def test_alias_apple_mlx(self, monkeypatch):
        monkeypatch.setenv("YESON_AI_PROVIDER", "apple_mlx")
        monkeypatch.setattr(
            "apps.server.ai.mlx_live_translate.mlx_live_available", lambda: True)
        assert create_ai_provider() is not None

    def test_unavailable_returns_none(self, monkeypatch):
        # 게이팅 미충족 → None (S2 count-only) — apple provider와 동일 관례
        monkeypatch.setenv("YESON_AI_PROVIDER", "apple_mlx_live_translate")
        monkeypatch.setattr(
            "apps.server.ai.mlx_live_translate.mlx_live_available", lambda: False)
        assert create_ai_provider() is None


def test_is_english_leak():
    from apps.server.ai.mlx_live_translate import is_english_leak

    assert is_english_leak("Margarita vibes, baby girl!") is True
    assert is_english_leak("마르가리타 분위기야!") is False
    assert is_english_leak("") is False
    # 한글에 고유명사가 섞인 정도는 누수가 아니다
    assert is_english_leak("Margarita 한 잔 하자") is False
# === ANCHOR: TEST_MLX_LIVE_TRANSLATE_END ===
