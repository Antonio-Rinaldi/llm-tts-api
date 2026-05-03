from __future__ import annotations

import io
import logging
import os
import tempfile
import threading
import wave
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask

from llm_tts_api.config import Settings, VoiceConfig
from llm_tts_api.errors import OpenAIHTTPException, internal_error, invalid_request
from llm_tts_api.schemas.speech import SpeechRequest
from llm_tts_api.services.audio_postprocessing import normalize_wav_rms
from llm_tts_api.services.model_registry import ModelRegistry
from llm_tts_api.services.text_preprocessing import (
    preprocess_for_tts,
)
from llm_tts_api.services.text_preprocessing import (
    split_text_semantic as split_text_semantic_pipeline,
)
from llm_tts_api.services.tts_providers.base import GenerationOptions, SynthesisRequest
from llm_tts_api.services.tts_providers.registry import TTSProviderRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedSpeechRequest:
    """Normalized speech request ready for synthesis execution."""

    model_name: str
    provider: str
    voice_name: str
    voice: VoiceConfig
    response_format: str
    chunks: list[str]
    normalize_db: float


def split_text_semantic(text: str, max_chars: int) -> list[str]:
    """Backward-compatible semantic splitter using conservative sentence grouping."""
    return split_text_semantic_pipeline(text, max_chars=max_chars, max_sentences_per_chunk=2)


def _concat_wav_bytes(parts: list[bytes]) -> bytes:
    """Concatenate multiple WAV payloads preserving a single consistent header.

    Args:
        parts: Ordered list of valid WAV byte payloads.

    Returns:
        A single WAV payload containing all frames in sequence.

    Raises:
        OpenAIHTTPException: If WAV chunks are incompatible.
    """

    if not parts:
        return b""
    if len(parts) == 1:
        return parts[0]

    frames: list[bytes] = []
    params: tuple[int, int, int, str, str] | None = None

    for item in parts:
        with wave.open(io.BytesIO(item), "rb") as reader:
            current_params = (
                reader.getnchannels(),
                reader.getsampwidth(),
                reader.getframerate(),
                reader.getcomptype(),
                reader.getcompname(),
            )
            if params is None:
                params = current_params
            elif params != current_params:
                raise internal_error("Speech generation failed: incompatible WAV chunks")
            frames.append(reader.readframes(reader.getnframes()))

    if params is None:
        return b""

    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(params[0])
        writer.setsampwidth(params[1])
        writer.setframerate(params[2])
        writer.setcomptype(params[3], params[4])
        for frame in frames:
            writer.writeframes(frame)
    return out.getvalue()


class SpeechRequestResolver:
    """Validate incoming speech requests and produce normalized synthesis payloads."""

    def __init__(self, settings: Settings, model_registry: ModelRegistry) -> None:
        """Store dependencies used to validate and normalize requests."""
        self._settings = settings
        self._model_registry = model_registry

    def resolve(self, request: SpeechRequest) -> ResolvedSpeechRequest:
        """Validate request values, preprocess text, and split it into stable chunks."""
        self._ensure_input_present(request.input)
        model_name, provider = self._resolve_target(request)
        self._ensure_model_allowed(model_name, provider)
        voice = self._resolve_voice(request.voice)
        response_format = self._resolve_response_format(request.response_format)
        chunks = self._prepare_chunks(request.input, voice)
        normalize_db = request.normalize_db if request.normalize_db is not None else voice.target_db
        return ResolvedSpeechRequest(
            model_name=model_name,
            provider=provider,
            voice_name=request.voice,
            voice=voice,
            response_format=response_format,
            chunks=chunks,
            normalize_db=normalize_db,
        )

    @staticmethod
    def _ensure_input_present(text: str) -> None:
        """Reject empty or whitespace-only input strings."""
        if not text or not text.strip():
            raise invalid_request("input is required", param="input")

    def _resolve_target(self, request: SpeechRequest) -> tuple[str, str]:
        """Resolve provider and model from request fallback rules."""
        try:
            return self._model_registry.resolve_tts_target(request.model, request.provider)
        except ValueError as exc:
            raise invalid_request(str(exc), param="provider") from exc

    def _ensure_model_allowed(self, model_name: str, provider: str) -> None:
        """Ensure the selected model is present in the provider allow-list."""
        if not self._model_registry.is_allowed_tts_model(model_name, provider):
            raise invalid_request(f"model '{model_name}' is not allowed", param="model")

    def _resolve_voice(self, voice_name: str) -> VoiceConfig:
        """Resolve and validate a configured voice entry."""
        voice = self._settings.tts_voice_map.get(voice_name)
        if not voice:
            raise invalid_request(f"voice '{voice_name}' is not configured", param="voice")

        if voice.ref_audio_path and not Path(voice.ref_audio_path).exists():
            raise invalid_request(
                f"voice '{voice_name}' reference audio path does not exist",
                param="voice",
                code="voice_reference_missing",
            )
        return voice

    @staticmethod
    def _resolve_response_format(response_format: str | None) -> str:
        """Validate and normalize requested audio response format."""
        normalized_format = (response_format or "wav").lower()
        if normalized_format != "wav":
            raise invalid_request(
                "Only 'wav' response_format is currently supported",
                param="response_format",
            )
        return normalized_format

    def _prepare_chunks(self, input_text: str, voice: VoiceConfig) -> list[str]:
        """Apply text preprocessing and semantic chunking for stable synthesis."""
        normalized_input = preprocess_for_tts(input_text, voice.number_lang or voice.language)
        chunks = split_text_semantic_pipeline(
            normalized_input,
            max_chars=self._settings.tts_max_input_chars,
            max_sentences_per_chunk=voice.max_sentences_per_chunk,
        )
        if not chunks:
            raise invalid_request("input is required", param="input")
        return chunks


