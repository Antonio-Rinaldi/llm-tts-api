from __future__ import annotations

import os
import tempfile
from pathlib import Path

import soundfile as sf
import torch
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from qwen_tts_api.config import Settings
from qwen_tts_api.errors import internal_error, invalid_request
from qwen_tts_api.schemas.speech import SpeechRequest
from qwen_tts_api.services.model_registry import ModelRegistry


class TTSService:
    def __init__(self, settings: Settings, model_registry: ModelRegistry) -> None:
        self.settings = settings
        self.model_registry = model_registry
        self._model_cache: dict[str, object] = {}

    def _get_model(self, model_name: str):
        if model_name in self._model_cache:
            return self._model_cache[model_name]

        from qwen_tts import Qwen3TTSModel

        # On Apple Silicon (MPS), qwen-tts can fail with
        # "unsupported scalarType" when it selects bf16/fp16.
        # Force a safe dtype for model load.
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

        try:
            model = self._get_model(model_name)
            wavs, sr = model.generate_voice_clone(
                text=request.input,
                language=voice.language,
                ref_audio=voice.ref_audio_path,
                ref_text=voice.ref_text,
            )

            fd, out_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            sf.write(out_path, wavs[0], sr)

            return FileResponse(
                out_path,
                media_type="audio/wav",
                filename="speech.wav",
                background=BackgroundTask(self._cleanup_file, out_path),
            )
        except Exception as exc:  # pragma: no cover
            raise internal_error(f"Speech generation failed: {exc}") from exc
