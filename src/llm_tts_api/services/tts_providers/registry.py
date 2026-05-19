from __future__ import annotations

from llm_tts_api.errors import invalid_request
from llm_tts_api.services.tts_providers.base import TTSProviderStrategy

_KNOWN_MODEL_FAMILY_PREFIXES = ("qwen", "voxtral-mini", "voxtral-small", "llama", "mistral")


class TTSProviderRegistry:
    """Registry mapping provider names to concrete synthesis strategies.

    Provider names are *engine identifiers* (e.g. ``mlx_audio``, ``voxtral``,
    ``vllm-omni``) — they select which synthesis backend handles the request.
    Model names are *checkpoints* (e.g. ``Qwen/Qwen3-TTS-12Hz-0.6B-Base``) and
    are configured per-provider via the ``model`` field on the request/preset.
    """

    def __init__(self, providers: list[TTSProviderStrategy]) -> None:
        """Register provider instances keyed by their ``provider_name``."""
        self._providers = {provider.provider_name: provider for provider in providers}

    def get(self, provider_name: str) -> TTSProviderStrategy:
        """Return a provider strategy or raise a normalized API error."""
        provider = self._providers.get(provider_name)
        if provider is None:
            valid = ", ".join(self._providers.keys())
            message = f"provider '{provider_name}' is not supported. Valid providers: {valid}."
            if self._looks_like_model_name(provider_name):
                message += (
                    f" Note: '{provider_name}' refers to a model family, not a provider — "
                    "use provider='mlx_audio' with the desired model "
                    "(e.g. model='Qwen/Qwen3-TTS-12Hz-0.6B-Base')."
                )
            raise invalid_request(message, param="provider")
        return provider

    @staticmethod
    def _looks_like_model_name(name: str) -> bool:
        """Heuristic: does ``name`` look like a model/checkpoint rather than a provider?"""
        if "/" in name:
            return True
        lowered = name.lower()
        return any(lowered.startswith(prefix) for prefix in _KNOWN_MODEL_FAMILY_PREFIXES)

    def names(self) -> list[str]:
        """Return registered provider names in registration order."""
        return list(self._providers.keys())

    def all(self) -> list[TTSProviderStrategy]:
        """Return registered provider instances in registration order."""
        return list(self._providers.values())

    def find(self, provider_name: str) -> TTSProviderStrategy | None:
        """Return a provider strategy or ``None`` when not registered."""
        return self._providers.get(provider_name)
