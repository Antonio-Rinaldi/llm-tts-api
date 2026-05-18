"""Rich endpoint ``POST /v1/tts/synthesize`` (S-013).

This is the source of truth for synthesis (SRS §4.2 / FR-EP-01..04). It
consumes the producers from Sprints 1–3:

* :func:`llm_tts_api.dependencies.get_voice_metadata_repo` /
  :func:`get_voice_blob_repo` — S-022 + S-023 + S-024 + S-025 + S-011
  publish the voice via these repos.
* :func:`get_provider_selection` / :func:`get_tts_provider_registry` —
  S-006 auto-selection (or env override).
* ``app.state.queue_semaphore`` + ``concurrency_semaphore`` +
  ``model_locks`` — S-007 admission and per-(provider, model)
  serialization (sync provider calls are dispatched via
  ``anyio.to_thread.run_sync`` so the event loop stays responsive,
  NFR-PF-02).

Step 2 (S-015 streaming, S-016 cancellation) **extend** this handler.
The lifecycle hooks they will attach to are documented in the per-section
comments and the matching ``S-013-impl.md`` Service Interface section.
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
from typing import Annotated, Any

import anyio.to_thread
from fastapi import APIRouter, Depends, Request, Response
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from llm_tts_api.config import Settings, VoiceConfig
from llm_tts_api.dependencies import (
    get_device_profile,
    get_provider_selection,
    get_settings,
    get_tts_provider_registry,
    get_voice_blob_repo,
    get_voice_metadata_repo,
)
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

router = APIRouter(prefix="/v1/tts", tags=["synthesize"])


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
    """T4: pick provider + model from request overrides or auto-selection.

    Provider lookup goes through the registry so unknown overrides surface
    as ``validation_error.invalid_parameter`` (param=provider) via the
    registry's existing error. Model allow-list check uses the
    provider-specific helper on :class:`Settings` so the right list is
    consulted regardless of which provider auto-selection picked.
    """
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
    """T7: merge per-request overrides on top of the stored voice record."""
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
    """S-015.T2: ``TE`` header parsing per RFC 9110 §10.1.4.

    The header is a comma-separated list of transfer codings; ``trailers``
    in the list signals the client can accept response trailers. We treat
    any case-insensitive match in the list as a yes.
    """
    te_header = request.headers.get("te", "")
    if not te_header:
        return False
    tokens = [t.strip().split(";", 1)[0].lower() for t in te_header.split(",")]
    return "trailers" in tokens


class _TrailerStreamingResponse(StreamingResponse):
    """Streaming WAV response with optional end-of-stream trailers.

    Emits ``X-Chunks`` + ``X-Total-Duration-Ms`` as response trailers
    **only** when both:

    * the ASGI scope advertises ``extensions['http.response.trailers']``
      (uvicorn ≥0.24 declares this), and
    * the client previously set ``TE: trailers``.

    When either condition is missing the trailers are silently omitted
    per SRS §5 Resolution G-3 — we never fake the totals and we never
    block the stream to wait for chunk-count finality.
    """

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
    """S-015.T3: per-chunk async generator — yields WAV bytes as each chunk completes.

    Owns the teardown of the concurrency semaphore, the queue admission
    slot, and the temp file. The queue slot is assumed already acquired
    by the caller so a saturated queue can fail-fast with 429 before any
    response bytes are sent. S-016 (cancellation) will poll
    ``request.is_disconnected()`` between yields.
    """
    try:
        async with concur_sem:
            key = (provider_name, model_name)
            lock = model_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                model_locks[key] = lock
            async with lock:
                for chunk_text in chunks:
                    synthesis_req = SynthesisRequest(
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
    """T3: read the metadata record + blob bytes, mapping repo errors to envelopes."""
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


@router.post("/synthesize", response_model=None)
async def synthesize(
    payload: SynthesizeRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    provider_registry: Annotated[TTSProviderRegistry, Depends(get_tts_provider_registry)],
    provider_selection: Annotated[ProviderSelection, Depends(get_provider_selection)],
    device_profile: Annotated[DeviceProfile, Depends(get_device_profile)],
    metadata_repo: Annotated[VoiceMetadataRepository, Depends(get_voice_metadata_repo)],
    blob_repo: Annotated[VoiceBlobRepository, Depends(get_voice_blob_repo)],
) -> Response:
    """Rich-endpoint synthesis handler.

    Lifecycle hooks consumed by Step 2:

    * **S-015 (streaming)**: replaces the buffered :class:`Response`
      return with a :class:`fastapi.responses.StreamingResponse` whose
      async generator yields per-chunk WAV bytes between the
      ``concurrency_semaphore`` acquire and release. The response-start
      headers below are reused; ``X-Chunks`` + ``X-Total-Duration-Ms``
      move to trailers (or get omitted) per SRS §5 Resolution G-3.
    * **S-016 (cancellation)**: polls ``request.is_disconnected()`` at
      chunk boundaries inside the generator, stops further chunk
      synthesis, and relies on the ``finally`` blocks here to release
      the semaphores + delete the temp file. Cleanup is already correct
      because every acquire is paired with a ``finally`` release; S-016
      just adds the disconnect probe at the boundary.
    """
    # T1 + T8: explicit voice-required + input validation. ``voice`` is
    # optional at the Pydantic layer so this branch can emit the
    # dedicated ``validation_error.voice_required`` envelope.
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

    # T3: voice resolution before allocating temp resources.
    record, blob_bytes = await _resolve_voice(voice_id, metadata_repo, blob_repo)

    # T4: provider/model after voice resolution so a bad voice fails
    # faster than a provider/model lookup against the registry.
    provider_name, model_name, provider_strategy = _resolve_provider_and_model(
        payload, settings, provider_registry, provider_selection
    )

    # T3 (cont.): write blob to a per-request temp file. Cleaned in
    # ``finally`` even if synthesis raises (NFR-PV-01, FR-VS-10).
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f"voice-{voice_id}-", suffix=".wav", delete=False
        ) as tmpf:
            tmpf.write(blob_bytes)
            tmp_path = tmpf.name

        voice_config = _build_voice_config(record, payload, tmp_path)

        # T7 (cont.): chunking honours the per-request
        # ``max_sentences_per_chunk`` override via ``voice_config``.
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

        # S-015: streaming branch. Response-start headers carry the
        # FR-EP-04 inventory minus the two end-of-stream fields; the
        # generator owns tmp-file + semaphore teardown so we hand off
        # tmp_path ownership before returning. Queue admission is taken
        # here (not in the generator) so saturation fails fast as 429
        # before any response bytes are written.
        if payload.stream:
            queue_sem: asyncio.Semaphore = request.app.state.queue_semaphore
            if queue_sem.locked():
                raise capacity_error("queue_full", "Server is at capacity; queue is full")
            await queue_sem.acquire()
            owned_tmp_path = tmp_path
            tmp_path = None
            totals: dict[str, int] = {"chunks": 0, "duration_ms": 0}
            stream_headers: dict[str, str] = {
                "X-Request-ID": current_request_id(),
                "X-Provider": provider_name,
                "X-Model": model_name,
                "X-Device": device_profile.device,
                "X-Dtype": device_profile.dtype,
                "X-Voice-Source": str(record.source),
                "X-Voice-Id": record.id,
            }
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

        # T5: queue admission (non-blocking; overflow → 429), concurrency
        # cap, and per-(provider, model) lock around the sync provider
        # call. The sync call is dispatched via ``anyio.to_thread.run_sync``
        # so the event loop stays responsive (NFR-PF-02 / UAT-CC-02).
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

        # T6: full header inventory per SRS §5 Resolution C-2.
        # ``X-Voice-Source`` carries the record's provenance literal
        # (``seed`` for voice_map ingestion, ``crud`` for endpoint
        # uploads). The request-id middleware already sets X-Request-ID
        # on the response, but we mirror it here so the header is
        # present on the buffered body without depending on middleware
        # ordering.
        headers: dict[str, str] = {
            "X-Request-ID": current_request_id(),
            "X-Provider": provider_name,
            "X-Model": model_name,
            "X-Device": device_profile.device,
            "X-Dtype": device_profile.dtype,
            "X-Voice-Source": str(record.source),
            "X-Voice-Id": record.id,
            "X-Chunks": str(len(chunk_wavs)),
            "X-Total-Duration-Ms": str(duration_ms),
        }
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
    """Acquire admission/concurrency/model-lock and run the sync provider.

    Extracted from the handler so S-015 (streaming) can swap the buffered
    ``synthesize_chunks`` call for a per-chunk generator while reusing
    the same semaphore + lock discipline. S-016 (cancellation) probes
    ``request.is_disconnected()`` between chunk yields once that hook
    exists; the ``finally`` releases established here already trigger
    correctly on cancellation.
    """
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
                synthesis_req = SynthesisRequest(
                    model_name=model_name,
                    chunks=chunks,
                    voice=voice,
                    voice_name=voice_name,
                    response_format=response_format,
                    generation=GenerationOptions(
                        language=voice.language,
                        temperature=voice.temperature,
                        top_p=voice.top_p,
                    ),
                )
                result: list[bytes] = await anyio.to_thread.run_sync(
                    provider_strategy.synthesize_chunks, synthesis_req
                )
                return result
    finally:
        queue_sem.release()
