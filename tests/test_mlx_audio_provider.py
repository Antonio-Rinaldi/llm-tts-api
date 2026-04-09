from __future__ import annotations

from dataclasses import dataclass

from llm_tts_api.config import VoiceConfig
from llm_tts_api.errors import OpenAIHTTPException
from llm_tts_api.services.tts_providers.base import GenerationOptions, SynthesisRequest
from llm_tts_api.services.tts_providers.mlx_audio_provider import MLXAudioTTSProvider


@dataclass
class _FakeResult:
    audio: list[float]
    sample_rate: int


def _request(*, voice_name: str = "gold", ref_audio_path: str = "/tmp/gold.wav") -> SynthesisRequest:
    return SynthesisRequest(
        model_name="Qwen/Qwen3-TTS-12Hz-0.6B-Base",
        chunks=["hello"],
        voice=VoiceConfig(ref_audio_path=ref_audio_path, ref_text="reference text", language="Italian"),
        voice_name=voice_name,
        generation=GenerationOptions(language="Italian", temperature=0.8, top_p=0.95),
    )


def test_build_voice_kwargs_prefers_clone_over_voice_name() -> None:
    request = _request()

    selection = MLXAudioTTSProvider._build_voice_selection(
        request,
        {"text", "voice", "ref_audio", "ref_text"},
    )

    assert selection.primary_args == {"ref_audio": "/tmp/gold.wav", "ref_text": "reference text"}
    assert selection.used_named_voice is False


def test_build_voice_kwargs_falls_back_to_voice_name_when_clone_params_missing() -> None:
    request = _request()

    selection = MLXAudioTTSProvider._build_voice_selection(request, {"text", "voice"})

    assert selection.primary_args == {"voice": "gold"}
    assert selection.used_named_voice is True


def test_build_voice_kwargs_rejects_when_no_clone_and_no_named_voice() -> None:
    request = _request(voice_name="", ref_audio_path="")

    try:
        MLXAudioTTSProvider._build_voice_selection(request, {"text"})
        assert False, "expected exception"
    except OpenAIHTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail["param"] == "voice"


def test_synthesize_chunks_uses_clone_kwargs_even_when_voice_name_present() -> None:
    provider = MLXAudioTTSProvider()
    captured: list[dict[str, str]] = []

    class _FakeModel:
        def generate(
            self,
            text: str,
            ref_audio: str,
            ref_text: str,
            temperature: float,
            top_p: float,
            language: str,
        ):
            kwargs = {
                "text": text,
                "ref_audio": ref_audio,
                "ref_text": ref_text,
                "temperature": temperature,
                "top_p": top_p,
                "language": language,
            }
            captured.append(kwargs)
            return [_FakeResult(audio=[0.0, 0.0, 0.0], sample_rate=16000)]

    provider._get_model = lambda _model_name: _FakeModel()  # type: ignore[method-assign]

    wavs = provider.synthesize_chunks(_request())

    assert len(wavs) == 1
    assert wavs[0][:4] == b"RIFF"
    assert captured == [
        {
            "text": "hello",
            "ref_audio": "/tmp/gold.wav",
            "ref_text": "reference text",
            "temperature": 0.8,
            "top_p": 0.95,
            "language": "Italian",
        }
    ]


