from llm_tts_api.errors import OpenAIHTTPException
from llm_tts_api.services.tts_providers.base import SynthesisRequest
from llm_tts_api.services.tts_providers.registry import TTSProviderRegistry


class _FakeProvider:
    def __init__(self, provider_name: str) -> None:
        self.provider_name = provider_name

    def synthesize_chunks(self, request: SynthesisRequest) -> list[bytes]:
        _ = request
        return [b"wav"]


def test_registry_returns_registered_provider() -> None:
    registry = TTSProviderRegistry([_FakeProvider("mlx_audio")])

    provider = registry.get("mlx_audio")

    assert provider.provider_name == "mlx_audio"


def test_registry_rejects_unknown_provider() -> None:
    registry = TTSProviderRegistry(
        [_FakeProvider("mlx_audio"), _FakeProvider("voxtral"), _FakeProvider("vllm-omni")]
    )

    try:
        registry.get("nonexistent")
        raise AssertionError("expected exception")
    except OpenAIHTTPException as exc:
        assert exc.status_code == 400
        message = str(exc.detail)
        assert "Valid providers:" in message
        assert "mlx_audio" in message
        assert "voxtral" in message
        assert "vllm-omni" in message
        assert "model family" not in message


def test_registry_rejects_qwen_with_model_vs_provider_hint() -> None:
    registry = TTSProviderRegistry(
        [_FakeProvider("mlx_audio"), _FakeProvider("voxtral"), _FakeProvider("vllm-omni")]
    )

    try:
        registry.get("qwen")
        raise AssertionError("expected exception")
    except OpenAIHTTPException as exc:
        assert exc.status_code == 400
        message = str(exc.detail)
        assert "Valid providers:" in message
        assert "mlx_audio" in message
        assert "voxtral" in message
        assert "vllm-omni" in message
        assert "model family" in message
        assert "mlx_audio" in message


def test_registry_rejects_model_path_with_hint() -> None:
    registry = TTSProviderRegistry(
        [_FakeProvider("mlx_audio"), _FakeProvider("voxtral"), _FakeProvider("vllm-omni")]
    )

    try:
        registry.get("Qwen/Qwen3-TTS-12Hz-0.6B-Base")
        raise AssertionError("expected exception")
    except OpenAIHTTPException as exc:
        assert exc.status_code == 400
        message = str(exc.detail)
        assert "Valid providers:" in message
        assert "model family" in message
