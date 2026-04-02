from __future__ import annotations

from llm_tts_api.errors import invalid_request
from llm_tts_api.services.tts_providers.base import TTSProviderStrategy


class TTSProviderRegistry:
    def __init__(self, providers: list[TTSProviderStrategy]) -> None:
        self._providers = {provider.provider_name: provider for provider in providers}

    def get(self, provider_name: str) -> TTSProviderStrategy:
        provider = self._providers.get(provider_name)
        if provider is None:
            raise invalid_request(
                f"provider '{provider_name}' is not supported",
                param="provider",
            )
        return provider

