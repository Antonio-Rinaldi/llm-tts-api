from __future__ import annotations

import io
from threading import Lock

import numpy as np
import soundfile as sf

from llm_tts_api.errors import invalid_request
from llm_tts_api.services.tts_providers.base import SynthesisRequest


class QwenTTSProvider:
    provider_name = "qwen"

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

    def _get_model(self, model_name: str):
        with self._cache_lock:
            cached = self._model_cache.get(model_name)
        if cached is not None:
            return cached

        try:
            import mlx_audio.tts.utils as mlx_audio_model
        except Exception as exc:  # noqa: BLE001
            raise invalid_request(
                "Provider 'qwen' requires the optional dependency 'mlx-audio'",
                param="provider",
            ) from exc

        try:
            model = mlx_audio_model.load(model_name)
        except Exception as exc:  # noqa: BLE001
            raise invalid_request(
                f"Failed to load qwen model '{model_name}': {exc}",
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

    def synthesize_chunks(self, request: SynthesisRequest) -> list[bytes]:
        model = self._get_model(request.model_name)
        model_lock = self._get_model_lock(request.model_name)
        output: list[bytes] = []

        with model_lock:
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

