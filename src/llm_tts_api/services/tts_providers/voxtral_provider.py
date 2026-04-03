from __future__ import annotations

import io

import numpy as np
import soundfile as sf

from llm_tts_api.errors import invalid_request
from llm_tts_api.services.tts_providers.base import SynthesisRequest
from llm_tts_api.services.tts_providers.cached_model_provider import CachedModelProvider


class VoxtralTTSProvider(CachedModelProvider):
    provider_name = "voxtral"

    def _load_model(self, model_name: str):
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
        return model


    @staticmethod
    def _build_generate_kwargs(request: SynthesisRequest, chunk: str) -> dict[str, str]:
        if not request.voice.ref_audio_path:
            raise invalid_request(
                "Voxtral provider requires voice cloning references (ref_audio_path/ref_text)",
                param="voice",
            )

        return {
            "text": chunk,
            "ref_audio": request.voice.ref_audio_path,
            "ref_text": request.voice.ref_text,
        }

    def synthesize_chunks(self, request: SynthesisRequest) -> list[bytes]:
        model = self._get_model(request.model_name)
        model_lock = self._get_model_lock(request.model_name)
        output: list[bytes] = []

        with model_lock:
            for chunk in request.chunks:
                kwargs = self._build_generate_kwargs(request, chunk)
                for result in model.generate(**kwargs):
                    buf = io.BytesIO()
                    sf.write(buf, np.asarray(result.audio), int(result.sample_rate), format="WAV")
                    output.append(buf.getvalue())

        return output

