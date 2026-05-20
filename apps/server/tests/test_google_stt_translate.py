# === ANCHOR: TEST_GOOGLE_STT_TRANSLATE_START ===
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from apps.server.ai.google_stt_translate import (
    GoogleSttTranslateProvider,
    google_stt_translate_health,
)


class FakeSpeech:
    class RecognitionConfig:
        class AudioEncoding:
            LINEAR16 = "LINEAR16"

        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class StreamingRecognitionConfig:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class StreamingRecognizeRequest:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs


class FakeSpeechClient:
    def __init__(self) -> None:
        self.requests: list[object] = []

    def streaming_recognize(self, requests):
        self.requests.append(next(requests))
        self.requests.append(next(requests))
        yield _response("hello", is_final=False)
        yield _response("hello world", is_final=True)


class FakeTranslateClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def translate_text(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        source = kwargs["contents"][0]
        return SimpleNamespace(
            translations=[SimpleNamespace(translated_text=f"ko:{source}")],
        )


async def _audio():
    yield b"\x01" * 640


def _response(transcript: str, is_final: bool) -> object:
    return SimpleNamespace(
        results=[
            SimpleNamespace(
                alternatives=[SimpleNamespace(transcript=transcript)],
                is_final=is_final,
            )
        ]
    )


async def test_google_stt_translate_emits_partial_and_final() -> None:
    speech_client = FakeSpeechClient()
    translate_client = FakeTranslateClient()
    provider = GoogleSttTranslateProvider(
        speech_client=speech_client,
        translate_client=translate_client,
        project_id="test-project",
        speech_module=FakeSpeech,
    )

    utterances = [item async for item in provider.stream(_audio(), "en")]

    assert [item.text_en for item in utterances] == ["hello", "hello world"]
    assert [item.text_ko for item in utterances] == ["ko:hello", "ko:hello world"]
    assert [item.seq for item in utterances] == [1, 1]
    assert [item.is_final for item in utterances] == [False, True]
    assert speech_client.requests[0].kwargs["streaming_config"].kwargs["interim_results"] is True
    assert speech_client.requests[1].kwargs["audio_content"] == b"\x01" * 640
    assert translate_client.calls[0]["parent"] == "projects/test-project/locations/global"
    assert translate_client.calls[0]["target_language_code"] == "ko"


def test_google_stt_translate_health(monkeypatch) -> None:
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    assert google_stt_translate_health()["configured"] is False

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "yeson-test")
    health = google_stt_translate_health()

    assert health["configured"] is True
    assert health["project_id"] == "yeson-test"
# === ANCHOR: TEST_GOOGLE_STT_TRANSLATE_END ===
