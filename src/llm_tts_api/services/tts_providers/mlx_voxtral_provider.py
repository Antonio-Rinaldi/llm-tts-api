from __future__ import annotations

import io

import numpy as np
import soundfile as sf

from llm_tts_api.errors import invalid_request
from llm_tts_api.services.tts_providers.base import SynthesisRequest


class MLXVoxtralTTSProvider:
    provider_name = "voxtral"

    def __init__(self) -> None:
        self._model_cache: dict[str, object] = {}

    def _get_model(self, model_name: str):
        if model_name in self._model_cache:
            return self._model_cache[model_name]

        try:
            import mlx_audio.tts.utils as mlx_audio_model
        except Exception as exc:  # noqa: BLE001
            raise invalid_request(
                "Provider 'voxtral' requires the optional dependency 'mlx-audio'",
                param="provider",
            ) from exc

        try:
            model = mlx_audio_model.load(model_name)
        except Exception as exc:  # noqa: BLE001
            raise invalid_request(
                f"Failed to load voxtral model '{model_name}': {exc}",
                param="model",
            ) from exc
        self._model_cache[model_name] = model
        return model

    def preload(self, model_name: str) -> None:
        self._get_model(model_name)

    def synthesize_chunks(self, request: SynthesisRequest) -> list[bytes]:
        model = self._get_model(request.model_name)
        output: list[bytes] = []

        for chunk in request.chunks:
            for result in model.generate(
                text=chunk,
                ref_audio=request.voice.ref_audio_path,
                ref_text=request.voice.ref_text,
            ):
                buf = io.BytesIO()
                sf.write(buf, np.asarray(result.audio), int(result.sample_rate), format="WAV")
                output.append(buf.getvalue())

        return output

