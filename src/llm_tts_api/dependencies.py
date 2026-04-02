from __future__ import annotations

from functools import lru_cache

from llm_tts_api.config import Settings
from llm_tts_api.services.model_registry import ModelRegistry
from llm_tts_api.services.stt_service import STTService
from llm_tts_api.services.tts_providers.mlx_voxtral_provider import MLXVoxtralTTSProvider
from llm_tts_api.services.tts_providers.qwen_provider import QwenTTSProvider
from llm_tts_api.services.tts_providers.registry import TTSProviderRegistry
from llm_tts_api.services.tts_service import TTSService


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def get_model_registry() -> ModelRegistry:
    return ModelRegistry(get_settings())


@lru_cache(maxsize=1)
def get_tts_service() -> TTSService:
    return TTSService(
        settings=get_settings(),
        model_registry=get_model_registry(),
        provider_registry=get_tts_provider_registry(),
    )


@lru_cache(maxsize=1)
def get_tts_provider_registry() -> TTSProviderRegistry:
    return TTSProviderRegistry(
        providers=[
            QwenTTSProvider(),
            MLXVoxtralTTSProvider(),
        ]
    )


@lru_cache(maxsize=1)
def get_stt_service() -> STTService:
    return STTService()
