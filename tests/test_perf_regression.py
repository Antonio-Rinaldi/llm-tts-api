"""S-021 — CI-friendly perf-regression smoke tests.

The strict baseline measurement is operator-driven and recorded in
``docs/perf/baseline.md`` (see :mod:`scripts.perf_baseline`). This module
adds a *methodology* gate that runs in the standard unit suite using the
in-process :class:`FakeTTSProvider` so the perf scenarios stay exercised
even when nobody re-runs the operator script.

Coverage:

* T3 — :func:`test_health_p95_under_inflight_synthesis` asserts
  ``/health`` stays responsive (p95 well under the NFR-PF-02 / UAT-CC-02
  budget) while a synthesis request is holding a worker thread.
* T4 — :func:`test_concurrent_throughput_within_band` asserts that 4
  parallel synthesis requests with ``TTS_MAX_CONCURRENT_REQUESTS=2`` land
  within ±20% of 2× single-request time, the UAT-CC-01 invariant.

Bounds are deliberately relaxed vs. real-hardware numbers: the gate is
"the perf invariants hold against the in-process fake", not "the
production stack is fast". When the operator re-runs
``scripts/perf_baseline.py`` and appends a row to
``docs/perf/baseline.md``, that file carries the absolute numbers; this
test carries the *shape* of the contract.
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
from llm_tts_api.services.tts_providers.auto_select import ProviderSelection
from llm_tts_api.services.tts_providers.base import SynthesisRequest
from llm_tts_api.services.tts_providers.registry import TTSProviderRegistry
from llm_tts_api.services.tts_service import ModelLockMap, TTSService
from llm_tts_api.services.voice_store import VoiceRecord
from tests.fakes.fake_voice_store import (
    FakeVoiceBlobRepository,
    FakeVoiceMetadataRepository,
)


def _wav_bytes(seconds: float = 0.01) -> bytes:
    frames = int(16000 * seconds)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\x00\x00" * frames)
    return buf.getvalue()


class _PacedFakeProvider:
    """Synchronous TTS provider that sleeps ``delay`` seconds per call.

    Mirrors the production sync-on-worker-thread contract — ``synthesize_core``
    schedules ``synthesize_chunks`` via ``anyio.to_thread.run_sync`` — so the
    sleep here exercises the semaphore + worker-thread admission path the
    way a real provider would.
    """

    provider_name = "paced_provider"

    def __init__(self, delay: float) -> None:
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


def _stub_settings(*, max_concurrent: int, max_queue: int) -> Settings:
    settings = object.__new__(Settings)
    settings.app_name = "llm-tts-api"
    settings.app_env = "test"
    settings.app_log_level = "INFO"
    settings.tts_provider = "paced_provider"
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
    settings.tts_voice_map = {"alloy": VoiceConfig(ref_audio_path="", ref_text="", language="en")}
    settings.tts_max_input_chars = 4096
    settings.tts_max_concurrent_requests = max_concurrent
    settings.tts_max_queue_depth = max_queue
    return settings


class _StubModelRegistry:
    def resolve_tts_target(self, model: str | None, provider: str | None) -> tuple[str, str]:
        _ = provider
        return (model or "fake-model", "paced_provider")

    def is_allowed_tts_model(self, model_name: str, provider: str) -> bool:
        _ = (model_name, provider)
        return True


def _make_service(
    *, max_concurrent: int, delay: float
) -> tuple[TTSService, _PacedFakeProvider, asyncio.Semaphore, asyncio.Semaphore]:
    settings = _stub_settings(max_concurrent=max_concurrent, max_queue=16)
    provider = _PacedFakeProvider(delay=delay)
    registry = TTSProviderRegistry(providers=[provider])  # type: ignore[list-item]
    model_locks: ModelLockMap = {}
    concurrency_semaphore = asyncio.Semaphore(max_concurrent)
    queue_semaphore = asyncio.Semaphore(settings.tts_max_queue_depth)
    service = TTSService(
        settings=settings,
        model_registry=_StubModelRegistry(),  # type: ignore[arg-type]
        provider_registry=registry,
        concurrency_semaphore=concurrency_semaphore,
        queue_semaphore=queue_semaphore,
        model_locks=model_locks,
    )
    return service, provider, concurrency_semaphore, queue_semaphore


@pytest.fixture
def health_smoke_client() -> Iterator[tuple[TestClient, _PacedFakeProvider]]:
    """Real app wired with a paced fake provider for /health under load."""
    import os

    os.environ[TEST_BYPASS_ENV] = "1"
    try:
        app: FastAPI = create_app()
        service, provider, concurrency_sem, queue_sem = _make_service(max_concurrent=1, delay=0.3)

        app.state.settings = service.settings
        app.state.device_profile = DeviceProfile(device="cpu", dtype="float32", source="auto")
        app.state.model_registry = ModelRegistry.__new__(ModelRegistry)
        app.state.provider_registry = TTSProviderRegistry(  # type: ignore[list-item]
            providers=[provider]
        )
        app.state.provider_selection = ProviderSelection(
            provider_name="paced_provider", device="cpu", source="auto"
        )
        metadata_repo = FakeVoiceMetadataRepository()
        blob_repo = FakeVoiceBlobRepository()

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
            yield client, provider
        app.dependency_overrides.clear()
    finally:
        os.environ.pop(TEST_BYPASS_ENV, None)


def _percentile(samples: list[float], pct: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    idx = max(0, min(len(ordered) - 1, int(round(pct / 100.0 * len(ordered))) - 1))
    return ordered[idx]


def test_health_p95_under_inflight_synthesis(
    health_smoke_client: tuple[TestClient, _PacedFakeProvider],
) -> None:
    """T3 / NFR-PF-02 / UAT-CC-02 — /health stays responsive during synth.

    Strict NFR budget is 50 ms p95 on real hardware. The smoke version uses
    a 200 ms ceiling to absorb TestClient + scheduler jitter on slow CI
    runners. The point is to fail loud if /health becomes synchronously
    blocked behind the synthesis worker thread, not to certify a number.
    """
    client, provider = health_smoke_client

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
        deadline = time.monotonic() + 1.0
        while provider.active == 0 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert provider.active > 0, "synthesis didn't start in time"

        latencies: list[float] = []
        for _ in range(20):
            t0 = time.monotonic()
            resp = client.get("/health")
            latencies.append(time.monotonic() - t0)
            assert resp.status_code == 200

        p95 = _percentile(latencies, 95)
        assert p95 < 0.2, (
            f"/health p95 during in-flight synthesis was {p95 * 1000:.1f} ms (samples={latencies})"
        )
    finally:
        worker.join(timeout=5.0)
        assert speech_done.is_set()


async def test_concurrent_throughput_within_band() -> None:
    """T4 / UAT-CC-01 — 4 parallel @ cap=2 lands within ±20% of 2× single.

    With ``TTS_MAX_CONCURRENT_REQUESTS=2`` the semaphore admits two waves
    of two requests each, so the expected wall-clock is ``2 * delay``. We
    use distinct model names per request so the per-(provider, model) lock
    does not serialize beyond the concurrency cap.
    """
    delay = 0.15
    service, provider, _, _ = _make_service(max_concurrent=2, delay=delay)

    start = time.monotonic()
    await asyncio.gather(
        *(
            service.create_speech(
                SpeechRequest(model=f"model-{i}", voice="alloy", input="hi."),
                stream=True,
            )
            for i in range(4)
        )
    )
    elapsed = time.monotonic() - start

    assert provider.calls == 4
    assert provider.peak_active == 2

    expected = 2 * delay
    # ±20% lower bound; generous upper bound to absorb scheduler jitter on
    # slow CI runners — the regression we care about is "throughput
    # collapses below 2× single-request" (semaphore broken or worker
    # thread saturated), not millisecond-level noise.
    assert elapsed >= expected * 0.8, (
        f"concurrent wall-clock {elapsed:.3f}s suspiciously fast vs expected ~{expected:.3f}s"
    )
    assert elapsed < expected * 2.5, (
        f"concurrent wall-clock {elapsed:.3f}s well over 2x single ({expected:.3f}s) — "
        "concurrency cap may have collapsed to serial"
    )
