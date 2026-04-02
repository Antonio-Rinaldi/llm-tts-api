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
    registry = TTSProviderRegistry([_FakeProvider("qwen")])

    provider = registry.get("qwen")

    assert provider.provider_name == "qwen"


def test_registry_rejects_unknown_provider() -> None:
    registry = TTSProviderRegistry([_FakeProvider("qwen")])

    try:
        registry.get("unknown")
        assert False, "expected exception"
    except OpenAIHTTPException as exc:
        assert exc.status_code == 400

