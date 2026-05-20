# === ANCHOR: GOOGLE_STT_TRANSLATE_START ===
"""Google Cloud Speech interim STT + Cloud Translation provider."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
import html
import json
import logging
import os
import queue
from typing import Any, Protocol

from apps.server.ai.providers import TranslatedUtterance

INPUT_SAMPLE_RATE = 16000
PROVIDER_ENV = "YESON_AI_PROVIDER"
GOOGLE_STT_PROJECT_ENV = "GOOGLE_CLOUD_PROJECT"
GOOGLE_CREDENTIALS_JSON_ENV = "GOOGLE_APPLICATION_CREDENTIALS_JSON"
GOOGLE_STT_LANGUAGE_ENV = "GOOGLE_STT_LANGUAGE_CODE"
GOOGLE_TRANSLATE_TARGET_ENV = "GOOGLE_TRANSLATE_TARGET_LANGUAGE"
GOOGLE_TRANSLATE_LOCATION_ENV = "GOOGLE_TRANSLATE_LOCATION"
DEFAULT_SOURCE_LANGUAGE = "en-US"
DEFAULT_TARGET_LANGUAGE = "ko"
DEFAULT_LOCATION = "global"
logger = logging.getLogger(__name__)


class SpeechClientLike(Protocol):
    def streaming_recognize(self, requests: Iterable[Any]) -> Iterable[Any]: ...


class TranslateClientLike(Protocol):
    def translate_text(self, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class GoogleSttTranslateHealth:
    configured: bool
    status: str
    project_id: str | None
    source_language: str
    target_language: str


def google_stt_translate_health() -> dict[str, object]:
    project_id = os.environ.get(GOOGLE_STT_PROJECT_ENV) or _project_id_from_json_env()
    return {
        "configured": bool(project_id),
        "status": "configured" if project_id else "missing_google_cloud_project",
        "project_id": project_id,
        "source_language": os.environ.get(GOOGLE_STT_LANGUAGE_ENV, DEFAULT_SOURCE_LANGUAGE),
        "target_language": os.environ.get(GOOGLE_TRANSLATE_TARGET_ENV, DEFAULT_TARGET_LANGUAGE),
    }


class GoogleSttTranslateProvider:
    """Emit fast partial Korean subtitles from Speech interim transcripts."""

    def __init__(
        self,
        speech_client: SpeechClientLike | None = None,
        translate_client: TranslateClientLike | None = None,
        project_id: str | None = None,
        source_language: str | None = None,
        target_language: str | None = None,
        location: str | None = None,
        speech_module: Any | None = None,
    ) -> None:
        self._speech_client = speech_client
        self._translate_client = translate_client
        self._project_id = project_id or os.environ.get(GOOGLE_STT_PROJECT_ENV)
        self._source_language = source_language or os.environ.get(
            GOOGLE_STT_LANGUAGE_ENV,
            DEFAULT_SOURCE_LANGUAGE,
        )
        self._target_language = target_language or os.environ.get(
            GOOGLE_TRANSLATE_TARGET_ENV,
            DEFAULT_TARGET_LANGUAGE,
        )
        self._location = location or os.environ.get(
            GOOGLE_TRANSLATE_LOCATION_ENV,
            DEFAULT_LOCATION,
        )
        self._speech_module = speech_module

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        lang_hint: str,
    ) -> AsyncIterator[TranslatedUtterance]:
        if self._project_id is None:
            self._project_id = _project_id_from_json_env()
        if not self._project_id:
            raise RuntimeError(
                f"{GOOGLE_STT_PROJECT_ENV} or {GOOGLE_CREDENTIALS_JSON_ENV} is required"
            )

        speech_module = self._speech_module
        speech_client = self._speech_client
        translate_client = self._translate_client
        if speech_module is None or speech_client is None:
            from google.cloud import speech

            credentials, credentials_project_id = _credentials_from_json_env()
            if self._project_id is None:
                self._project_id = credentials_project_id
            speech_module = speech
            speech_client = speech.SpeechClient(credentials=credentials)
        if translate_client is None:
            from google.cloud import translate_v3

            credentials, credentials_project_id = _credentials_from_json_env()
            if self._project_id is None:
                self._project_id = credentials_project_id
            translate_client = translate_v3.TranslationServiceClient(credentials=credentials)

        loop = asyncio.get_running_loop()
        audio_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=2000)
        output_queue: asyncio.Queue[TranslatedUtterance | BaseException | None] = asyncio.Queue()

        producer = asyncio.create_task(_enqueue_audio(audio, audio_queue))
        worker = asyncio.to_thread(
            self._run_streaming_recognition,
            speech_module,
            speech_client,
            translate_client,
            audio_queue,
            output_queue,
            loop,
        )
        worker_task = asyncio.create_task(worker)
        try:
            while True:
                item = await output_queue.get()
                if item is None:
                    return
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            producer.cancel()
            await asyncio.to_thread(audio_queue.put, None)
            worker_task.cancel()

    def _run_streaming_recognition(
        self,
        speech: Any,
        speech_client: SpeechClientLike,
        translate_client: TranslateClientLike,
        audio_queue: queue.Queue[bytes | None],
        output_queue: asyncio.Queue[TranslatedUtterance | BaseException | None],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        try:
            requests = self._speech_requests(speech, audio_queue)
            seq = 1
            turn_started_at = datetime.now(timezone.utc)
            last_transcript_by_seq: dict[int, str] = {}
            for response in speech_client.streaming_recognize(requests):
                for result in getattr(response, "results", []) or []:
                    alternatives = getattr(result, "alternatives", []) or []
                    if not alternatives:
                        continue
                    transcript = (getattr(alternatives[0], "transcript", "") or "").strip()
                    if not transcript or transcript == last_transcript_by_seq.get(seq):
                        continue
                    last_transcript_by_seq[seq] = transcript
                    ended_at = datetime.now(timezone.utc)
                    translated = self._translate(translate_client, transcript)
                    utterance = TranslatedUtterance(
                        seq=seq,
                        text_en=transcript,
                        text_ko=translated,
                        started_at=turn_started_at,
                        ended_at=ended_at,
                        is_final=bool(getattr(result, "is_final", False)),
                    )
                    loop.call_soon_threadsafe(output_queue.put_nowait, utterance)
                    if utterance.is_final:
                        seq += 1
                        turn_started_at = ended_at
            loop.call_soon_threadsafe(output_queue.put_nowait, None)
        except BaseException as exc:
            logger.exception("Google STT/Translate provider disconnected")
            loop.call_soon_threadsafe(output_queue.put_nowait, exc)

    def _speech_requests(
        self,
        speech: Any,
        audio_queue: queue.Queue[bytes | None],
    ) -> Iterable[Any]:
        recognition_config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=INPUT_SAMPLE_RATE,
            language_code=self._source_language,
            enable_automatic_punctuation=True,
        )
        streaming_config = speech.StreamingRecognitionConfig(
            config=recognition_config,
            interim_results=True,
            single_utterance=False,
        )
        yield speech.StreamingRecognizeRequest(streaming_config=streaming_config)
        while True:
            chunk = audio_queue.get()
            if chunk is None:
                return
            yield speech.StreamingRecognizeRequest(audio_content=chunk)

    def _translate(self, translate_client: TranslateClientLike, text: str) -> str:
        parent = f"projects/{self._project_id}/locations/{self._location}"
        response = translate_client.translate_text(
            contents=[text],
            parent=parent,
            mime_type="text/plain",
            source_language_code=self._source_language,
            target_language_code=self._target_language,
        )
        translations = getattr(response, "translations", []) or []
        if not translations:
            return ""
        translated = getattr(translations[0], "translated_text", "") or ""
        return html.unescape(translated)


async def _enqueue_audio(
    audio: AsyncIterator[bytes],
    audio_queue: queue.Queue[bytes | None],
) -> None:
    try:
        async for chunk in audio:
            await asyncio.to_thread(audio_queue.put, chunk)
    finally:
        await asyncio.to_thread(audio_queue.put, None)


def _credentials_from_json_env() -> tuple[Any | None, str | None]:
    raw_json = os.environ.get(GOOGLE_CREDENTIALS_JSON_ENV)
    if not raw_json:
        return None, None
    from google.oauth2 import service_account

    info = json.loads(raw_json)
    credentials = service_account.Credentials.from_service_account_info(info)
    return credentials, info.get("project_id")


def _project_id_from_json_env() -> str | None:
    raw_json = os.environ.get(GOOGLE_CREDENTIALS_JSON_ENV)
    if not raw_json:
        return None
    try:
        info = json.loads(raw_json)
    except json.JSONDecodeError:
        return None
    project_id = info.get("project_id")
    return project_id if isinstance(project_id, str) else None
# === ANCHOR: GOOGLE_STT_TRANSLATE_END ===
