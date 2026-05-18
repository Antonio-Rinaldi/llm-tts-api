from __future__ import annotations

from llm_tts_api.errors import invalid_request
from llm_tts_api.services.tts_providers.base import TTSProviderStrategy


class TTSProviderRegistry:
    """Registry mapping provider names to concrete synthesis strategies."""

    def __init__(self, providers: list[TTSProviderStrategy]) -> None:
        """Register provider instances keyed by their ``provider_name``."""
        self._providers = {provider.provider_name: provider for provider in providers}

    def get(self, provider_name: str) -> TTSProviderStrategy:
        """Return a provider strategy or raise a normalized API error."""
        provider = self._providers.get(provider_name)
        if provider is None:
            raise invalid_request(
                f"provider '{provider_name}' is not supported",
                param="provider",
            )
        return provider

    def names(self) -> list[str]:
        """Return registered provider names in registration order."""
        return list(self._providers.keys())

    def all(self) -> list[TTSProviderStrategy]:
        """Return registered provider instances in registration order."""
        return list(self._providers.values())

    def find(self, provider_name: str) -> TTSProviderStrategy | None:
        """Return a provider strategy or ``None`` when not registered."""
        return self._providers.get(provider_name)
