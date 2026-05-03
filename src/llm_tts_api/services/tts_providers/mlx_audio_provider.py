from __future__ import annotations

import inspect
import io
from typing import Any

import numpy as np
import soundfile as sf  # type: ignore[import-untyped]

from llm_tts_api.errors import invalid_request
from llm_tts_api.services.tts_providers.base import SynthesisRequest
from llm_tts_api.services.tts_providers.cached_model_provider import CachedModelProvider
from llm_tts_api.services.tts_providers.voice_args import (
    VoiceArgsSelection,
    build_generation_args,
    select_voice_args,
)


class MLXAudioTTSProvider(CachedModelProvider):
    """Generic mlx-audio provider supporting clone and named-voice synthesis."""

    provider_name = "mlx_audio"

    def _load_model(self, model_name: str) -> Any:
        """Load and return an ``mlx_audio`` TTS model instance."""
        try:
            import mlx_audio.tts.utils as mlx_audio_model  # type: ignore[import-untyped]
        except Exception as exc:  # noqa: BLE001
            raise invalid_request(
                "Provider 'mlx_audio' requires the optional dependency 'mlx-audio'",
                param="provider",
            ) from exc

        try:
            model = mlx_audio_model.load(model_name)
        except Exception as exc:  # noqa: BLE001
            raise invalid_request(
                f"Failed to load mlx_audio model '{model_name}': {exc}",
                param="model",
            ) from exc
        return model


    @staticmethod
    def _signature_params(model: Any) -> set[str]:
        """Inspect supported generate parameters, with safe fallback defaults."""
        try:
            return set(inspect.signature(model.generate).parameters.keys())
        except Exception:  # noqa: BLE001
            return {"text", "voice", "ref_audio", "ref_text"}

    @staticmethod
    def _build_voice_selection(request: SynthesisRequest, params: set[str]) -> VoiceArgsSelection:
        """Choose clone or named voice arguments according to project policy."""
        selection = select_voice_args(
            voice_name=request.voice_name,
            ref_audio_path=request.voice.ref_audio_path,
            ref_text=request.voice.ref_text,
            available_params=params,
            prefer_clone=True,
            require_any=True,
        )
        if selection.primary_args:
            return selection

        raise invalid_request(
            "No usable voice provided: configure ref_audio_path/ref_text or set request voice_name",
            param="voice",
        )

    @staticmethod
    def _generation_args(request: SynthesisRequest, params: set[str]) -> dict[str, Any]:
        """Build provider generation arguments (language/temperature/top_p)."""
        if request.generation is None:
            return {}
        return build_generation_args(
            language=request.generation.language,
            temperature=request.generation.temperature,
            top_p=request.generation.top_p,
            available_params=params,
        )

    def synthesize_chunks(self, request: SynthesisRequest) -> list[bytes]:
        """Synthesize all chunks and return WAV payloads."""
        model = self._get_model(request.model_name)
        model_lock = self._get_model_lock(request.model_name)
        output: list[bytes] = []

        with model_lock:
            params = self._signature_params(model)
            voice_selection = self._build_voice_selection(request, params)
            generation_args = self._generation_args(request, params)

            for chunk in request.chunks:
                synthesis_args = {"text": chunk, **voice_selection.primary_args, **generation_args}
                try:
                    results = model.generate(**synthesis_args)
                except AssertionError as exc:
                    if voice_selection.used_named_voice and voice_selection.clone_fallback_args:
                        fallback_args = {
                            "text": chunk,
                            **voice_selection.clone_fallback_args,
                            **generation_args,
                        }
                        results = model.generate(**fallback_args)
                    else:
                        raise invalid_request(str(exc), param="voice") from exc

                for result in results:
                    buf = io.BytesIO()
                    sf.write(buf, np.asarray(result.audio), int(result.sample_rate), format="WAV")
                    output.append(buf.getvalue())

        return output

