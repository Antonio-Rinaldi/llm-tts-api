from __future__ import annotations

import importlib
import io
import inspect
from threading import Lock
from typing import Any

import numpy as np
import soundfile as sf

from llm_tts_api.errors import invalid_request
from llm_tts_api.services.tts_providers.base import SynthesisRequest
from llm_tts_api.services.tts_providers.voice_args import build_clone_voice_args, build_named_voice_args


class VllmOmniTTSProvider:
    provider_name = "vllm-omni"

    def __init__(self) -> None:
        self._model_cache: dict[str, object] = {}
        self._model_locks: dict[str, Lock] = {}
        self._cache_lock = Lock()

    def _get_model_lock(self, model_name: str) -> Lock:
        with self._cache_lock:
            model_lock = self._model_locks.get(model_name)
            if model_lock is None:
                model_lock = Lock()
                self._model_locks[model_name] = model_lock
            return model_lock

    @staticmethod
    def _resolve_loader() -> Any:
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

    def _get_model(self, model_name: str):
        with self._cache_lock:
            cached = self._model_cache.get(model_name)
        if cached is not None:
            return cached

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

        with self._cache_lock:
            existing = self._model_cache.get(model_name)
            if existing is not None:
                return existing
            self._model_cache[model_name] = model
            if model_name not in self._model_locks:
                self._model_locks[model_name] = Lock()
        return model

    def preload(self, model_name: str) -> None:
        self._get_model(model_name)

    @staticmethod
    def _signature_params(model: object) -> set[str]:
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
    def _build_voice_kwargs(request: SynthesisRequest, params: set[str]) -> tuple[dict[str, str], dict[str, str], bool]:
        selected_named_voice_args = build_named_voice_args(
            voice_name=request.voice_name,
            available_params=params,
        )
        selected_clone_args = build_clone_voice_args(
            ref_audio_path=request.voice.ref_audio_path,
            ref_text=request.voice.ref_text,
            available_params=params,
        )

        if selected_named_voice_args:
            return selected_named_voice_args, selected_clone_args, True
        if selected_clone_args:
            return selected_clone_args, selected_clone_args, False
        return {}, {}, False

    @staticmethod
    def _generate(model: object, kwargs: dict[str, str]) -> Any:
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
        model = self._get_model(request.model_name)
        model_lock = self._get_model_lock(request.model_name)
        output: list[bytes] = []

        with model_lock:
            params = self._signature_params(model)
            voice_kwargs, clone_kwargs, used_voice_name = self._build_voice_kwargs(request, params)

            for chunk in request.chunks:
                kwargs: dict[str, str] = {"text": chunk, **voice_kwargs}
                try:
                    results = self._generate(model, kwargs)
                except AssertionError as exc:
                    if used_voice_name and clone_kwargs:
                        results = self._generate(model, {"text": chunk, **clone_kwargs})
                    elif used_voice_name:
                        results = self._generate(model, {"text": chunk})
                    else:
                        raise invalid_request(str(exc), param="voice") from exc

                if isinstance(results, (bytes, dict)) or hasattr(results, "audio"):
                    output.append(self._result_to_wav_bytes(results))
                    continue

                for result in results:
                    output.append(self._result_to_wav_bytes(result))

        return output


