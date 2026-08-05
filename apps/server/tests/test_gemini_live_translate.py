# === ANCHOR: TEST_GEMINI_LIVE_TRANSLATE_START ===
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from apps.server.ai.gemini_live_translate import (
    GeminiLiveTranslateProvider,
    TranscriptAssembler,
)
from apps.server.ai.providers import TranslatedUtterance


def make_assembler(**kwargs: int) -> TranscriptAssembler:
    defaults = dict(
        provider_segment=1,
        force_final_chars=90,
        min_final_chars=2,
        max_utterance_ms=12000,
        idle_final_ms=2000,
        partial_min_delta_chars=4,
    )
    defaults.update(kwargs)
    return TranscriptAssembler(**defaults)


class TestTranscriptAssembler:
    def test_partial_emitted_after_min_delta(self) -> None:
        assembler = make_assembler()
        out = assembler.feed(" Good", " 안녕하세요", now_monotonic=0.0)
        assert len(out) == 1
        assert out[0].is_final is False
        assert out[0].seq == 1
        assert out[0].text_ko == "안녕하세요"
        assert out[0].text_en == "Good"

    def test_tiny_fragment_below_delta_is_buffered(self) -> None:
        assembler = make_assembler()
        assert assembler.feed(None, " 네", now_monotonic=0.0) == []
        out = assembler.feed(None, " 알겠습니다만", now_monotonic=0.5)
        assert len(out) == 1
        assert out[0].text_ko == "네 알겠습니다만"

    def test_sentence_boundary_finalizes_and_carries_remainder(self) -> None:
        assembler = make_assembler()
        assembler.feed(" First one.", " 첫 번째 회의를", now_monotonic=0.0)
        out = assembler.feed(" Second", " 시작하겠습니다. 두 번째", now_monotonic=1.0)
        assert [u.is_final for u in out] == [True]
        assert out[0].text_ko == "첫 번째 회의를 시작하겠습니다."
        assert out[0].seq == 1
        # 경계 뒤 꼬리(" 두 번째")는 다음 seq로 캐리
        nxt = assembler.feed(None, " 안건은 예산입니다", now_monotonic=1.5)
        assert nxt and nxt[0].seq == 2
        assert nxt[0].text_ko == "두 번째 안건은 예산입니다"

    def test_boundary_splits_before_trailing_clause(self) -> None:
        assembler = make_assembler()
        out = assembler.feed(None, " 시작하겠습니다. 두 번째 안건은", now_monotonic=0.0)
        assert [u.is_final for u in out] == [True]
        assert out[0].text_ko == "시작하겠습니다."
        follow = assembler.feed(None, " 예산입니다만 아직", now_monotonic=0.5)
        assert follow[0].seq == 2
        assert follow[0].text_ko == "두 번째 안건은 예산입니다만 아직"

    def test_decimal_number_is_not_a_boundary(self) -> None:
        assembler = make_assembler()
        out = assembler.feed(None, " 러닝타임은 1.5초로", now_monotonic=0.0)
        assert len(out) == 1
        assert out[0].is_final is False

    def test_force_final_on_length(self) -> None:
        assembler = make_assembler(force_final_chars=10)
        out = assembler.feed(None, " 열자를넘는아주긴한국어자막입니다", now_monotonic=0.0)
        assert [u.is_final for u in out] == [True]

    def test_force_final_on_age(self) -> None:
        assembler = make_assembler(max_utterance_ms=5000)
        assembler.feed(None, " 계속 이어지는", now_monotonic=0.0)
        out = assembler.feed(None, " 아주 긴 발화", now_monotonic=6.0)
        assert [u.is_final for u in out] == [True]

    def test_idle_poll_finalizes(self) -> None:
        assembler = make_assembler(idle_final_ms=2000)
        assembler.feed(None, " 마지막 한마디", now_monotonic=0.0)
        assert assembler.poll(now_monotonic=1.0) == []
        out = assembler.poll(now_monotonic=2.5)
        assert [u.is_final for u in out] == [True]
        assert assembler.poll(now_monotonic=3.0) == []

    def test_flush_emits_remaining_buffer(self) -> None:
        assembler = make_assembler()
        assembler.feed(" tail", " 남은 텍스트", now_monotonic=0.0)
        out = assembler.flush()
        assert [u.is_final for u in out] == [True]
        assert out[0].text_ko == "남은 텍스트"
        assert assembler.flush() == []

    def test_ko_corrections_applied(self) -> None:
        assembler = make_assembler()
        out = assembler.feed(None, " 연필 테스트 수정이 필요합니다.", now_monotonic=0.0)
        assert out[0].text_ko == "펜슬 테스트 수정이 필요합니다."

    def test_min_final_chars_merges_short_sentences(self) -> None:
        assembler = make_assembler(min_final_chars=20)
        # 문장 경계가 있어도 20자 미만이면 줄을 끊지 않고 계속 자란다(파셜)
        out = assembler.feed(None, " 좋아요.", now_monotonic=0.0)
        assert [u.is_final for u in out] == [False]
        out = assembler.feed(None, " 그럼 다음 안건으로 넘어가겠습니다.", now_monotonic=1.0)
        assert [u.is_final for u in out] == [True]
        assert out[0].text_ko == "좋아요. 그럼 다음 안건으로 넘어가겠습니다."
        assert out[0].seq == 1

    def test_min_final_chars_does_not_block_idle_flush(self) -> None:
        assembler = make_assembler(min_final_chars=40, idle_final_ms=2000)
        assembler.feed(None, " 네, 좋습니다.", now_monotonic=0.0)
        out = assembler.poll(now_monotonic=2.5)
        assert [u.is_final for u in out] == [True]
        assert out[0].text_ko == "네, 좋습니다."

    def test_provider_segment_is_stamped(self) -> None:
        assembler = make_assembler(provider_segment=7)
        out = assembler.feed(None, " 확인했습니다.", now_monotonic=0.0)
        assert out[0].provider_segment == 7


