from __future__ import annotations

import io
import os
import re
import tempfile
import wave
from pathlib import Path

from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask

from llm_tts_api.config import Settings
from llm_tts_api.errors import OpenAIHTTPException, internal_error, invalid_request
from llm_tts_api.schemas.speech import SpeechRequest
from llm_tts_api.services.model_registry import ModelRegistry
from llm_tts_api.services.tts_providers.base import SynthesisRequest
from llm_tts_api.services.tts_providers.registry import TTSProviderRegistry

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")


def split_text_semantic(text: str, max_chars: int) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n{2,}", cleaned) if p.strip()]
    chunks: list[str] = []
    current = ""

    def flush_current() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    def append_part(part: str) -> None:
        nonlocal current
        part = part.strip()
        if not part:
            return

        candidate = f"{current}\n\n{part}" if current else part
        if len(candidate) <= max_chars:
            current = candidate
            return

        flush_current()

        if len(part) <= max_chars:
            current = part
            return

        sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(part) if s.strip()]
        if len(sentences) <= 1:
            for i in range(0, len(part), max_chars):
                slice_part = part[i : i + max_chars].strip()
                if slice_part:
                    chunks.append(slice_part)
            return

        sentence_acc = ""
        for sentence in sentences:
            sentence_candidate = f"{sentence_acc} {sentence}".strip() if sentence_acc else sentence
            if len(sentence_candidate) <= max_chars:
                sentence_acc = sentence_candidate
                continue

            if sentence_acc:
                chunks.append(sentence_acc.strip())
                sentence_acc = ""

            if len(sentence) <= max_chars:
                sentence_acc = sentence
            else:
                for i in range(0, len(sentence), max_chars):
                    slice_part = sentence[i : i + max_chars].strip()
                    if slice_part:
                        chunks.append(slice_part)

        if sentence_acc.strip():
            chunks.append(sentence_acc.strip())

    for paragraph in paragraphs:
        append_part(paragraph)

    flush_current()
    return chunks


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
        # Preload the default TTS model at startup for faster first request
        default_provider = self.model_registry.infer_tts_provider(self.settings.tts_model_default)
        preload_target = self.provider_registry.get(default_provider)
        preload_model = getattr(preload_target, "preload", None)
        if callable(preload_model):
            preload_model(self.settings.tts_model_default)

    @staticmethod
    def _cleanup_file(path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass

    def create_speech(self, request: SpeechRequest, stream: bool = False) -> FileResponse:
        """
        Generate speech audio for the given request.
        If stream=True, return StreamingResponse (audio from memory, no disk IO).
        Otherwise, return FileResponse (audio from temp file, auto cleanup).
        """
        if not request.input or not request.input.strip():
            raise invalid_request("input is required", param="input")

        try:
            model_name, provider = self.model_registry.resolve_tts_target(request.model, request.provider)
        except ValueError as exc:
            raise invalid_request(str(exc), param="provider") from exc

        if not self.model_registry.is_allowed_tts_model(model_name):
            raise invalid_request(f"model '{model_name}' is not allowed", param="model")

        voice = self.settings.tts_voice_map.get(request.voice)
        if not voice:
            raise invalid_request(f"voice '{request.voice}' is not configured", param="voice")

        if not Path(voice.ref_audio_path).exists():
            raise invalid_request(
                f"voice '{request.voice}' reference audio path does not exist",
                param="voice",
                code="voice_reference_missing",
            )

        requested_format = (request.response_format or "wav").lower()
        if requested_format != "wav":
            raise invalid_request("Only 'wav' response_format is currently supported", param="response_format")

        chunks = split_text_semantic(request.input, self.settings.tts_max_input_chars)
        if not chunks:
            raise invalid_request("input is required", param="input")

        try:
            provider_strategy = self.provider_registry.get(provider)
            chunk_wavs = provider_strategy.synthesize_chunks(
                SynthesisRequest(
                    model_name=model_name,
                    chunks=chunks,
                    voice=voice,
                    response_format=requested_format,
                )
            )

            merged_wav = _concat_wav_bytes(chunk_wavs)

            if stream:
                return StreamingResponse(io.BytesIO(merged_wav), media_type="audio/wav")

            fd, out_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            Path(out_path).write_bytes(merged_wav)

            return FileResponse(
                out_path,
                media_type="audio/wav",
                filename="speech.wav",
                background=BackgroundTask(self._cleanup_file, out_path),
            )
        except OpenAIHTTPException:
            raise
        except Exception as exc:  # pragma: no cover
            raise internal_error(f"Speech generation failed: {exc}") from exc

# To run with multiple workers for concurrency, use:
# uvicorn llm_tts_api.main:app --host 0.0.0.0 --port 8000 --workers 4

