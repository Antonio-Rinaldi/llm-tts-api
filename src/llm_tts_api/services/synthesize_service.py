"""Shared service-layer entry point for rich + OpenAI-adapter synthesis (S-017).

The rich endpoint ``POST /v1/tts/synthesize`` and the OpenAI-adapter
``POST /v1/audio/speech`` both funnel through :func:`synthesize_core` so
there is exactly one synthesis pipeline (BR-9). The router handlers stay
thin: they resolve their FastAPI ``Depends`` graph and pass it as plain
arguments. No HTTP indirection between the two handlers.

The audio-adapter router (:mod:`llm_tts_api.routers.audio`) imports
:func:`synthesize_core` only — never :class:`SpeechSynthesizer` or
:mod:`llm_tts_api.routers.synthesize`. UAT-OA-03 (a static check in
``tests/test_openai_adapter.py``) pins that constraint.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import tempfile
import wave
from collections.abc import AsyncIterator, Mapping, MutableMapping
from contextlib import suppress
from typing import Any

import anyio.to_thread
from fastapi import Request, Response
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from llm_tts_api.config import Settings, VoiceConfig
from llm_tts_api.engine import DeviceProfile
from llm_tts_api.errors import (
    OpenAIHTTPException,
    capacity_error,
    internal_error,
    invalid_request,
    voice_error,
)
from llm_tts_api.observability import current_request_id
from llm_tts_api.schemas.synthesis import SynthesizeRequest
from llm_tts_api.services.audio_postprocessing import normalize_wav_rms
from llm_tts_api.services.text_preprocessing import (
    preprocess_for_tts,
    split_text_semantic,
)
from llm_tts_api.services.tts_providers.auto_select import ProviderSelection
from llm_tts_api.services.tts_providers.base import (
    GenerationOptions,
    SynthesisRequest,
    TTSProviderStrategy,
)
from llm_tts_api.services.tts_providers.registry import TTSProviderRegistry
from llm_tts_api.services.tts_service import _concat_wav_bytes
from llm_tts_api.services.voice_store import (
    VoiceBlobRepository,
    VoiceIdInvalidError,
    VoiceMetadataRepository,
    VoiceNotFoundError,
    VoiceRecord,
)

logger = logging.getLogger(__name__)


def _build_synthesis_request(
    *,
    model_name: str,
    chunk_text: str,
    voice: VoiceConfig,
    voice_name: str,
    response_format: str,
) -> SynthesisRequest:
    """Construct one per-chunk :class:`SynthesisRequest` (consolidates two call sites)."""
    return SynthesisRequest(
        model_name=model_name,
        chunks=[chunk_text],
        voice=voice,
        voice_name=voice_name,
        response_format=response_format,
        generation=GenerationOptions(
            language=voice.language,
            temperature=voice.temperature,
            top_p=voice.top_p,
        ),
    )


def _synthesis_headers(
    *,
    provider_name: str,
    model_name: str,
    device_profile: DeviceProfile,
    record: VoiceRecord,
    chunks: int | None = None,
    duration_ms: int | None = None,
) -> dict[str, str]:
    """FR-EP-04 response headers; ``chunks`` / ``duration_ms`` omitted on the stream path."""
    headers: dict[str, str] = {
        "X-Request-ID": current_request_id(),
        "X-Provider": provider_name,
        "X-Model": model_name,
        "X-Device": device_profile.device,
        "X-Dtype": device_profile.dtype,
        "X-Voice-Source": str(record.source),
        "X-Voice-Id": record.id,
    }
    if chunks is not None:
        headers["X-Chunks"] = str(chunks)
    if duration_ms is not None:
        headers["X-Total-Duration-Ms"] = str(duration_ms)
    return headers


def _wav_duration_ms(wav_bytes: bytes) -> int:
    """Return WAV duration in milliseconds, or 0 if the payload is empty/invalid."""
    if not wav_bytes:
        return 0
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
            frames = reader.getnframes()
            rate = reader.getframerate()
            if rate <= 0:
                return 0
            return int(round((frames / rate) * 1000))
    except wave.Error:
        return 0


def _resolve_provider_and_model(
    payload: SynthesizeRequest,
    settings: Settings,
    provider_registry: TTSProviderRegistry,
    provider_selection: ProviderSelection,
) -> tuple[str, str, TTSProviderStrategy]:
    """Pick provider + model from request overrides or auto-selection."""
    provider_name = (payload.provider or "").strip() or provider_selection.provider_name
    provider_strategy = provider_registry.get(provider_name)

    model_name = (payload.model or "").strip() or settings.tts_model_default_for_provider(
        provider_name
    )
    allowed_models = settings.tts_model_allowed_for_provider(provider_name)
    if allowed_models and model_name not in allowed_models:
        raise invalid_request(
            f"model {model_name!r} is not allowed for provider {provider_name!r}",
            param="model",
            code="unknown_model",
        )
    return provider_name, model_name, provider_strategy


def _build_voice_config(
    record: VoiceRecord,
    payload: SynthesizeRequest,
    tmp_path: str,
) -> VoiceConfig:
    """Merge per-request overrides on top of the stored voice record."""
    language = (payload.language or record.language).strip() or record.language
    number_lang = payload.number_lang if payload.number_lang is not None else record.number_lang
    temperature = payload.temperature if payload.temperature is not None else record.temperature
    top_p = payload.top_p if payload.top_p is not None else record.top_p
    target_db = payload.normalize_db if payload.normalize_db is not None else record.target_db
    max_sentences = (
        payload.max_sentences_per_chunk
        if payload.max_sentences_per_chunk is not None
        else record.max_sentences_per_chunk
    )
    return VoiceConfig(
        ref_audio_path=tmp_path,
        ref_text=record.transcript,
        language=language,
        number_lang=number_lang,
        temperature=temperature,
        top_p=top_p,
        target_db=target_db,
        max_sentences_per_chunk=max_sentences,
    )


def _client_advertises_trailers(request: Request) -> bool:
    """RFC 9110 §10.1.4 TE header parsing — does the client accept trailers?"""
    te_header = request.headers.get("te", "")
    if not te_header:
        return False
    tokens = [t.strip().split(";", 1)[0].lower() for t in te_header.split(",")]
    return "trailers" in tokens


class _TrailerStreamingResponse(StreamingResponse):
    """Streaming WAV response with optional end-of-stream trailers (S-015)."""

    def __init__(
        self,
        content: AsyncIterator[bytes],
        *,
        headers: Mapping[str, str],
        totals: dict[str, int],
        te_trailers: bool,
    ) -> None:
        super().__init__(
            content=content, status_code=200, headers=dict(headers), media_type="audio/wav"
        )
        self._totals = totals
        self._te_trailers = te_trailers

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        extensions = scope.get("extensions") or {}
        supports_trailers = self._te_trailers and "http.response.trailers" in extensions

        async def _send_with_trailer_flag(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start" and supports_trailers:
                message["trailers"] = True
            await send(message)

        await super().__call__(scope, receive, _send_with_trailer_flag)

        if supports_trailers:
            await send(
                {
                    "type": "http.response.trailers",
                    "headers": [
                        (b"x-chunks", str(self._totals.get("chunks", 0)).encode()),
                        (
                            b"x-total-duration-ms",
                            str(self._totals.get("duration_ms", 0)).encode(),
                        ),
                    ],
                    "more_trailers": False,
                }
            )


async def _stream_synthesis_chunks(
    *,
    provider_strategy: TTSProviderStrategy,
    provider_name: str,
    model_name: str,
    chunks: list[str],
    voice: VoiceConfig,
    voice_name: str,
    response_format: str,
    target_db: float,
    concur_sem: asyncio.Semaphore,
    queue_sem: asyncio.Semaphore,
    model_locks: dict[tuple[str, str], asyncio.Lock],
    tmp_path: str,
    totals: dict[str, int],
) -> AsyncIterator[bytes]:
    """Per-chunk async generator — yields WAV bytes as each chunk completes."""
    try:
        async with concur_sem:
            key = (provider_name, model_name)
            lock = model_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                model_locks[key] = lock
            async with lock:
                for chunk_text in chunks:
                    synthesis_req = _build_synthesis_request(
                        model_name=model_name,
                        chunk_text=chunk_text,
                        voice=voice,
                        voice_name=voice_name,
                        response_format=response_format,
                    )
                    result: list[bytes] = await anyio.to_thread.run_sync(
                        provider_strategy.synthesize_chunks, synthesis_req
                    )
                    chunk_wav = normalize_wav_rms(result[0], target_db=target_db)
                    totals["chunks"] += 1
                    totals["duration_ms"] += _wav_duration_ms(chunk_wav)
                    yield chunk_wav
    finally:
        queue_sem.release()
        with suppress(OSError):
            os.remove(tmp_path)


async def _resolve_voice(
    voice_id: str,
    metadata_repo: VoiceMetadataRepository,
    blob_repo: VoiceBlobRepository,
) -> tuple[VoiceRecord, bytes]:
    """Read the metadata record + blob bytes, mapping repo errors to envelopes."""
    try:
        record = await metadata_repo.get(voice_id)
    except VoiceNotFoundError as exc:
        raise voice_error(
            "voice_not_found",
            f"voice {voice_id!r} not found in voice store",
            status_code=404,
            param="voice",
        ) from exc
    except VoiceIdInvalidError as exc:
        raise invalid_request(str(exc), param="voice") from exc

    try:
        blob_bytes = await blob_repo.get(voice_id)
    except VoiceNotFoundError as exc:
        raise voice_error(
            "voice_blob_missing",
            f"voice {voice_id!r} metadata exists but blob is missing",
            status_code=422,
            param="voice",
        ) from exc
    return record, blob_bytes


async def _run_synthesis(
    *,
    request: Request,
    provider_strategy: TTSProviderStrategy,
    provider_name: str,
    model_name: str,
    chunks: list[str],
    voice: VoiceConfig,
    voice_name: str,
    response_format: str,
) -> list[bytes]:
    """Acquire admission/concurrency/model-lock and run the sync provider."""
    queue_sem: asyncio.Semaphore = request.app.state.queue_semaphore
    concur_sem: asyncio.Semaphore = request.app.state.concurrency_semaphore
    model_locks: dict[tuple[str, str], asyncio.Lock] = request.app.state.model_locks

    if queue_sem.locked():
        raise capacity_error("queue_full", "Server is at capacity; queue is full")
    await queue_sem.acquire()
    try:
        async with concur_sem:
            key = (provider_name, model_name)
            lock = model_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                model_locks[key] = lock
            async with lock:
                outputs: list[bytes] = []
                for index, chunk_text in enumerate(chunks):
                    if await request.is_disconnected():
                        logger.info(
                            (
                                "synthesize cancelled by client disconnect | "
                                "provider=%s model=%s voice=%s chunks_done=%d/%d"
                            ),
                            provider_name,
                            model_name,
                            voice_name,
                            index,
                            len(chunks),
                        )
                        raise asyncio.CancelledError("client disconnected")
                    synthesis_req = _build_synthesis_request(
                        model_name=model_name,
                        chunk_text=chunk_text,
                        voice=voice,
                        voice_name=voice_name,
                        response_format=response_format,
                    )
                    chunk_results = await anyio.to_thread.run_sync(
                        provider_strategy.synthesize_chunks, synthesis_req
                    )
                    outputs.extend(chunk_results)
                return outputs
    finally:
        queue_sem.release()


async def synthesize_core(
    payload: SynthesizeRequest,
    *,
    request: Request,
    settings: Settings,
    provider_registry: TTSProviderRegistry,
    provider_selection: ProviderSelection,
    device_profile: DeviceProfile,
    metadata_repo: VoiceMetadataRepository,
    blob_repo: VoiceBlobRepository,
) -> Response:
    """Shared synthesis entry point (S-017).

    Identical pipeline for both ``POST /v1/tts/synthesize`` (rich endpoint)
    and ``POST /v1/audio/speech`` (OpenAI adapter). Returns a buffered
    :class:`Response` when ``payload.stream`` is False, otherwise a
    :class:`_TrailerStreamingResponse`. Rich-endpoint metadata headers
    (FR-EP-04 inventory) are always populated; the OpenAI adapter strips
    them at the boundary to keep its response shape OpenAI-identical.
    """
    if not payload.voice or not payload.voice.strip():
        raise invalid_request("voice is required", param="voice", code="voice_required")
    voice_id = payload.voice.strip()

    input_text = payload.input
    if not input_text or not input_text.strip():
        raise invalid_request("input is required", param="input")
    if len(input_text) > settings.tts_max_input_chars:
        raise invalid_request(
            (
                f"input length {len(input_text)} exceeds "
                f"TTS_MAX_INPUT_CHARS={settings.tts_max_input_chars}"
            ),
            param="input",
            code="input_too_long",
        )

    record, blob_bytes = await _resolve_voice(voice_id, metadata_repo, blob_repo)

    provider_name, model_name, provider_strategy = _resolve_provider_and_model(
        payload, settings, provider_registry, provider_selection
    )

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f"voice-{voice_id}-", suffix=".wav", delete=False
        ) as tmpf:
            tmpf.write(blob_bytes)
            tmp_path = tmpf.name

        voice_config = _build_voice_config(record, payload, tmp_path)

        normalized_input = preprocess_for_tts(
            input_text, voice_config.number_lang or voice_config.language
        )
        chunks = split_text_semantic(
            normalized_input,
            max_chars=settings.tts_max_input_chars,
            max_sentences_per_chunk=voice_config.max_sentences_per_chunk,
        )
        if not chunks:
            raise invalid_request("input is required", param="input")

        if payload.stream:
            queue_sem: asyncio.Semaphore = request.app.state.queue_semaphore
            if queue_sem.locked():
                raise capacity_error("queue_full", "Server is at capacity; queue is full")
            await queue_sem.acquire()
            owned_tmp_path = tmp_path
            tmp_path = None
            totals: dict[str, int] = {"chunks": 0, "duration_ms": 0}
            stream_headers = _synthesis_headers(
                provider_name=provider_name,
                model_name=model_name,
                device_profile=device_profile,
                record=record,
            )
            generator = _stream_synthesis_chunks(
                provider_strategy=provider_strategy,
                provider_name=provider_name,
                model_name=model_name,
                chunks=chunks,
                voice=voice_config,
                voice_name=voice_id,
                response_format=payload.response_format,
                target_db=voice_config.target_db,
                concur_sem=request.app.state.concurrency_semaphore,
                queue_sem=queue_sem,
                model_locks=request.app.state.model_locks,
                tmp_path=owned_tmp_path,
                totals=totals,
            )
            return _TrailerStreamingResponse(
                generator,
                headers=stream_headers,
                totals=totals,
                te_trailers=_client_advertises_trailers(request),
            )

        chunk_wavs = await _run_synthesis(
            request=request,
            provider_strategy=provider_strategy,
            provider_name=provider_name,
            model_name=model_name,
            chunks=chunks,
            voice=voice_config,
            voice_name=voice_id,
            response_format=payload.response_format,
        )

        normalized_chunks = [
            normalize_wav_rms(chunk_wav, target_db=voice_config.target_db)
            for chunk_wav in chunk_wavs
        ]
        merged = _concat_wav_bytes(normalized_chunks)
        duration_ms = _wav_duration_ms(merged)

        headers = _synthesis_headers(
            provider_name=provider_name,
            model_name=model_name,
            device_profile=device_profile,
            record=record,
            chunks=len(chunk_wavs),
            duration_ms=duration_ms,
        )
        return Response(content=merged, media_type="audio/wav", headers=headers)

    except OpenAIHTTPException:
        raise
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception(
            "synthesize failed | provider=%s model=%s voice=%s err=%s",
            provider_name,
            model_name,
            voice_id,
            exc,
        )
        raise internal_error(f"Speech generation failed: {exc}") from exc
    finally:
        if tmp_path is not None:
            with suppress(OSError):
                os.remove(tmp_path)
