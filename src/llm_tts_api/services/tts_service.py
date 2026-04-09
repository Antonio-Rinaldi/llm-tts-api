from __future__ import annotations

import io
import logging
import os
import tempfile
import threading
import wave
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
    split_text_semantic as split_text_semantic_pipeline,
)
from llm_tts_api.services.tts_providers.base import GenerationOptions, SynthesisRequest
from llm_tts_api.services.tts_providers.registry import TTSProviderRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedSpeechRequest:
    model_name: str
    provider: str
    voice_name: str
    voice: VoiceConfig
    response_format: str
    chunks: list[str]


def split_text_semantic(text: str, max_chars: int) -> list[str]:
    return split_text_semantic_pipeline(text, max_chars=max_chars, max_sentences_per_chunk=2)


def _concat_wav_bytes(parts: list[bytes]) -> bytes:
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


class TTSService:
    def __init__(self, settings: Settings, model_registry: ModelRegistry, provider_registry: TTSProviderRegistry) -> None:
        self.settings = settings
        self.model_registry = model_registry
        self.provider_registry = provider_registry
        max_inflight_raw = os.getenv("TTS_MAX_CONCURRENT_REQUESTS", "1").strip()
        try:
            self._max_concurrent_requests = max(1, int(max_inflight_raw))
        except ValueError as exc:
            raise ValueError("TTS_MAX_CONCURRENT_REQUESTS must be an integer >= 1") from exc
        self._synthesis_semaphore = threading.Semaphore(self._max_concurrent_requests)
        # Preload the default TTS model at startup for faster first request
        default_provider = self.settings.tts_provider
        preload_target = self.provider_registry.get(default_provider)
        preload_model = getattr(preload_target, "preload", None)
        if callable(preload_model):
            preload_model(self.settings.tts_model_default_for_provider(default_provider))

    @staticmethod
    def _cleanup_file(path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass

    def _resolve_speech_request(self, request: SpeechRequest) -> ResolvedSpeechRequest:
        if not request.input or not request.input.strip():
            raise invalid_request("input is required", param="input")

        try:
            model_name, provider = self.model_registry.resolve_tts_target(request.model, request.provider)
        except ValueError as exc:
            raise invalid_request(str(exc), param="provider") from exc

        if not self.model_registry.is_allowed_tts_model(model_name, provider):
            raise invalid_request(f"model '{model_name}' is not allowed", param="model")

        voice = self.settings.tts_voice_map.get(request.voice)
        if not voice:
            raise invalid_request(f"voice '{request.voice}' is not configured", param="voice")

        if voice.ref_audio_path and not Path(voice.ref_audio_path).exists():
            raise invalid_request(
                f"voice '{request.voice}' reference audio path does not exist",
                param="voice",
                code="voice_reference_missing",
            )

        response_format = (request.response_format or "wav").lower()
        if response_format != "wav":
            raise invalid_request("Only 'wav' response_format is currently supported", param="response_format")

        normalized_input = preprocess_for_tts(request.input, voice.number_lang or voice.language)
        chunks = split_text_semantic_pipeline(
            normalized_input,
            max_chars=self.settings.tts_max_input_chars,
            max_sentences_per_chunk=voice.max_sentences_per_chunk,
        )
        if not chunks:
            raise invalid_request("input is required", param="input")

        return ResolvedSpeechRequest(
            model_name=model_name,
            provider=provider,
            voice_name=request.voice,
            voice=voice,
            response_format=response_format,
            chunks=chunks,
        )

    def _synthesize_wav(self, resolved: ResolvedSpeechRequest) -> bytes:
        with self._synthesis_semaphore:
            provider_strategy = self.provider_registry.get(resolved.provider)
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
            normalize_wav_rms(chunk_wav, target_db=resolved.voice.target_db)
            for chunk_wav in chunk_wavs
        ]
        return _concat_wav_bytes(normalized_chunks)

    def _build_speech_response(self, wav_bytes: bytes, stream: bool) -> FileResponse | StreamingResponse:
        if stream:
            return StreamingResponse(io.BytesIO(wav_bytes), media_type="audio/wav")

        fd, out_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        Path(out_path).write_bytes(wav_bytes)
        return FileResponse(
            out_path,
            media_type="audio/wav",
            filename="speech.wav",
            background=BackgroundTask(self._cleanup_file, out_path),
        )

    def create_speech(self, request: SpeechRequest, stream: bool = False) -> FileResponse | StreamingResponse:
        """
        Generate speech audio for the given request.
        If stream=True, return StreamingResponse (audio from memory, no disk IO).
        Otherwise, return FileResponse (audio from temp file, auto cleanup).
        """
        resolved = self._resolve_speech_request(request)

        try:
            merged_wav = self._synthesize_wav(resolved)
            return self._build_speech_response(merged_wav, stream)
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


