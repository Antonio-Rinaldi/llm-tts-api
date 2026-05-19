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

import asyncio
from dataclasses import dataclass, field
from typing import cast

from fastapi import Request

from llm_tts_api.config import PreloadEntry, Settings
from llm_tts_api.engine import DeviceProfile, resolve_device_profile
from llm_tts_api.services.model_cache import LRUModelCache
from llm_tts_api.services.model_registry import ModelRegistry
from llm_tts_api.services.presets import PresetRegistry
from llm_tts_api.services.stt_service import STTService
from llm_tts_api.services.tts_providers.auto_select import (
    ProviderSelection,
    select_provider,
)
from llm_tts_api.services.tts_providers.cached_model_provider import CachedModelProvider
from llm_tts_api.services.tts_providers.mlx_audio_provider import MLXAudioTTSProvider
from llm_tts_api.services.tts_providers.registry import TTSProviderRegistry
from llm_tts_api.services.tts_providers.vllm_omni_provider import VllmOmniTTSProvider
from llm_tts_api.services.tts_providers.voxtral_provider import VoxtralTTSProvider
from llm_tts_api.services.tts_service import ModelLockMap, TTSService
from llm_tts_api.services.voice_store import (
    FsBlobRepository,
    FsJsonMetadataRepository,
    VoiceBlobRepository,
    VoiceMetadataRepository,
    VoiceSeedIngestor,
    force_polling_from_env,
    resolve_seed_file_path,
)


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
    provider_selection: ProviderSelection
    model_registry: ModelRegistry
    provider_registry: TTSProviderRegistry
    model_cache: LRUModelCache
    tts_service: TTSService
    stt_service: STTService
    concurrency_semaphore: asyncio.Semaphore
    queue_semaphore: asyncio.Semaphore
    voice_metadata_repo: VoiceMetadataRepository
    voice_blob_repo: VoiceBlobRepository
    voice_seed_ingestor: VoiceSeedIngestor
    model_locks: ModelLockMap = field(default_factory=dict)


def build_voice_metadata_repo(settings: Settings) -> VoiceMetadataRepository:
    """Construct the metadata repository for the configured backend.

    Dispatches on ``settings.tts_voice_metadata_backend``:

    * ``fs_json`` (default) → :class:`FsJsonMetadataRepository`, no extras.
    * ``postgres`` → :class:`PostgresMetadataRepository`, requires the
      ``[postgres]`` extra and a non-empty ``TTS_VOICE_METADATA_DSN``.
      A missing ``psycopg`` import is surfaced as ``config_error.missing_extra``
      (NFR-ST-02) so operators can identify the wiring problem from the
      startup log without reading a traceback.
    """
    backend = settings.tts_voice_metadata_backend
    if backend == "fs_json":
        return FsJsonMetadataRepository(settings.tts_voice_store_dir)
    if backend == "postgres":
        try:
            from llm_tts_api.services.voice_store.postgres_metadata import (
                PostgresMetadataRepository,
            )
        except ModuleNotFoundError as exc:
            missing = exc.name or "psycopg"
            raise RuntimeError(
                "config_error.missing_extra: voice metadata backend 'postgres' "
                f"requires the [postgres] extra (missing module: {missing}). "
                "Install via `pip install '.[postgres]'`."
            ) from exc
        dsn = settings.tts_voice_metadata_dsn
        if not dsn:
            raise ValueError(
                "TTS_VOICE_METADATA_DSN must be set when TTS_VOICE_METADATA_BACKEND=postgres"
            )
        return PostgresMetadataRepository(dsn)
    # Settings._load_voice_metadata_backend rejects unknown values at startup,
    # so this branch is defensive (e.g. tests that bypass __post_init__).
    raise ValueError(f"unknown voice metadata backend: {backend!r}")


def build_default_dependencies() -> AppDependencies:
    """Construct the full default dependency graph from environment.

    Side effects: reads env vars (validates them via ``Settings.__post_init__``)
    and probes the host for the inference device. Heavy work (model preload)
    happens inside ``TTSService`` initialization.
    """
    settings = Settings()
    device_profile = resolve_device_profile()
    # Registration order is the auto-select priority (FR-HW-04):
    #   mps  → mlx_audio, voxtral
    #   cuda → vllm-omni
    #   cpu  → no current provider declares support → fails startup
    mlx_audio = MLXAudioTTSProvider()
    voxtral = VoxtralTTSProvider()
    vllm_omni = VllmOmniTTSProvider()
    providers: list[CachedModelProvider] = [mlx_audio, voxtral, vllm_omni]
    provider_registry = TTSProviderRegistry(providers=[mlx_audio, voxtral, vllm_omni])
    provider_selection = select_provider(
        device_profile=device_profile,
        registry=provider_registry,
    )
    # Reconcile the legacy ``settings.tts_provider`` slot with the
    # auto-selected name so downstream consumers (TTSService preload,
    # model-default lookup) see the same provider the registry will hand
    # them. Settings is mutable by design (no ``frozen=True``).
    settings.tts_provider = provider_selection.provider_name
    settings.tts_model_default = settings.tts_model_default_for_provider(
        provider_selection.provider_name
    )
    settings.tts_model_allowed = settings.tts_model_allowed_for_provider(
        provider_selection.provider_name
    )
    model_registry = ModelRegistry(settings)
    # S-007 concurrency primitives: queue admission, active cap, per-model locks.
    concurrency_semaphore = asyncio.Semaphore(settings.tts_max_concurrent_requests)
    queue_semaphore = asyncio.Semaphore(settings.tts_max_queue_depth)
    model_locks: ModelLockMap = {}
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
        concurrency_semaphore=concurrency_semaphore,
        queue_semaphore=queue_semaphore,
        model_locks=model_locks,
    )
    stt_service = STTService()
    # S-022 + S-023: voice metadata repo selected on ``settings.tts_voice_metadata_backend``.
    # S-022 + S-024: voice blob repo selected on ``settings.tts_voice_blob_backend``.
    # Default FS impls keep the zero-external-services deploy story; alternate impls
    # (postgres, s3) live behind optional extras and raise ``provider_error.missing_extra``
    # if the module is absent (NFR-ST-02).
    voice_metadata_repo = build_voice_metadata_repo(settings)
    voice_blob_repo: VoiceBlobRepository = _build_voice_blob_repo(settings)
    # S-011 — voice_map.json seed ingestion. Path resolution is tolerant
    # (FR-VM-05): unset or missing file → ingestor is constructed with
    # ``seed_file_path=None`` and becomes a no-op.
    voice_seed_ingestor = VoiceSeedIngestor(
        metadata_repo=voice_metadata_repo,
        blob_repo=voice_blob_repo,
        seed_file_path=resolve_seed_file_path(),
        force_polling=force_polling_from_env(),
    )
    return AppDependencies(
        settings=settings,
        device_profile=device_profile,
        provider_selection=provider_selection,
        model_registry=model_registry,
        provider_registry=provider_registry,
        model_cache=model_cache,
        tts_service=tts_service,
        stt_service=stt_service,
        concurrency_semaphore=concurrency_semaphore,
        queue_semaphore=queue_semaphore,
        voice_metadata_repo=voice_metadata_repo,
        voice_blob_repo=voice_blob_repo,
        voice_seed_ingestor=voice_seed_ingestor,
        model_locks=model_locks,
    )


