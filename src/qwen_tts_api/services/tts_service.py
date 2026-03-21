from __future__ import annotations

import io
import os
import re
import tempfile
import wave
from pathlib import Path

import soundfile as sf
import torch
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from qwen_tts_api.config import Settings
from qwen_tts_api.errors import internal_error, invalid_request
from qwen_tts_api.schemas.speech import SpeechRequest
from qwen_tts_api.services.model_registry import ModelRegistry

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
    def __init__(self, settings: Settings, model_registry: ModelRegistry) -> None:
        self.settings = settings
        self.model_registry = model_registry
        self._model_cache: dict[str, object] = {}

    def _get_model(self, model_name: str):
        if model_name in self._model_cache:
            return self._model_cache[model_name]

        from qwen_tts import Qwen3TTSModel

        load_kwargs: dict[str, object] = {}
        if torch.backends.mps.is_available():
            load_kwargs["device_map"] = "mps"
            load_kwargs["dtype"] = torch.float32

        model = Qwen3TTSModel.from_pretrained(model_name, **load_kwargs)
        self._model_cache[model_name] = model
        return model

    @staticmethod
    def _cleanup_file(path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass

    def create_speech(self, request: SpeechRequest) -> FileResponse:
        if not request.input or not request.input.strip():
            raise invalid_request("input is required", param="input")

        model_name = self.model_registry.resolve_tts_model(request.model)
        if not self.model_registry.is_allowed_tts_model(model_name):
            raise invalid_request(f"model '{model_name}' is not allowed", param="model")

        voice = self.settings.qwen_tts_voice_map.get(request.voice)
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

        chunks = split_text_semantic(request.input, self.settings.qwen_tts_max_input_chars)
        if not chunks:
            raise invalid_request("input is required", param="input")

        try:
            model = self._get_model(model_name)
            chunk_wavs: list[bytes] = []

            for chunk in chunks:
                wavs, sr = model.generate_voice_clone(
                    text=chunk,
                    language=voice.language,
                    ref_audio=voice.ref_audio_path,
                    ref_text=voice.ref_text,
                )
                buf = io.BytesIO()
                sf.write(buf, wavs[0], sr, format="WAV")
                chunk_wavs.append(buf.getvalue())

            merged_wav = _concat_wav_bytes(chunk_wavs)

            fd, out_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            Path(out_path).write_bytes(merged_wav)

            return FileResponse(
                out_path,
                media_type="audio/wav",
                filename="speech.wav",
                background=BackgroundTask(self._cleanup_file, out_path),
            )
        except Exception as exc:  # pragma: no cover
            raise internal_error(f"Speech generation failed: {exc}") from exc
