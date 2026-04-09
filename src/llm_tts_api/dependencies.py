from __future__ import annotations

from functools import lru_cache

from llm_tts_api.config import Settings
from llm_tts_api.services.model_registry import ModelRegistry
from llm_tts_api.services.stt_service import STTService
from llm_tts_api.services.tts_providers.mlx_audio_provider import MLXAudioTTSProvider
from llm_tts_api.services.tts_providers.registry import TTSProviderRegistry
from llm_tts_api.services.tts_providers.vllm_omni_provider import VllmOmniTTSProvider
from llm_tts_api.services.tts_providers.voxtral_provider import VoxtralTTSProvider
from llm_tts_api.services.tts_service import TTSService


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return singleton validated application settings."""
    return Settings()


@lru_cache(maxsize=1)
def get_model_registry() -> ModelRegistry:
    """Return singleton model registry bound to application settings."""
    return ModelRegistry(get_settings())


@lru_cache(maxsize=1)
def get_tts_service() -> TTSService:
    """Return singleton TTS service with provider registry and preload behavior."""
    return TTSService(
        settings=get_settings(),
        model_registry=get_model_registry(),
        provider_registry=get_tts_provider_registry(),
    )


@lru_cache(maxsize=1)
def get_tts_provider_registry() -> TTSProviderRegistry:
    """Return singleton provider registry with all supported providers."""
    return TTSProviderRegistry(
        providers=[
            MLXAudioTTSProvider(),
            VoxtralTTSProvider(),
            VllmOmniTTSProvider(),
        ]
    )


@lru_cache(maxsize=1)
def get_stt_service() -> STTService:
    """Return singleton placeholder STT service."""
    return STTService()
