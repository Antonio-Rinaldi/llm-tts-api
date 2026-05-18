"""Health/readiness/lifecycle endpoint tests (S-010).

Covers UAT-HL-01..05 plus the regression suite carried forward from
S-003 (``/ready`` 200 vs. 503) and S-006 (``/health`` provider self-report).
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["provider"] == "mlx_audio"
    assert body["provider_source"] == "auto"
    assert body["device"] == "cpu"
    # S-010: new required body fields.
    assert "version" in body
    assert "dtype" in body
    assert body["model_loaded"] == []
    assert body["queue_depth"] == 0
    assert body["concurrent_active"] == 0


def test_health_without_provider_selection_still_returns_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Liveness probe must never 5xx (FR-HL-01) — even with no state slots."""
    from llm_tts_api.main import TEST_BYPASS_ENV, create_app

    monkeypatch.setenv(TEST_BYPASS_ENV, "1")
    app = create_app()
    # Intentionally do NOT populate any optional slot.

    with TestClient(app) as test_client:
        response = test_client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["model_loaded"] == []
    assert body["queue_depth"] == 0
    assert body["concurrent_active"] == 0


# ---------------------------------------------------------------------------
# UAT-HL-01 — /health is always 200, including under in-flight load.
# ---------------------------------------------------------------------------
def test_uat_hl_01_health_reports_queue_and_concurrent_under_load(
    client: TestClient,
) -> None:
    """UAT-HL-01: /health surfaces queue_depth + concurrent_active while a
    synthesis is in flight (simulated by manually consuming semaphore permits)."""
    app = client.app
    sem = app.state.concurrency_semaphore  # type: ignore[attr-defined]
    queue = app.state.queue_semaphore  # type: ignore[attr-defined]

    # Synchronously decrement the internal counter to simulate one in-flight
    # request. asyncio.Semaphore.acquire would require a running loop here,
    # so we drive _value directly — the same approach the live code reads.
    sem._value -= 1
    queue._value -= 1
    try:
        response = client.get("/health")
    finally:
        sem._value += 1
        queue._value += 1

    assert response.status_code == 200
    body = response.json()
    assert body["concurrent_active"] == 1
    assert body["queue_depth"] == 1


# ---------------------------------------------------------------------------
# UAT-HL-02 — /ready gates on warmup; flips True after lifespan yields.
# ---------------------------------------------------------------------------
def test_uat_hl_02_ready_returns_503_during_warmup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Before lifespan runs (or when ready=False), /ready must be 503 with
    the structured ``{ready, reason}`` body."""
    from llm_tts_api.main import TEST_BYPASS_ENV, create_app

    monkeypatch.setenv(TEST_BYPASS_ENV, "1")
    app = create_app()
    # create_app() sets ready=False, ready_reason="warming_up".
    # Probe BEFORE entering the lifespan context so we see the pre-yield state.
    from starlette.testclient import TestClient as RawClient

    # Manually do the probe with a request scope that does not run lifespan.
    raw = RawClient(app)
    # RawClient runs lifespan on context entry; bypass by calling the route
    # function via FastAPI's router directly is too invasive. Easiest: enter
    # the context (which sets ready=True in real lifespan, but bypass mode
    # SKIPS the construction block AND therefore SKIPS the ready=True flip).
    with raw as test_client:
        response = test_client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body == {"ready": False, "reason": "warming_up"}


def test_uat_hl_02_ready_returns_200_post_warmup(client: TestClient) -> None:
    """Once app.state.ready is True (fixture default), /ready is 200."""
    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


# ---------------------------------------------------------------------------
# UAT-HL-03 — Graceful shutdown drain waits on in-flight work.
# ---------------------------------------------------------------------------
def test_uat_hl_03_drain_waits_for_concurrency_semaphore_release() -> None:
    """``_drain_concurrency`` polls until the semaphore is fully released,
    then returns within the drain budget."""
    from llm_tts_api.main import _drain_concurrency

    async def scenario() -> tuple[float, float]:
        loop = asyncio.get_running_loop()
        sem = asyncio.Semaphore(2)
        sem._value -= 1  # Simulate one active request.
        fake_app = SimpleNamespace(
            state=SimpleNamespace(
                concurrency_semaphore=sem,
                settings=SimpleNamespace(tts_max_concurrent_requests=2),
            )
        )

        async def release_soon() -> None:
            await asyncio.sleep(0.1)
            sem._value += 1

        start = loop.time()
        release_task = asyncio.create_task(release_soon())
        await _drain_concurrency(cast(FastAPI, fake_app), drain_seconds=5)
        elapsed = loop.time() - start
        await release_task
        return elapsed, 5.0

    elapsed, budget = asyncio.run(scenario())
    assert elapsed < budget, f"drain should return immediately after release; took {elapsed:.2f}s"
    assert elapsed >= 0.1, "drain must wait for the in-flight release"


# ---------------------------------------------------------------------------
# UAT-HL-04 — Drain timeout forces exit when synthesis exceeds the budget.
# ---------------------------------------------------------------------------
def test_uat_hl_04_drain_times_out_when_synthesis_exceeds_budget(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When in-flight work never finishes within the drain budget,
    ``_drain_concurrency`` exits within ~budget seconds and logs a warning."""
    from llm_tts_api.main import _drain_concurrency

    async def scenario() -> float:
        loop = asyncio.get_running_loop()
        sem = asyncio.Semaphore(1)
        sem._value -= 1  # Pretend a synthesis is in flight and never releases.
        fake_app = SimpleNamespace(
            state=SimpleNamespace(
                concurrency_semaphore=sem,
                settings=SimpleNamespace(tts_max_concurrent_requests=1),
            )
        )

        start = loop.time()
        await _drain_concurrency(cast(FastAPI, fake_app), drain_seconds=1)
        return loop.time() - start

    caplog.set_level(logging.WARNING, logger="llm_tts_api.main")
    elapsed = asyncio.run(scenario())
    assert 0.9 <= elapsed <= 2.0, f"drain should bail near budget; took {elapsed:.2f}s"
    assert any("drain timed out" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# UAT-HL-05 — Low-memory warning at startup when threshold breached.
# ---------------------------------------------------------------------------
def test_uat_hl_05_low_memory_warning_emitted_when_threshold_exceeds_free(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A threshold larger than current free memory must emit a single
    WARNING line naming both the threshold and the observed free value."""
    from llm_tts_api.main import _emit_low_memory_warning

    caplog.set_level(logging.WARNING, logger="llm_tts_api.main")
    _emit_low_memory_warning(threshold_gb=10_000_000)

    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 1
    assert "low_memory_at_startup" in warns[0].message
    assert "threshold_gb=10000000" in warns[0].message


def test_uat_hl_05_low_memory_warning_disabled_when_threshold_zero(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``TTS_MIN_FREE_MEMORY_GB=0`` disables the probe — no log lines."""
    from llm_tts_api.main import _emit_low_memory_warning

    caplog.set_level(logging.WARNING, logger="llm_tts_api.main")
    _emit_low_memory_warning(threshold_gb=0)

    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []
