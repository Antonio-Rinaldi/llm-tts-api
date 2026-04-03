from __future__ import annotations

import pytest

from llm_tts_api.config import VoiceConfig
from llm_tts_api.errors import OpenAIHTTPException, invalid_request
from llm_tts_api.services.tts_providers.base import SynthesisRequest
from llm_tts_api.services.tts_providers.vllm_omni_provider import VllmOmniTTSProvider


def _request(*, voice_name: str = "", ref_audio_path: str = "", ref_text: str = "") -> SynthesisRequest:
    return SynthesisRequest(
        model_name="vllm-omni/default-tts",
        chunks=["hello"],
        voice=VoiceConfig(ref_audio_path=ref_audio_path, ref_text=ref_text, language="en"),
        voice_name=voice_name,
    )


def test_resolve_loader_rejects_missing_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = VllmOmniTTSProvider()

    def _missing(_module_name: str):
        raise ModuleNotFoundError("missing")

    monkeypatch.setattr("llm_tts_api.services.tts_providers.vllm_omni_provider.importlib.import_module", _missing)

    with pytest.raises(OpenAIHTTPException) as exc_info:
        provider._resolve_loader()

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["param"] == "provider"


def test_get_model_keeps_existing_invalid_request() -> None:
    provider = VllmOmniTTSProvider()

    def _loader(_model_name: str):
        raise invalid_request("bad model", param="model")

    provider._resolve_loader = lambda: _loader  # type: ignore[method-assign]

    with pytest.raises(OpenAIHTTPException) as exc_info:
        provider._get_model("vllm-omni/default-tts")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["message"] == "bad model"


def test_synthesize_chunks_falls_back_from_voice_name_to_clone_kwargs() -> None:
    provider = VllmOmniTTSProvider()

    class FakeModel:
        def generate(
            self,
            text: str,
            voice: str | None = None,
            ref_audio: str | None = None,
            ref_text: str | None = None,
        ) -> dict[str, bytes]:
            if voice is not None:
                raise AssertionError("speaker id unsupported")
            assert text == "hello"
            assert ref_audio == "/tmp/ref.wav"
            assert ref_text == "reference"
            return {"wav_bytes": b"RIFFclone"}

    provider._get_model = lambda _model_name: FakeModel()  # type: ignore[method-assign]

    out = provider.synthesize_chunks(
        _request(voice_name="alloy", ref_audio_path="/tmp/ref.wav", ref_text="reference")
    )

    assert out == [b"RIFFclone"]


def test_synthesize_chunks_falls_back_to_text_only_for_voice_name_only() -> None:
    provider = VllmOmniTTSProvider()

    class FakeModel:
        def generate(self, text: str, voice: str | None = None) -> dict[str, bytes]:
            if voice is not None:
                raise AssertionError("voice unsupported")
            assert text == "hello"
            return {"wav_bytes": b"RIFFtext"}

    provider._get_model = lambda _model_name: FakeModel()  # type: ignore[method-assign]

    out = provider.synthesize_chunks(_request(voice_name="alloy"))

    assert out == [b"RIFFtext"]


def test_result_to_wav_bytes_supports_audio_payload() -> None:
    payload = {"audio": [0.0, 0.0, 0.0], "sample_rate": 16000}

    out = VllmOmniTTSProvider._result_to_wav_bytes(payload)

    assert out[:4] == b"RIFF"


def test_result_to_wav_bytes_rejects_unsupported_payload() -> None:
    with pytest.raises(OpenAIHTTPException) as exc_info:
        VllmOmniTTSProvider._result_to_wav_bytes({"foo": "bar"})

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["param"] == "provider"

