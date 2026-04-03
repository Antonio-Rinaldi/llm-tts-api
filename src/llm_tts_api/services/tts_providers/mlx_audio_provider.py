from __future__ import annotations

import io
import inspect

import numpy as np
import soundfile as sf

from llm_tts_api.errors import invalid_request
from llm_tts_api.services.tts_providers.base import SynthesisRequest
from llm_tts_api.services.tts_providers.cached_model_provider import CachedModelProvider
from llm_tts_api.services.tts_providers.voice_args import build_clone_voice_args, build_named_voice_args


class MLXAudioTTSProvider(CachedModelProvider):
    provider_name = "mlx_audio"

    def _load_model(self, model_name: str):
        try:
            import mlx_audio.tts.utils as mlx_audio_model
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
    def _signature_params(model: object) -> set[str]:
        try:
            return set(inspect.signature(model.generate).parameters.keys())
        except Exception:  # noqa: BLE001
            return {"text", "voice", "ref_audio", "ref_text"}

    @staticmethod
    def _build_voice_kwargs(request: SynthesisRequest, params: set[str]) -> tuple[dict[str, str], bool]:
        selected_clone_args = build_clone_voice_args(
            ref_audio_path=request.voice.ref_audio_path,
            ref_text=request.voice.ref_text,
            available_params=params,
        )
        if selected_clone_args:
            return selected_clone_args, False

        selected_named_voice_args = build_named_voice_args(
            voice_name=request.voice_name,
            available_params=params,
        )
        if selected_named_voice_args:
            return selected_named_voice_args, True

        raise invalid_request(
            "No usable voice provided: configure ref_audio_path/ref_text or set request voice_name",
            param="voice",
        )


    def synthesize_chunks(self, request: SynthesisRequest) -> list[bytes]:
        model = self._get_model(request.model_name)
        model_lock = self._get_model_lock(request.model_name)
        output: list[bytes] = []

        with model_lock:
            params = self._signature_params(model)
            voice_kwargs, used_voice_name = self._build_voice_kwargs(request, params)

            for chunk in request.chunks:
                kwargs: dict[str, str] = {"text": chunk, **voice_kwargs}
                try:
                    results = model.generate(**kwargs)
                except AssertionError as exc:
                    # If direct voice selection fails, fallback to cloning refs when available.
                    if used_voice_name and request.voice.ref_audio_path:
                        fallback_kwargs = {
                            "text": chunk,
                            **build_clone_voice_args(
                                ref_audio_path=request.voice.ref_audio_path,
                                ref_text=request.voice.ref_text,
                                available_params=params,
                            ),
                        }
                        results = model.generate(**fallback_kwargs)
                    else:
                        raise invalid_request(str(exc), param="voice") from exc

                for result in results:
                    buf = io.BytesIO()
                    sf.write(buf, np.asarray(result.audio), int(result.sample_rate), format="WAV")
                    output.append(buf.getvalue())

        return output