class FakeSession:
    def __init__(self, messages: list[object]) -> None:
        self._messages = messages
        self.sent: list[dict[str, object]] = []

    async def send_realtime_input(self, **kwargs: object) -> None:
        self.sent.append(kwargs)

    def receive(self):
        messages, self._messages = self._messages, []

        async def _gen():
            for message in messages:
                yield message

        return _gen()


class FakeLive:
    def __init__(self, session: FakeSession) -> None:
        self._session = session

    def connect(self, model: str, config: object):
        session = self._session
        session.model = model
        session.config = config

        class _Ctx:
            async def __aenter__(self) -> FakeSession:
                return session

            async def __aexit__(self, *exc: object) -> None:
                return None

        return _Ctx()


def fake_client(session: FakeSession) -> SimpleNamespace:
    return SimpleNamespace(aio=SimpleNamespace(live=FakeLive(session)))


def message(en: str | None = None, ko: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        server_content=SimpleNamespace(
            input_transcription=SimpleNamespace(text=en) if en else None,
            output_transcription=SimpleNamespace(text=ko) if ko else None,
        )
    )


async def _audio():
    yield b"\x01" * 640
    yield b"\x02" * 640


class TestGeminiLiveTranslateProvider:
    async def test_stream_assembles_and_sends_stream_end(self, monkeypatch) -> None:
        monkeypatch.setenv("GEMINI_LT_MIN_FINAL_CHARS", "2")
        session = FakeSession(
            [
                message(en=" Good morning."),
                message(ko=" 좋은 아침입니다."),
                message(en=" Thank you.", ko=" 감사합니다."),
            ]
        )
        provider = GeminiLiveTranslateProvider(
            api_key="test-key", client=fake_client(session)
        )
        utterances = [u async for u in provider.stream(_audio(), "en")]

        finals = [u for u in utterances if u.is_final]
        assert [u.text_ko for u in finals] == ["좋은 아침입니다.", "감사합니다."]
        assert [u.seq for u in finals] == [1, 2]
        assert finals[0].text_en == "Good morning."
        assert all(u.provider_segment == 1 for u in utterances)
        assert {"audio_stream_end": True} in session.sent
        audio_sends = [s for s in session.sent if "audio" in s]
        assert len(audio_sends) == 2
        assert session.model == "gemini-3.5-live-translate-preview"

    async def test_go_away_recycles_cleanly_with_flush(self, monkeypatch) -> None:
        monkeypatch.setenv("GEMINI_LT_MIN_FINAL_CHARS", "2")
        session = FakeSession(
            [
                message(ko=" 아직 안 끝난 문장"),
                SimpleNamespace(
                    server_content=None,
                    go_away=SimpleNamespace(time_left="10s"),
                ),
                message(ko=" go_away 이후 조각은 버려짐"),
            ]
        )
        provider = GeminiLiveTranslateProvider(
            api_key="test-key", client=fake_client(session)
        )
        utterances = [u async for u in provider.stream(_audio(), "en")]
        # go_away 시점의 버퍼가 유실 없이 final로 flush되고 스트림이 곱게 끝난다
        assert utterances[-1].is_final is True
        assert utterances[-1].text_ko == "아직 안 끝난 문장"

    async def test_reconnect_bumps_provider_segment(self, monkeypatch) -> None:
        monkeypatch.setenv("GEMINI_LT_MIN_FINAL_CHARS", "2")
        provider = GeminiLiveTranslateProvider(
            api_key="test-key",
            client=fake_client(FakeSession([message(ko=" 첫 세션입니다.")])),
        )
        first = [u async for u in provider.stream(_audio(), "en")]
        provider._client = fake_client(FakeSession([message(ko=" 두 번째 세션입니다.")]))
        second = [u async for u in provider.stream(_audio(), "en")]
        assert first[-1].provider_segment == 1
        assert second[-1].provider_segment == 2
        assert second[-1].seq == 1