def _build_voice_blob_repo(settings: Settings) -> VoiceBlobRepository:
    """Construct the blob repository selected by ``TTS_VOICE_BLOB_BACKEND``.

    ``fs`` (default) returns :class:`FsBlobRepository` rooted at
    ``settings.tts_voice_store_dir``. ``s3`` imports the optional
    :mod:`llm_tts_api.services.voice_store.s3_blob` module — if the
    ``[s3]`` extra is not installed, ``aiobotocore`` will be missing
    and we surface a ``provider_error.missing_extra`` per NFR-ST-02
    (named in the message so operators can run the right install
    command). The import is local to keep the base install free of
    aiobotocore (the ``base-install-no-extras-import`` smoke test in
    ``test_voice_store.py`` enforces this).
    """
    backend = settings.tts_voice_blob_backend
    if backend == "fs":
        return FsBlobRepository(settings.tts_voice_store_dir)
    if backend == "s3":
        try:
            from llm_tts_api.services.voice_store.s3_blob import S3BlobRepository
        except ModuleNotFoundError as exc:
            missing = exc.name or "aiobotocore"
            raise RuntimeError(
                "provider_error.missing_extra: "
                f"TTS_VOICE_BLOB_BACKEND=s3 requires the '[s3]' extra "
                f"(missing module: {missing}). Install with 'pip install .[s3]'."
            ) from exc
        return S3BlobRepository(
            bucket=settings.tts_voice_blob_s3_bucket,
            endpoint_url=settings.tts_voice_blob_s3_endpoint or None,
            region_name=settings.tts_voice_blob_s3_region or None,
        )
    # Defensive: Settings validates the enum, so this branch is unreachable
    # in normal operation but keeps mypy honest about the union.
    raise ValueError(f"Unknown TTS_VOICE_BLOB_BACKEND={backend!r}")


def _preload_models(
    provider_registry: TTSProviderRegistry, preload_pairs: list[PreloadEntry]
) -> None:
    """Warm the cache for every ``provider:model`` pair from ``TTS_PRELOAD_MODELS``."""
    for entry in preload_pairs:
        provider = provider_registry.get(entry.provider)
        preload_fn = getattr(provider, "preload", None)
        if callable(preload_fn):
            preload_fn(entry.model)


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


def get_provider_selection(request: Request) -> ProviderSelection:
    """Return the process-wide :class:`ProviderSelection` (S-006)."""
    return cast(ProviderSelection, request.app.state.provider_selection)


def get_model_cache(request: Request) -> LRUModelCache:
    """Return the process-wide :class:`LRUModelCache` (S-008)."""
    return cast(LRUModelCache, request.app.state.model_cache)


def get_voice_metadata_repo(request: Request) -> VoiceMetadataRepository:
    """Return the process-wide :class:`VoiceMetadataRepository` (S-022)."""
    return cast(VoiceMetadataRepository, request.app.state.voice_metadata_repo)


def get_voice_blob_repo(request: Request) -> VoiceBlobRepository:
    """Return the process-wide :class:`VoiceBlobRepository` (S-022)."""
    return cast(VoiceBlobRepository, request.app.state.voice_blob_repo)


def get_preset_registry_snapshot(request: Request) -> PresetRegistry:
    """Return the request-scoped :class:`PresetRegistry` snapshot (S-029 T3).

    This getter is resolved by FastAPI ONCE per request, at request-entry,
    binding the current ``app.state.preset_registry`` for the whole call.
    Even if the S-029 reloader replaces the slot mid-flight, the request
    keeps the snapshot it captured here — that is the NFR-PR-04 in-flight
    snapshot invariant the cycle-2 hot-reload story locks in.

    S-028's ``resolve_preset(request, snapshot, settings)`` consumes this
    via ``Depends(get_preset_registry_snapshot)``; the resolver MUST NOT
    re-read ``app.state.preset_registry`` directly. The contract is
    documented in ``docs/planning/sprints/sprint-impl-7.md`` § "Locked
    Service Interface".
    """
    return cast(PresetRegistry, request.app.state.preset_registry)
