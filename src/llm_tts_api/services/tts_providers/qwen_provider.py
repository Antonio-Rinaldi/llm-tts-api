from __future__ import annotations

import io

import soundfile as sf
import torch

from llm_tts_api.errors import invalid_request
from llm_tts_api.services.tts_providers.base import SynthesisRequest


class QwenTTSProvider:
    provider_name = "qwen"

    def __init__(self) -> None:
        self._model_cache: dict[str, object] = {}

    def _get_model(self, model_name: str):
        if model_name in self._model_cache:
            return self._model_cache[model_name]

        try:
            from qwen_tts import Qwen3TTSModel
        except Exception as exc:  # noqa: BLE001
            raise invalid_request(
                "Provider 'qwen' requires the optional dependency 'qwen-tts'",
                param="provider",
            ) from exc

        load_kwargs: dict[str, object] = {}
        if torch.backends.mps.is_available():
            load_kwargs["device_map"] = "mps"
            load_kwargs["dtype"] = torch.float32

        try:
            model = Qwen3TTSModel.from_pretrained(model_name, **load_kwargs)
        except Exception as exc:  # noqa: BLE001
            raise invalid_request(
                f"Failed to load qwen model '{model_name}': {exc}",
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
            wavs, sr = model.generate_voice_clone(
                text=chunk,
                language=request.voice.language,
                ref_audio=request.voice.ref_audio_path,
                ref_text=request.voice.ref_text,
            )
            buf = io.BytesIO()
            sf.write(buf, wavs[0], sr, format="WAV")
            output.append(buf.getvalue())

        return output

