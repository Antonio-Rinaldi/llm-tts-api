"""FastAPI dependency-injection helpers backed by ``app.state`` singletons.

Implements FR-HL-03 (SRS §4.8): singletons live in lifespan-managed ``app.state``
slots, not module-level ``@lru_cache`` factories. This retires the cross-test
singleton-leak problem and gives future sprints (S-007 semaphores, S-008 model
cache) a single seam to bind their own slots.

The Depends-shape getters here read from ``request.app.state.*``. A separate
``build_default_dependencies`` factory is consumed by the lifespan in
``main.py`` to construct everything once at startup.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from fastapi import Request

from llm_tts_api.config import Settings
from llm_tts_api.engine import DeviceProfile, resolve_device_profile
from llm_tts_api.services.model_cache import LRUModelCache
from llm_tts_api.services.model_registry import ModelRegistry
from llm_tts_api.services.stt_service import STTService
from llm_tts_api.services.tts_providers.cached_model_provider import CachedModelProvider
from llm_tts_api.services.tts_providers.mlx_audio_provider import MLXAudioTTSProvider
from llm_tts_api.services.tts_providers.registry import TTSProviderRegistry
from llm_tts_api.services.tts_providers.vllm_omni_provider import VllmOmniTTSProvider
from llm_tts_api.services.tts_providers.voxtral_provider import VoxtralTTSProvider
from llm_tts_api.services.tts_service import TTSService


@dataclass(slots=True)
class AppDependencies:
    """Bundle of process-wide singletons stashed on ``app.state``.

    A single container makes the lifespan handoff explicit: lifespan assembles
    one instance, fans it out across ``app.state.*`` slots, and the request-
    aware getters below read each slot back out. Tests construct their own
    instance (or set individual slots) and skip the heavy construction path.
    """

    settings: Settings
    device_profile: DeviceProfile
    model_registry: ModelRegistry
    provider_registry: TTSProviderRegistry
    model_cache: LRUModelCache
    tts_service: TTSService
    stt_service: STTService


def build_default_dependencies() -> AppDependencies:
    """Construct the full default dependency graph from environment.

    Side effects: reads env vars (validates them via ``Settings.__post_init__``)
    and probes the host for the inference device. Heavy work (model preload)
    happens inside ``TTSService`` initialization.
    """
    settings = Settings()
    device_profile = resolve_device_profile()
    model_registry = ModelRegistry(settings)
    mlx_audio = MLXAudioTTSProvider()
    voxtral = VoxtralTTSProvider()
    vllm_omni = VllmOmniTTSProvider()
    providers: list[CachedModelProvider] = [mlx_audio, voxtral, vllm_omni]
    provider_registry = TTSProviderRegistry(providers=[mlx_audio, voxtral, vllm_omni])

    # S-008: build the shared LRU cache, hand it to each provider with the
    # provider-specific allow-list, then preload the configured pairs so
    # the first synthesis incurs no load latency (FR-CA-04 / UAT-CA-03).
    model_cache = LRUModelCache(max_size=settings.tts_model_cache_size)
    for provider in providers:
        provider.attach_model_cache(
            model_cache,
            allowed_models=settings.tts_model_allowed_for_provider(provider.provider_name),
        )
    _preload_models(provider_registry, settings.tts_preload_models)

    tts_service = TTSService(
        settings=settings,
        model_registry=model_registry,
        provider_registry=provider_registry,
    )
    stt_service = STTService()
    return AppDependencies(
        settings=settings,
        device_profile=device_profile,
        model_registry=model_registry,
        provider_registry=provider_registry,
        model_cache=model_cache,
        tts_service=tts_service,
        stt_service=stt_service,
    )


def _preload_models(
    provider_registry: TTSProviderRegistry, preload_pairs: list[tuple[str, str]]
) -> None:
    """Warm the cache for every ``provider:model`` pair from ``TTS_PRELOAD_MODELS``."""
    for provider_name, model_id in preload_pairs:
        provider = provider_registry.get(provider_name)
        preload_fn = getattr(provider, "preload", None)
        if callable(preload_fn):
            preload_fn(model_id)


# --- Request-aware Depends-shape getters ------------------------------------
# Routers depend on these; FastAPI's Depends machinery resolves them per request.
# Each one is a thin "pluck from app.state" — no @lru_cache, no module-level
# singletons. Tests override these via ``app.dependency_overrides`` or by
# replacing the corresponding ``app.state`` slot directly.


def get_settings(request: Request) -> Settings:
    """Return the process-wide :class:`Settings`."""
    return cast(Settings, request.app.state.settings)


def get_model_registry(request: Request) -> ModelRegistry:
    """Return the process-wide :class:`ModelRegistry`."""
    return cast(ModelRegistry, request.app.state.model_registry)


def get_tts_provider_registry(request: Request) -> TTSProviderRegistry:
    """Return the process-wide :class:`TTSProviderRegistry`."""
    return cast(TTSProviderRegistry, request.app.state.provider_registry)


def get_tts_service(request: Request) -> TTSService:
    """Return the process-wide :class:`TTSService`."""
    return cast(TTSService, request.app.state.tts_service)


def get_stt_service(request: Request) -> STTService:
    """Return the placeholder :class:`STTService`."""
    return cast(STTService, request.app.state.stt_service)


def get_device_profile(request: Request) -> DeviceProfile:
    """Return the process-wide :class:`DeviceProfile` (S-005)."""
    return cast(DeviceProfile, request.app.state.device_profile)


def get_model_cache(request: Request) -> LRUModelCache:
    """Return the process-wide :class:`LRUModelCache` (S-008)."""
    return cast(LRUModelCache, request.app.state.model_cache)
