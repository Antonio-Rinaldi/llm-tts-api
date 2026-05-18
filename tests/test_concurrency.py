"""S-007 concurrency-model tests (UAT-CC-01..03, NFR-PF-02).

The synthesis pipeline runs sync provider calls on a worker thread
(``anyio.to_thread.run_sync``) and admits via two semaphores stashed on
``app.state`` (the producer slots for S-010 / ``/health``):

* ``concurrency_semaphore`` — active in-flight cap (UAT-CC-01).
* ``queue_semaphore`` — total admission cap; overflow → 429 (UAT-CC-03).

These tests build a TTSService against a fake provider that sleeps,
then drive it with concurrent ``asyncio.gather`` calls. ``/health`` is
hit while a synthesis is in flight to assert event-loop responsiveness
(UAT-CC-02).
"""

from __future__ import annotations

import asyncio
import io
import threading
import time
import wave
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from llm_tts_api.config import Settings, VoiceConfig
from llm_tts_api.engine import DeviceProfile
from llm_tts_api.main import TEST_BYPASS_ENV, create_app
from llm_tts_api.schemas.speech import SpeechRequest
from llm_tts_api.services.model_registry import ModelRegistry
from llm_tts_api.services.stt_service import STTService
from llm_tts_api.services.tts_providers.base import SynthesisRequest
from llm_tts_api.services.tts_providers.registry import TTSProviderRegistry
from llm_tts_api.services.tts_service import ModelLockMap, TTSService


def _wav_bytes(seconds: float = 0.01) -> bytes:
    frames = int(16000 * seconds)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\x00\x00" * frames)
    return buf.getvalue()


class _SlowProvider:
    """Synchronous provider that blocks a worker thread for ``delay`` seconds."""

    provider_name = "slow_provider"

    def __init__(self, delay: float = 0.2) -> None:
        self.delay = delay
        self.active = 0
        self.peak_active = 0
        self._lock = threading.Lock()
        self.calls = 0

    def synthesize_chunks(self, request: SynthesisRequest) -> list[bytes]:
        _ = request
        with self._lock:
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
            self.calls += 1
        try:
            time.sleep(self.delay)
        finally:
            with self._lock:
                self.active -= 1
        return [_wav_bytes()]


def _stub_settings(max_concurrent: int, max_queue: int) -> Settings:
    """Build a Settings instance bypassing ``__post_init__`` env reads."""
    settings = object.__new__(Settings)
    settings.app_name = "llm-tts-api"
    settings.app_env = "test"
    settings.app_log_level = "INFO"
    settings.tts_provider = "slow_provider"
    settings.tts_model_default = "fake-model"
    settings.tts_model_allowed = ["fake-model"]
    settings.tts_mlx_audio_model_default = "fake-model"
    settings.tts_mlx_audio_model_allowed = ["fake-model"]
    settings.tts_voxtral_model_default = "fake-model"
    settings.tts_voxtral_model_allowed = ["fake-model"]
    settings.tts_vllm_omni_model_default = "fake-model"
    settings.tts_vllm_omni_model_allowed = ["fake-model"]
    settings.stt_model_default = "whisper-1"
    settings.stt_model_allowed = ["whisper-1"]
    settings.tts_voice_map = {
        "alloy": VoiceConfig(
            ref_audio_path="",
            ref_text="",
            language="en",
        )
    }
    settings.tts_max_input_chars = 4096
    settings.tts_max_concurrent_requests = max_concurrent
    settings.tts_max_queue_depth = max_queue
    return settings


