from __future__ import annotations

import importlib
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


class VllmOmniTTSProvider(CachedModelProvider):
    """Adapter strategy for vllm-omni TTS backends."""

    provider_name = "vllm-omni"

    @staticmethod
    def _resolve_loader() -> Any:
        """Resolve a compatible ``load`` callable from vllm-omni modules."""
        candidates = [
            ("vllm_omni.tts.utils", "load"),
            ("vllm_omni.tts", "load"),
            ("vllm_omni", "load"),
        ]

        last_exc: Exception | None = None
        for module_name, attr_name in candidates:
            try:
                module = importlib.import_module(module_name)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                continue

            loader = getattr(module, attr_name, None)
            if callable(loader):
                return loader

        raise invalid_request(
            "Provider 'vllm-omni' requires the optional dependency 'vllm-omni'",
            param="provider",
        ) from last_exc

    def _load_model(self, model_name: str) -> Any:
        """Load and return a vllm-omni model instance."""
        try:
            loader = self._resolve_loader()
            model = loader(model_name)
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "status_code", None) == 400:
                raise
            raise invalid_request(
                f"Failed to load vllm-omni model '{model_name}': {exc}",
                param="model",
            ) from exc
        return model


    @staticmethod
    def _signature_params(model: object) -> set[str]:
        """Return generate signature parameters or a robust fallback list."""
        generate = getattr(model, "generate", None)
        if callable(generate):
            try:
                return set(inspect.signature(generate).parameters.keys())
            except Exception:  # noqa: BLE001
                pass
        return {
            "text",
            "voice",
            "voice_id",
            "speaker",
            "ref_audio",
            "reference_audio",
            "ref_text",
            "reference_text",
        }

    @staticmethod
    def _build_voice_selection(request: SynthesisRequest, params: set[str]) -> VoiceArgsSelection:
        """Select named/cloned voice arguments for vllm-omni synthesis."""
        return select_voice_args(
            voice_name=request.voice_name,
            ref_audio_path=request.voice.ref_audio_path,
            ref_text=request.voice.ref_text,
            available_params=params,
            prefer_clone=True,
            require_any=False,
        )

    @staticmethod
    def _generation_args(request: SynthesisRequest, params: set[str]) -> dict[str, Any]:
        """Build optional generation arguments for supported models."""
        if request.generation is None:
            return {}
        return build_generation_args(
            language=request.generation.language,
            temperature=request.generation.temperature,
            top_p=request.generation.top_p,
            available_params=params,
        )

    @staticmethod
    def _generate(model: object, kwargs: dict[str, str]) -> Any:
        """Invoke model generation regardless of object calling style."""
        generate = getattr(model, "generate", None)
        if callable(generate):
            return generate(**kwargs)
        if callable(model):
            return model(**kwargs)
        raise invalid_request(
            "vllm-omni model does not expose a callable generator",
            param="model",
        )

    @staticmethod
    def _result_to_wav_bytes(result: object) -> bytes:
        """Convert provider results to WAV bytes across supported payload shapes."""
        if isinstance(result, bytes):
            return result

        if hasattr(result, "wav_bytes") and isinstance(result.wav_bytes, bytes):
            return result.wav_bytes

        sample_rate = None
        audio = None

        if isinstance(result, dict):
            maybe_bytes = result.get("wav_bytes")
            if isinstance(maybe_bytes, bytes):
                return maybe_bytes
            audio = result.get("audio")
            sample_rate = result.get("sample_rate")
        else:
            audio = getattr(result, "audio", None)
            sample_rate = getattr(result, "sample_rate", None)

        if audio is None or sample_rate is None:
            raise invalid_request(
                "vllm-omni provider returned an unsupported audio payload",
                param="provider",
            )

        buf = io.BytesIO()
        sf.write(buf, np.asarray(audio), int(sample_rate), format="WAV")
        return buf.getvalue()

    def synthesize_chunks(self, request: SynthesisRequest) -> list[bytes]:
        """Synthesize request chunks and normalize output payload shapes."""
        model = self._get_model(request.model_name)
        model_lock = self._get_model_lock(request.model_name)
        output: list[bytes] = []

        with model_lock:
            params = self._signature_params(model)
            voice_selection = self._build_voice_selection(request, params)
            generation_args = self._generation_args(request, params)

            for chunk in request.chunks:
                synthesis_args: dict[str, Any] = {
                    "text": chunk,
                    **voice_selection.primary_args,
                    **generation_args,
                }
                try:
                    results = self._generate(model, synthesis_args)
                except AssertionError as exc:
                    if voice_selection.used_named_voice and voice_selection.clone_fallback_args:
                        fallback_args = {
                            "text": chunk,
                            **voice_selection.clone_fallback_args,
                            **generation_args,
                        }
                        results = self._generate(
                            model,
                            fallback_args,
                        )
                    elif voice_selection.used_named_voice:
                        results = self._generate(model, {"text": chunk, **generation_args})
                    else:
                        raise invalid_request(str(exc), param="voice") from exc

                if isinstance(results, (bytes, dict)) or hasattr(results, "audio"):
                    output.append(self._result_to_wav_bytes(results))
                    continue

                for result in results:
                    output.append(self._result_to_wav_bytes(result))

        return output


