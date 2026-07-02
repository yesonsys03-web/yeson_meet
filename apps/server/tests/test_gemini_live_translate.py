# === ANCHOR: TEST_GEMINI_LIVE_TRANSLATE_START ===
from __future__ import annotations

from types import SimpleNamespace

from apps.server.ai.gemini_live_translate import (
    GeminiLiveTranslateProvider,
    TranscriptAssembler,
)


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
# === ANCHOR: TEST_GEMINI_LIVE_TRANSLATE_END ===