class _StubModelRegistry:
    """Minimal stand-in that echoes the requested model under the slow provider."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def resolve_tts_target(self, model: str | None, provider: str | None) -> tuple[str, str]:
        _ = provider
        return (model or "fake-model", "slow_provider")

    def is_allowed_tts_model(self, model_name: str, provider: str) -> bool:
        _ = (model_name, provider)
        return True


def _make_service(
    *,
    max_concurrent: int,
    max_queue: int,
    delay: float = 0.2,
) -> tuple[TTSService, _SlowProvider, asyncio.Semaphore, asyncio.Semaphore]:
    settings = _stub_settings(max_concurrent=max_concurrent, max_queue=max_queue)
    provider = _SlowProvider(delay=delay)
    registry = TTSProviderRegistry(providers=[provider])  # type: ignore[list-item]
    model_registry = _StubModelRegistry(settings)
    concurrency_semaphore = asyncio.Semaphore(max_concurrent)
    queue_semaphore = asyncio.Semaphore(max_queue)
    model_locks: ModelLockMap = {}
    service = TTSService(
        settings=settings,
        model_registry=model_registry,  # type: ignore[arg-type]
        provider_registry=registry,
        concurrency_semaphore=concurrency_semaphore,
        queue_semaphore=queue_semaphore,
        model_locks=model_locks,
    )
    return service, provider, concurrency_semaphore, queue_semaphore


def _make_request(model: str = "fake-model") -> SpeechRequest:
    return SpeechRequest(model=model, voice="alloy", input="hello world.")


async def test_concurrency_cap_limits_parallelism_uat_cc_01() -> None:
    """UAT-CC-01: 4 parallel requests with cap=2 → peak active == 2.

    Uses distinct model names so the per-(provider, model) lock (T3) does
    not serialize the requests itself — exercising the concurrency cap as
    the binding constraint.
    """
    service, provider, _, _ = _make_service(max_concurrent=2, max_queue=8, delay=0.15)

    start = time.monotonic()
    results = await asyncio.gather(
        *(service.create_speech(_make_request(f"model-{i}"), stream=True) for i in range(4))
    )
    elapsed = time.monotonic() - start

    assert len(results) == 4
    assert provider.calls == 4
    assert provider.peak_active == 2, f"expected peak 2 active, saw {provider.peak_active}"
    # 4 reqs / cap 2 == 2 waves * 0.15s = ~0.30s; allow generous upper bound.
    assert elapsed < 0.7, f"4 reqs at cap=2 should run in ~2 waves, took {elapsed:.3f}s"


async def test_per_model_lock_serializes_same_model_calls() -> None:
    """T3: per-(provider, model) lock prevents two concurrent calls on one model.

    Different models can run concurrently under the concurrency cap, but the
    same model must not be inferred twice in parallel — the loaded model is
    not thread-safe at the provider layer.
    """
    service, provider, _, _ = _make_service(max_concurrent=4, max_queue=8, delay=0.1)

    await asyncio.gather(
        *(service.create_speech(_make_request("same-model"), stream=True) for _ in range(3))
    )

    assert provider.calls == 3
    assert provider.peak_active == 1, (
        f"same-model calls must serialize; saw peak_active={provider.peak_active}"
    )


async def test_queue_full_returns_429_uat_cc_03() -> None:
    """UAT-CC-03: when queue_semaphore is exhausted, new admissions raise 429."""
    from llm_tts_api.errors import OpenAIHTTPException

    service, _, _, queue_semaphore = _make_service(max_concurrent=1, max_queue=2, delay=0.2)

    # Drain the queue semaphore so the next attempt sees it locked.
    await queue_semaphore.acquire()
    await queue_semaphore.acquire()
    assert queue_semaphore.locked()

    with pytest.raises(OpenAIHTTPException) as exc_info:
        await service.create_speech(_make_request(), stream=True)

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["code"] == "queue_full"  # type: ignore[index, call-overload]
    assert exc_info.value.detail["type"] == "capacity_error"  # type: ignore[index, call-overload]


@pytest.fixture
def real_app_client() -> Iterator[tuple[TestClient, FastAPI, _SlowProvider]]:
    """Build a real app with bypassed lifespan and a slow synth fake wired in.

    /health stays answerable by the FastAPI default endpoint while the slow
    provider holds the worker thread — this is the UAT-CC-02 setup.
    """
    import os

    os.environ[TEST_BYPASS_ENV] = "1"
    try:
        app = create_app()
        service, provider, concurrency_sem, queue_sem = _make_service(
            max_concurrent=1, max_queue=4, delay=0.3
        )
        app.state.settings = service.settings
        app.state.device_profile = DeviceProfile(device="cpu", dtype="float32", source="auto")
        app.state.model_registry = ModelRegistry.__new__(ModelRegistry)
        # S-017: ``/v1/audio/speech`` now delegates to ``synthesize_core`` which
        # needs a registry containing the slow provider, a provider_selection
        # pointing at it, and an in-memory voice store with "alloy" seeded so
        # the request flows past voice resolution into the slow provider.
        app.state.provider_registry = TTSProviderRegistry(providers=[provider])  # type: ignore[list-item]
        from llm_tts_api.services.tts_providers.auto_select import ProviderSelection

        app.state.provider_selection = ProviderSelection(
            provider_name="slow_provider", device="cpu", source="auto"
        )
        from tests.fakes.fake_voice_store import (
            FakeVoiceBlobRepository,
            FakeVoiceMetadataRepository,
        )

        metadata_repo = FakeVoiceMetadataRepository()
        blob_repo = FakeVoiceBlobRepository()
        from llm_tts_api.services.voice_store import VoiceRecord

        async def _seed() -> None:
            await metadata_repo.create(
                VoiceRecord(
                    id="alloy",
                    transcript="ref",
                    language="en",
                    consent_acknowledged=True,
                    source="crud",
                )
            )
            await blob_repo.put("alloy", _wav_bytes())

        asyncio.new_event_loop().run_until_complete(_seed())
        app.state.voice_metadata_repo = metadata_repo
        app.state.voice_blob_repo = blob_repo
        app.state.tts_service = service
        app.state.stt_service = STTService()
        app.state.concurrency_semaphore = concurrency_sem
        app.state.queue_semaphore = queue_sem
        app.state.model_locks = {}

        from llm_tts_api.dependencies import get_tts_service

        app.dependency_overrides[get_tts_service] = lambda: service
        with TestClient(app) as client:
            yield client, app, provider
        app.dependency_overrides.clear()
    finally:
        os.environ.pop(TEST_BYPASS_ENV, None)


def test_health_responsive_during_synthesis_uat_cc_02(
    real_app_client: tuple[TestClient, FastAPI, _SlowProvider],
) -> None:
    """UAT-CC-02 / NFR-PF-02: /health p95 stays low while synthesis is in flight."""
    client, _, provider = real_app_client

    # Fire a synthesis in a background thread (TestClient is sync) so the
    # event loop is busy processing it while we hit /health.
    speech_done = threading.Event()

    def _do_speech() -> None:
        try:
            client.post(
                "/v1/audio/speech?stream=true",
                json={"model": "fake-model", "voice": "alloy", "input": "hello there."},
            )
        finally:
            speech_done.set()

    worker = threading.Thread(target=_do_speech)
    worker.start()
    try:
        # Wait until synthesis has actually started (provider.active > 0).
        deadline = time.monotonic() + 1.0
        while provider.active == 0 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert provider.active > 0, "synthesis didn't start in time"

        latencies: list[float] = []
        for _ in range(5):
            t0 = time.monotonic()
            resp = client.get("/health")
            latencies.append(time.monotonic() - t0)
            assert resp.status_code == 200

        # All /health calls should be well under 50 ms — generous bound 200 ms
        # to absorb TestClient overhead on slow CI runners.
        assert max(latencies) < 0.2, f"/health latencies during synth: {latencies}"
    finally:
        worker.join(timeout=5.0)
        assert speech_done.is_set()


def test_no_threading_semaphore_in_synthesis_path() -> None:
    """The async refactor must retire ``threading.Semaphore`` from synthesis."""
    import inspect

    from llm_tts_api.services import tts_service as tts_service_module

    source = inspect.getsource(tts_service_module)
    assert "threading.Semaphore" not in source, (
        "threading.Semaphore must not appear in the synthesis path after S-007"
    )