class TestProviderRegistration:
    def test_create_ai_provider_dispatch(self, monkeypatch) -> None:
        from apps.server.ws.sidecar import create_ai_provider

        monkeypatch.setenv("YESON_AI_PROVIDER", "gemini_live_translate")
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        assert create_ai_provider() is None
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        provider = create_ai_provider()
        assert isinstance(provider, GeminiLiveTranslateProvider)

    def test_create_ai_provider_hybrid(self, monkeypatch) -> None:
        from apps.server.ai.gemini_live_translate import GeminiHybridTranslateProvider
        from apps.server.ws.sidecar import create_ai_provider

        monkeypatch.setenv("YESON_AI_PROVIDER", "gemini_hybrid")
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        assert create_ai_provider() is None
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        provider = create_ai_provider()
        assert isinstance(provider, GeminiHybridTranslateProvider)
        assert provider._final_translate is True
        # 하이브리드도 3.5 라이브 스트림 기반 — 기존 프로바이더의 서브클래스
        assert isinstance(provider, GeminiLiveTranslateProvider)


def text_client(reply=None, error=None, calls=None):
    """aio.models.generate_content만 있는 텍스트 번역용 fake client."""
    async def generate_content(*, model, contents, config):
        if calls is not None:
            calls.append({"model": model, "contents": contents})
        if error is not None:
            raise error
        return SimpleNamespace(text=reply)

    return SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )


class TestTranslateFinalText:
    async def test_success_returns_stripped_korean(self) -> None:
        from apps.server.ai.gemini_live_translate import _translate_final_text

        calls: list = []
        client = text_client(reply=" 클린업 팀이 5% 컷을 재작업합니다. ", calls=calls)
        out = await _translate_final_text(
            client, "The cleanup team will redo five percent of the cuts."
        )
        assert out == "클린업 팀이 5% 컷을 재작업합니다."
        # 단어집이 프롬프트에 주입되고 EN 원문이 포함된다
        assert "cleanup" in calls[0]["contents"]
        assert "five percent" in calls[0]["contents"]

    async def test_empty_en_returns_none_without_call(self) -> None:
        from apps.server.ai.gemini_live_translate import _translate_final_text

        calls: list = []
        client = text_client(reply="무엇이든", calls=calls)
        assert await _translate_final_text(client, "   ") is None
        assert calls == []

    async def test_error_returns_none(self) -> None:
        from apps.server.ai.gemini_live_translate import _translate_final_text

        client = text_client(error=RuntimeError("boom"))
        assert await _translate_final_text(client, "Hello.") is None

    async def test_empty_reply_returns_none(self) -> None:
        from apps.server.ai.gemini_live_translate import _translate_final_text

        client = text_client(reply="  ")
        assert await _translate_final_text(client, "Hello.") is None

    async def test_model_env_override(self, monkeypatch) -> None:
        from apps.server.ai.gemini_live_translate import _translate_final_text

        monkeypatch.setenv("GEMINI_FINAL_TRANSLATION_MODEL", "gemini-3.6-flash")
        calls: list = []
        await _translate_final_text(text_client(reply="안녕.", calls=calls), "Hi.")
        assert calls[0]["model"] == "gemini-3.6-flash"

    async def test_prompt_pins_fahrenheit_to_celsius(self) -> None:
        """미국 화자의 '90 degrees'(화씨)가 맨 '90도'(섭씨로 읽힘)로 나가지
        않도록, 프롬프트가 섭씨 환산 표기('약 32도')를 고정한다 — 한국 자막은
        섭씨 기준(실기 2026-07-23: '90도' 방치와 '37.8도' 무언 환산 혼재)."""
        from apps.server.ai.gemini_live_translate import _translate_final_text

        calls: list = []
        await _translate_final_text(
            text_client(reply="약 32도 날씨예요.", calls=calls),
            "It's 90 degree weather.",
        )
        assert "Fahrenheit" in calls[0]["contents"]
        assert "Celsius" in calls[0]["contents"]
        assert "약 32도" in calls[0]["contents"]

    async def test_prompt_bans_invented_number_units(self) -> None:
        """원문에 없는 조수사를 지어내지 않도록 고정한다 — 한국어는 수사 뒤
        조수사가 문법적으로 필수라, 구 규칙의 "with their units"가 있으면
        모델이 하나를 만들어낸다(실기 2026-08-04 보고서: 화번 305가
        305년/305건/305개로, 실행마다 다르게)."""
        from apps.server.ai.gemini_live_translate import _translate_final_text

        calls: list = []
        await _translate_final_text(
            text_client(reply="73이 아니라 49입니다.", calls=calls),
            "it's not 73, it's 49 back on that we have remaining.",
        )
        prompt = calls[0]["contents"]
        assert "never supply a" in prompt
        assert "Korean counter" in prompt
        # 온도 규칙이 맨숫자까지 끌어가지 않도록 하는 명시적 차단
        assert "Numbers with no temperature word are never 도" in prompt

    async def test_prompt_gates_fahrenheit_on_an_explicit_degree_word(self) -> None:
        """화씨 환산은 화자가 실제로 degree/temperature를 말했을 때만 — 구
        규칙의 예시('90, 93 degree weather')가 쉼표로 이어진 맨숫자 두 개
        모양이라, 온도와 무관한 "it's not 73, it's 49"까지 끌어다 73도/49도를
        만들었다(실기 2026-08-04). PR#67의 실제 날씨 환산 의도는 유지."""
        from apps.server.ai.gemini_live_translate import _translate_final_text

        calls: list = []
        await _translate_final_text(text_client(reply="약 32도예요.", calls=calls), "It's 90 degrees.")
        assert "Only when the speaker actually says 'degree(s)'" in calls[0]["contents"]

    async def test_prompt_pins_three_digit_number_to_episode(self) -> None:
        """세 자리 수는 화번('305화')으로 고정 — "bare number는 bare로"만으로는
        'of 305'·'in 305' 같은 전치사 구문에서 모델이 부분표현으로 읽어 개/건을
        붙인다(수정 1차 측정에서 5/5 잔존). 단 뒤에 단위어가 오면 진짜 수량이라
        예외를 함께 박는다("305 shots completed and 55 remaining")."""
        from apps.server.ai.gemini_live_translate import _translate_final_text

        calls: list = []
        await _translate_final_text(
            text_client(reply="305화의 100%입니다.", calls=calls),
            "do you have 100% of 305 that is complete?",
        )
        prompt = calls[0]["contents"]
        assert "episode number" in prompt
        assert "305화" in prompt
        # 진짜 수량을 화번으로 오염시키지 않는 예외가 반드시 함께 있어야 한다
        assert "unit word follows it" in prompt
        assert "305 샷" in prompt

    async def test_prompt_preserves_speaker_mood(self) -> None:
        """조건문·평서문·부정을 뒤집지 않도록 고정 — 문장별 호출이라 문맥이 없어
        회의록에 없던 약속이 생긴다(실기 2026-08-04: "you think that you can do
        them by next week" → "다음 주까지 하세요", "if we do not deliver" →
        "전달하고 있습니다", 잘린 문장에 없던 부정 삽입)."""
        from apps.server.ai.gemini_live_translate import _translate_final_text

        calls: list = []
        await _translate_final_text(
            text_client(reply="다음 주까지 하실 수 있다면요.", calls=calls),
            "and then you think that you can still do them by next week.",
        )
        prompt = calls[0]["contents"]
        assert "a conditional stays" in prompt
        assert "never becomes a command" in prompt
        assert "leave it unfinished" in prompt

    async def test_prompt_renders_idioms_by_meaning(self) -> None:
        """관용구를 직역하지 않도록 프롬프트가 고정한다 — 사전은 등록 항목만
        고치지만 이 규칙은 모든 관용구에 일반화된다(실기 2026-07-28:
        'plant the seed'가 '씨앗을 심어두고'로 직역)."""
        from apps.server.ai.gemini_live_translate import _translate_final_text

        calls: list = []
        await _translate_final_text(
            text_client(reply="일단 미리 주제로 던져 두고 싶었어요.", calls=calls),
            "Just wanted to plant the seed.",
        )
        assert "idioms" in calls[0]["contents"]
        assert "never" in calls[0]["contents"]
        assert "word-for-word" in calls[0]["contents"]
        # '의역으로 대체'까지 명시한다 — 직역 문장을 내놓고 괄호 해설을 덧붙이는
        # 반쪽 준수가 실측됨(2026-07-29 보고서: "빙글빙글 돌고 있네요.
        # (정신없이 바쁘네요.)").
        assert "never a literal rendering followed by a gloss" in calls[0]["contents"]

    async def test_prompt_keeps_quoted_dialogue_in_english(self) -> None:
        """작품 대사·가사 인용은 번역하지 않고 영어 원문으로 남긴다 — 리테이크
        노트에서 캐릭터 대사를 소리내 읽으면 화자의 말로 번역돼 뜻이 무너진다
        (실기 2026-07-29: "Cuz I got all the eternity" 3회 → "제가 영원함을
        모두 가지고 있으니까요"). 원문이 팀에게 그 대사를 특정할 단서다."""
        from apps.server.ai.gemini_live_translate import _translate_final_text

        calls: list = []
        await _translate_final_text(
            text_client(reply='카메라는 "I got all the eternity"에서 빠져야 해요.',
                        calls=calls),
            "The camera should pull out on cuz I got all the eternity.",
        )
        assert "dialogue or lyrics" in calls[0]["contents"]
        assert "original English" in calls[0]["contents"]

    async def test_prompt_bans_meta_commentary(self) -> None:
        """번역 모델의 혼잣말이 자막에 실리지 않게 금지한다 — "(프로젝트
        명칭이라면 그대로 Eternity로 표기하는 것이 좋음)" 같은 메타 주석이
        자막 줄로 나간 실측(2026-07-29 보고서)."""
        from apps.server.ai.gemini_live_translate import _translate_final_text

        calls: list = []
        await _translate_final_text(text_client(reply="안녕.", calls=calls), "Hi.")
        assert "no notes" in calls[0]["contents"]
        assert "parenthetical commentary" in calls[0]["contents"]


