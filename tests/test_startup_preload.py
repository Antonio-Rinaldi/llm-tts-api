"""Startup preload tests (post-S-003).

The lifespan now constructs all singletons via ``build_default_dependencies``
and stashes them on ``app.state``. These tests verify:

1. When the bypass env is NOT set, lifespan calls the builder.
2. When the builder raises (e.g. real preload failure), startup fails fast.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from llm_tts_api.dependencies import AppDependencies
from tests.fakes.fake_tts_service import FakeTTSService


def _stub_deps(fake: FakeTTSService) -> AppDependencies:
    """Build an ``AppDependencies`` bundle whose ``tts_service`` is the fake.

    Other slots are populated minimally just so attribute access doesn't
    blow up during downstream test reads.
    """
    from llm_tts_api.config import Settings
    from llm_tts_api.engine import DeviceProfile
    from llm_tts_api.services.model_cache import LRUModelCache
    from llm_tts_api.services.model_registry import ModelRegistry
    from llm_tts_api.services.stt_service import STTService
    from llm_tts_api.services.tts_providers.auto_select import ProviderSelection
    from llm_tts_api.services.tts_providers.registry import TTSProviderRegistry
    from llm_tts_api.services.voice_store import VoiceSeedIngestor
    from tests.fakes.fake_voice_store import (
        FakeVoiceBlobRepository,
        FakeVoiceMetadataRepository,
    )

    metadata_repo = FakeVoiceMetadataRepository()
    blob_repo = FakeVoiceBlobRepository()

    settings = object.__new__(Settings)  # bypass __post_init__'s env reads
    settings.app_name = "llm-tts-api"
    settings.app_env = "test"
    settings.app_log_level = "INFO"
    settings.tts_voice_map = {}
    settings.tts_max_input_chars = 4096
    settings.tts_max_concurrent_requests = 1
    settings.tts_max_queue_depth = 8
    settings.tts_model_cache_size = 1
    settings.tts_preload_models = []
    settings.tts_shutdown_drain_seconds = 0
    settings.tts_min_free_memory_gb = 0

    return AppDependencies(
        settings=settings,
        device_profile=DeviceProfile(device="cpu", dtype="float32", source="auto"),
        provider_selection=ProviderSelection(
            provider_name="mlx_audio", device="cpu", source="auto"
        ),
        model_registry=object.__new__(ModelRegistry),
        provider_registry=TTSProviderRegistry(providers=[]),
        model_cache=LRUModelCache(max_size=1),
        tts_service=fake,  # type: ignore[arg-type]
        stt_service=STTService(),
        concurrency_semaphore=asyncio.Semaphore(1),
        queue_semaphore=asyncio.Semaphore(8),
        voice_metadata_repo=metadata_repo,
        voice_blob_repo=blob_repo,
        voice_seed_ingestor=VoiceSeedIngestor(
            metadata_repo=metadata_repo,
            blob_repo=blob_repo,
            seed_file_path=None,
        ),
        model_locks={},
    )


def test_startup_calls_builder_when_not_bypassed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the bypass env, lifespan must call ``build_default_dependencies``."""
    from llm_tts_api import main as main_module
    from llm_tts_api.main import TEST_BYPASS_ENV, create_app

    monkeypatch.delenv(TEST_BYPASS_ENV, raising=False)
    fake = FakeTTSService()
    calls: list[bool] = []

    def _spy_builder() -> AppDependencies:
        calls.append(True)
        return _stub_deps(fake)

    monkeypatch.setattr(main_module, "build_default_dependencies", _spy_builder)

    app = create_app()
    with TestClient(app):
        pass

    assert calls == [True]


def test_startup_fails_fast_when_builder_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the builder raises (real-world: model preload failure), startup must propagate."""
    from llm_tts_api import main as main_module
    from llm_tts_api.main import TEST_BYPASS_ENV, create_app

    monkeypatch.delenv(TEST_BYPASS_ENV, raising=False)

    def _failing_builder() -> AppDependencies:
        raise RuntimeError("preload failed")

    monkeypatch.setattr(main_module, "build_default_dependencies", _failing_builder)

    app = create_app()
    with pytest.raises(RuntimeError, match="preload failed"), TestClient(app):
        pass


def test_bypass_env_skips_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the bypass env set, no builder call, no app.state population."""
    from llm_tts_api import main as main_module
    from llm_tts_api.main import TEST_BYPASS_ENV, create_app

    monkeypatch.setenv(TEST_BYPASS_ENV, "1")
    calls: list[bool] = []

    def _spy_builder() -> AppDependencies:  # pragma: no cover — must not run
        calls.append(True)
        return _stub_deps(FakeTTSService())

    monkeypatch.setattr(main_module, "build_default_dependencies", _spy_builder)

    app = create_app()
    with TestClient(app):
        pass

    assert calls == []
    # Bypass mode must leave EVERY lifespan-managed slot absent — not
    # populated-with-None, which is what the prior `or X is None` clause
    # silently allowed. Test fixtures are the only thing that should set
    # these slots in bypass mode (and they explicitly do so).
    for slot in (
        "settings",
        "device_profile",
        "provider_selection",
        "model_registry",
        "provider_registry",
        "model_cache",
        "tts_service",
        "stt_service",
    ):
        assert not hasattr(app.state, slot), f"slot {slot!r} unexpectedly populated"
