from __future__ import annotations

import inspect
import io
from typing import Any

import numpy as np
import soundfile as sf  # type: ignore[import-untyped]

from llm_tts_api.errors import invalid_request
from llm_tts_api.services.tts_providers.base import SynthesisRequest
from llm_tts_api.services.tts_providers.cached_model_provider import CachedModelProvider
from llm_tts_api.services.tts_providers.voice_args import build_generation_args


class VoxtralTTSProvider(CachedModelProvider):
    """Voxtral provider strategy restricted to reference-audio cloning mode."""

    provider_name = "voxtral"

    def _load_model(self, model_name: str) -> Any:
        """Load and return a Voxtral model through mlx-audio."""
        try:
            import mlx_audio.tts.utils as mlx_audio_model  # type: ignore[import-untyped]
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
    def _signature_params(model: Any) -> set[str]:
        """Inspect supported generation parameters with safe defaults."""
        try:
            return set(inspect.signature(model.generate).parameters.keys())
        except Exception:  # noqa: BLE001
            return {"text", "ref_audio", "ref_text"}

    @staticmethod
    def _build_generate_kwargs(
        request: SynthesisRequest, chunk: str, params: set[str]
    ) -> dict[str, Any]:
        """Build synthesis args and enforce cloning-only policy for Voxtral provider."""
        if not request.voice.ref_audio_path:
            raise invalid_request(
                "Voxtral provider requires voice cloning references (ref_audio_path/ref_text)",
                param="voice",
            )

        generation_args = (
            build_generation_args(
                language=request.generation.language,
                temperature=request.generation.temperature,
                top_p=request.generation.top_p,
                available_params=params,
            )
            if request.generation is not None
            else {}
        )

        return {
            "text": chunk,
            "ref_audio": request.voice.ref_audio_path,
            "ref_text": request.voice.ref_text,
            **generation_args,
        }

    def synthesize_chunks(self, request: SynthesisRequest) -> list[bytes]:
        """Synthesize all chunks and return WAV payloads for concatenation."""
        model = self._get_model(request.model_name)
        model_lock = self._get_model_lock(request.model_name)
        output: list[bytes] = []

        with model_lock:
            params = self._signature_params(model)
            for chunk in request.chunks:
                kwargs = self._build_generate_kwargs(request, chunk, params)
                for result in model.generate(**kwargs):
                    buf = io.BytesIO()
                    sf.write(buf, np.asarray(result.audio), int(result.sample_rate), format="WAV")
                    output.append(buf.getvalue())

        return output
