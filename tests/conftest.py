"""Shared test fixtures.

Post-S-003 design: the lifespan refactor moved singletons onto ``app.state``,
so the test fixture now uses a single, simple mechanism:

1. Set ``LLM_TTS_API_TEST_NO_LIFESPAN=1`` so lifespan does NOT construct the
   real dependency graph (no model loads, no env-driven Settings parsing,
   no provider preload).
2. Build the app, then **manually populate** every ``app.state`` slot that
   any router or health endpoint reads. The TTS service slot points at the
   typed ``FakeTTSService``; the others are minimal stubs that satisfy
   attribute access without triggering heavy construction.
3. Wire ``app.dependency_overrides[get_tts_service] = lambda: fake`` so
   FastAPI ``Depends(get_tts_service)`` calls in routers also resolve to
   the fake.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tests.fakes.fake_tts_service import FakeTTSService  # noqa: E402  (path setup above)


@pytest.fixture(autouse=True)
def clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset env vars that ``Settings.__post_init__`` reads.

    ``LLM_TTS_API_TEST_NO_LIFESPAN`` is NOT cleared here — fixtures that
    need bypass set it themselves; tests that exercise real lifespan
    construction explicitly leave it unset.
    """
    keys = [
        "TTS_PROVIDER",
        "TTS_MLX_AUDIO_MODEL_DEFAULT",
        "TTS_MLX_AUDIO_MODEL_ALLOWED",
        "TTS_VOXTRAL_MODEL_DEFAULT",
        "TTS_VOXTRAL_MODEL_ALLOWED",
        "TTS_VLLM_OMNI_MODEL_DEFAULT",
        "TTS_VLLM_OMNI_MODEL_ALLOWED",
        "STT_MODEL_DEFAULT",
        "STT_MODEL_ALLOWED",
        "TTS_MAX_INPUT_CHARS",
        "TTS_VOICE_MAP_FILE",
        "TTS_DEVICE",
        "TTS_DTYPE",
        "TTS_MODEL_CACHE_SIZE",
        "TTS_PRELOAD_MODELS",
        "APP_NAME",
        "APP_ENV",
        "APP_LOG_LEVEL",
        "APP_LOG_FORMAT",
        "TTS_MAX_QUEUE_DEPTH",
        "TTS_MODEL_CACHE_SIZE",
        "TTS_PRELOAD_MODELS",
        "TTS_INFERENCE_TIMEOUT_SECONDS",
        "TTS_SHUTDOWN_DRAIN_SECONDS",
        "TTS_MIN_FREE_MEMORY_GB",
        "TTS_VOICE_STORE_DIR",
        "TTS_VOICE_METADATA_BACKEND",
        "TTS_VOICE_METADATA_DSN",
        "TTS_REFAUDIO_MAX_BYTES",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def fake_tts_service() -> FakeTTSService:
    """Default fake — happy-path speech returning stub WAV bytes."""
    return FakeTTSService()


def _stub_app_state(app_state: object, fake_tts: FakeTTSService) -> None:
    """Populate every ``app.state`` slot a router or health endpoint may read.

    The TTS service slot is the FakeTTSService; other slots are minimal
    stubs constructed without calling their heavy __init__ paths. Settings
    is built via ``object.__new__`` to skip ``__post_init__`` (which would
    try to parse env-driven config and read a voice map file).
    """
    from llm_tts_api.config import Settings
    from llm_tts_api.engine import DeviceProfile
    from llm_tts_api.services.model_cache import LRUModelCache
    from llm_tts_api.services.model_registry import ModelRegistry
    from llm_tts_api.services.stt_service import STTService
    from llm_tts_api.services.tts_providers.auto_select import ProviderSelection
    from llm_tts_api.services.tts_providers.registry import TTSProviderRegistry

    # Skip Settings.__post_init__ — that path parses env vars and requires
    # a real voice map file. Tests fill in only the attributes they need.
    settings = object.__new__(Settings)
    settings.app_name = "llm-tts-api"
    settings.app_env = "test"
    settings.app_log_level = "INFO"
    settings.tts_provider = "mlx_audio"
    settings.tts_model_default = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
    settings.tts_model_allowed = ["Qwen/Qwen3-TTS-12Hz-0.6B-Base"]
    settings.tts_mlx_audio_model_default = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
    settings.tts_mlx_audio_model_allowed = ["Qwen/Qwen3-TTS-12Hz-0.6B-Base"]
    settings.tts_voxtral_model_default = "mlx-community/Voxtral-4B-TTS-2603-mlx-4bit"
    settings.tts_voxtral_model_allowed = ["mlx-community/Voxtral-4B-TTS-2603-mlx-4bit"]
    settings.tts_vllm_omni_model_default = "vllm-omni/default-tts"
    settings.tts_vllm_omni_model_allowed = ["vllm-omni/default-tts"]
    settings.stt_model_default = "whisper-1"
    settings.stt_model_allowed = ["whisper-1"]
    settings.tts_voice_map = {}
    settings.tts_max_input_chars = 4096
    settings.tts_max_concurrent_requests = 1
    settings.tts_device = "auto"
    settings.tts_dtype = "auto"
    settings.tts_max_queue_depth = 8
    settings.tts_model_cache_size = 1
    settings.tts_preload_models = []
    settings.tts_inference_timeout_seconds = None
    settings.tts_shutdown_drain_seconds = 30
    settings.tts_min_free_memory_gb = 0
    settings.app_log_format = "text"
    settings.tts_voice_store_dir = Path("var/voices")
    settings.tts_voice_metadata_backend = "fs_json"
    settings.tts_voice_metadata_dsn = None
    settings.tts_voice_blob_backend = "fs"
    settings.tts_voice_blob_s3_endpoint = ""
    settings.tts_voice_blob_s3_bucket = ""
    settings.tts_voice_blob_s3_region = ""
    settings.tts_refaudio_max_bytes = 10 * 1024 * 1024

    app_state.settings = settings  # type: ignore[attr-defined]
    app_state.device_profile = DeviceProfile(  # type: ignore[attr-defined]
        device="cpu", dtype="float32", source="auto"
    )
    app_state.provider_selection = ProviderSelection(  # type: ignore[attr-defined]
        provider_name="mlx_audio", device="cpu", source="auto"
    )
    app_state.model_registry = ModelRegistry(settings)  # type: ignore[attr-defined]
    app_state.provider_registry = TTSProviderRegistry(providers=[])  # type: ignore[attr-defined]
    app_state.model_cache = LRUModelCache(max_size=1)  # type: ignore[attr-defined]
    app_state.tts_service = fake_tts  # type: ignore[attr-defined]
    app_state.stt_service = STTService()  # type: ignore[attr-defined]
    # S-007 slots — empty semaphores so /health can derive queue_depth /
    # concurrent_active without raising. Capacity matches the stub settings.
    import asyncio as _asyncio

    app_state.concurrency_semaphore = _asyncio.Semaphore(  # type: ignore[attr-defined]
        settings.tts_max_concurrent_requests
    )
    app_state.queue_semaphore = _asyncio.Semaphore(settings.tts_max_queue_depth)  # type: ignore[attr-defined]
    app_state.model_locks = {}  # type: ignore[attr-defined]
    # S-022 voice-store slots — use in-memory fakes so tests neither write
    # to disk nor depend on an actual TTS_VOICE_STORE_DIR layout.
    from tests.fakes.fake_voice_store import (
        FakeVoiceBlobRepository,
        FakeVoiceMetadataRepository,
    )

    app_state.voice_metadata_repo = FakeVoiceMetadataRepository()  # type: ignore[attr-defined]
    app_state.voice_blob_repo = FakeVoiceBlobRepository()  # type: ignore[attr-defined]
    # S-010: ready-flag default for the happy-path fixture. UAT-HL-02 toggles
    # this directly in test bodies that need the not-ready path.
    app_state.ready = True  # type: ignore[attr-defined]
    app_state.ready_reason = "ready"  # type: ignore[attr-defined]


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch, fake_tts_service: FakeTTSService
) -> Iterator[TestClient]:
    """``TestClient`` wired with stubbed app.state slots + a typed FakeTTSService.

    Bypass env ``LLM_TTS_API_TEST_NO_LIFESPAN=1`` skips real lifespan
    construction. We populate every ``app.state`` slot that routers read,
    then override the ``get_tts_service`` Depends so FastAPI's resolution
    machinery returns the fake too.
    """
    from llm_tts_api.dependencies import get_tts_service
    from llm_tts_api.main import TEST_BYPASS_ENV, create_app

    monkeypatch.setenv(TEST_BYPASS_ENV, "1")
    app = create_app()
    _stub_app_state(app.state, fake_tts_service)
    app.dependency_overrides[get_tts_service] = lambda: fake_tts_service

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