class SpeechSynthesizer:
    """Generate normalized WAV output from a resolved request."""

    def __init__(
        self, provider_registry: TTSProviderRegistry, max_concurrent_requests: int
    ) -> None:
        """Create a synthesis engine with bounded in-process concurrency."""
        self._provider_registry = provider_registry
        self._synthesis_semaphore = threading.Semaphore(max_concurrent_requests)

    def generate(self, resolved: ResolvedSpeechRequest) -> bytes:
        """Synthesize all chunks, normalize loudness, and concatenate final WAV bytes."""
        with self._synthesis_semaphore:
            provider_strategy = self._provider_registry.get(resolved.provider)
            chunk_wavs = provider_strategy.synthesize_chunks(
                SynthesisRequest(
                    model_name=resolved.model_name,
                    chunks=resolved.chunks,
                    voice=resolved.voice,
                    voice_name=resolved.voice_name,
                    response_format=resolved.response_format,
                    generation=GenerationOptions(
                        language=resolved.voice.language,
                        temperature=resolved.voice.temperature,
                        top_p=resolved.voice.top_p,
                    ),
                )
            )
        normalized_chunks = [
            normalize_wav_rms(chunk_wav, target_db=resolved.normalize_db)
            for chunk_wav in chunk_wavs
        ]
        return _concat_wav_bytes(normalized_chunks)


class SpeechResponseFactory:
    """Create HTTP responses for generated WAV payloads."""

    @staticmethod
    def cleanup_temp_file(path: str) -> None:
        """Remove a temp file and ignore deletion race conditions."""
        with suppress(OSError):
            os.remove(path)

    def build(self, wav_bytes: bytes, stream: bool) -> FileResponse | StreamingResponse:
        """Create either a streamed response or a temp-file-backed response."""
        if stream:
            return StreamingResponse(io.BytesIO(wav_bytes), media_type="audio/wav")

        fd, out_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        Path(out_path).write_bytes(wav_bytes)
        return FileResponse(
            out_path,
            media_type="audio/wav",
            filename="speech.wav",
            background=BackgroundTask(self.cleanup_temp_file, out_path),
        )


class TTSService:
    """Facade orchestrating request validation, synthesis, and HTTP response creation."""

    def __init__(
        self,
        settings: Settings,
        model_registry: ModelRegistry,
        provider_registry: TTSProviderRegistry,
    ) -> None:
        """Build and wire all internal speech pipeline components."""
        self.settings = settings
        self._resolver = SpeechRequestResolver(settings=settings, model_registry=model_registry)
        self._synthesizer = SpeechSynthesizer(
            provider_registry=provider_registry,
            max_concurrent_requests=settings.tts_max_concurrent_requests,
        )
        self._response_factory = SpeechResponseFactory()

        # Preload the default TTS model at startup for faster first request
        default_provider = settings.tts_provider
        preload_target = provider_registry.get(default_provider)
        preload_model = getattr(preload_target, "preload", None)
        if callable(preload_model):
            preload_model(settings.tts_model_default_for_provider(default_provider))

    def create_speech(
        self, request: SpeechRequest, stream: bool = False
    ) -> FileResponse | StreamingResponse:
        """Create speech for one request and return a stream or file response.

        Args:
            request: Incoming OpenAI-style speech payload.
            stream: If ``True``, return in-memory streamed audio.

        Returns:
            ``StreamingResponse`` or ``FileResponse`` containing WAV audio.
        """
        resolved = self._resolver.resolve(request)

        try:
            merged_wav = self._synthesizer.generate(resolved)
            return self._response_factory.build(merged_wav, stream)
        except OpenAIHTTPException:
            raise
        except Exception as exc:  # pragma: no cover
            logger.exception(
                "Speech generation failed | model=%s provider=%s voice=%s stream=%s error=%s",
                resolved.model_name,
                resolved.provider,
                resolved.voice_name,
                stream,
                exc,
            )
            raise internal_error(f"Speech generation failed: {exc}") from exc