def _final_utt(en="Hello.", ko="라이브 번역."):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return TranslatedUtterance(
        seq=1, text_en=en, text_ko=ko, started_at=now, ended_at=now, is_final=True
    )


class TestApplyFinalTranslation:
    async def test_hook_off_keeps_original(self) -> None:
        provider = GeminiLiveTranslateProvider(api_key="k")
        utt = _final_utt()
        out = await provider._apply_final_translation(
            utt, text_client(reply="교정본.")
        )
        assert out is utt

    async def test_hook_on_replaces_ko_with_corrections(self) -> None:
        provider = GeminiLiveTranslateProvider(api_key="k")
        provider._final_translate = True
        out = await provider._apply_final_translation(
            _final_utt(), text_client(reply="연필 테스트 결과가 좋아요.")
        )
        # 교체 + 사후 교정(연필 테스트→펜슬 테스트)까지 적용
        assert out.text_ko == "펜슬 테스트 결과가 좋아요."
        assert out.is_final is True

    async def test_timeout_keeps_live_ko(self, monkeypatch) -> None:
        monkeypatch.setenv("GEMINI_FINAL_TRANSLATION_TIMEOUT_MS", "50")

        async def slow(*, model, contents, config):
            await asyncio.sleep(0.5)
            return SimpleNamespace(text="늦은 교정본.")

        client = SimpleNamespace(
            aio=SimpleNamespace(models=SimpleNamespace(generate_content=slow))
        )
        provider = GeminiLiveTranslateProvider(api_key="k")
        provider._final_translate = True
        utt = _final_utt()
        assert await provider._apply_final_translation(utt, client) is utt

    async def test_empty_en_keeps_live_ko(self) -> None:
        provider = GeminiLiveTranslateProvider(api_key="k")
        provider._final_translate = True
        utt = _final_utt(en="  ")
        assert await provider._apply_final_translation(
            utt, text_client(reply="교정본.")
        ) is utt

    async def test_stream_replaces_only_finals(self, monkeypatch) -> None:
        monkeypatch.setenv("GEMINI_LT_MIN_FINAL_CHARS", "2")
        session = FakeSession(
            [
                message(en=" Good morning."),
                message(ko=" 좋은 아침입니다."),
            ]
        )
        client = SimpleNamespace(
            aio=SimpleNamespace(
                live=FakeLive(session),
                models=text_client(reply="교정된 파이널.").aio.models,
            )
        )
        provider = GeminiLiveTranslateProvider(api_key="k", client=client)
        provider._final_translate = True
        utterances = [u async for u in provider.stream(_audio(), "en")]
        finals = [u for u in utterances if u.is_final]
        assert [u.text_ko for u in finals] == ["교정된 파이널."]
        assert all(u.text_ko != "교정된 파이널." for u in utterances if not u.is_final)
# === ANCHOR: TEST_GEMINI_LIVE_TRANSLATE_END ===
