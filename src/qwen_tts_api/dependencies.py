from __future__ import annotations

from functools import lru_cache

from qwen_tts_api.config import Settings
from qwen_tts_api.services.model_registry import ModelRegistry
from qwen_tts_api.services.stt_service import STTService
from qwen_tts_api.services.tts_service import TTSService


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def get_model_registry() -> ModelRegistry:
    return ModelRegistry(get_settings())


@lru_cache(maxsize=1)
def get_tts_service() -> TTSService:
    return TTSService(settings=get_settings(), model_registry=get_model_registry())


@lru_cache(maxsize=1)
def get_stt_service() -> STTService:
    return STTService()
