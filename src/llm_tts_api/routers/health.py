"""Liveness + readiness endpoints (S-010).

``/health`` is a lock-free liveness probe (FR-HL-01): always 200, never
blocks on any singleton, reports the boot-time provider/device pair plus
S-007 queue/concurrency depth and S-008 cached models.

``/ready`` (FR-HL-02) is the orchestrator readiness gate. It returns 200
only after the lifespan flipped ``app.state.ready`` to True (warmup +
preload complete); during warmup, shutdown drain, or any irrecoverable
startup error it returns 503 with a structured ``{ready, reason}`` body.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


def _package_version() -> str:
    try:
        return importlib.metadata.version("llm-tts-api")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0"


def _semaphore_used(sem: asyncio.Semaphore | None, capacity: int) -> int:
    """Return capacity - available, clamped at 0.

    Reads ``Semaphore._value`` (the internal available-permits counter).
    CPython 3.10..3.13 keeps this stable; the alternative (subclassing the
    semaphore to track admits) was rejected upstream in S-007 (see
    sprint-impl-2.md Service Interface). Returns 0 when the slot is unset
    (test-bypass mode) so the liveness probe never raises.
    """
    if sem is None or capacity <= 0:
        return 0
    available = getattr(sem, "_value", capacity)
    used = capacity - int(available)
    return used if used > 0 else 0


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    """Lock-free liveness probe (FR-HL-01).

    Always returns 200. Body fields: ``status``, ``version``, ``device``,
    ``dtype``, ``provider``, ``provider_source``, ``model_loaded``,
    ``queue_depth``, ``concurrent_active``. Missing optional slots (test
    bypass) are reported as best-effort defaults — the probe MUST NOT
    fail merely because lifespan was skipped.
    """
    state = request.app.state
    body: dict[str, Any] = {"status": "ok", "version": _package_version()}

    selection = getattr(state, "provider_selection", None)
    if selection is not None:
        body["provider"] = selection.provider_name
        body["provider_source"] = selection.source
        body["device"] = selection.device

    device_profile = getattr(state, "device_profile", None)
    if device_profile is not None:
        body.setdefault("device", device_profile.device)
        body["dtype"] = device_profile.dtype

    settings = getattr(state, "settings", None)
    model_cache = getattr(state, "model_cache", None)
    if model_cache is not None:
        body["model_loaded"] = [
            f"{provider}:{model}" for provider, model in model_cache.loaded_keys()
        ]
    else:
        body["model_loaded"] = []

    queue_capacity = getattr(settings, "tts_max_queue_depth", 0) if settings is not None else 0
    concurrent_capacity = (
        getattr(settings, "tts_max_concurrent_requests", 0) if settings is not None else 0
    )
    body["queue_depth"] = _semaphore_used(
        getattr(state, "queue_semaphore", None), int(queue_capacity)
    )
    body["concurrent_active"] = _semaphore_used(
        getattr(state, "concurrency_semaphore", None), int(concurrent_capacity)
    )

    return body


@router.get("/ready")
def ready(request: Request) -> JSONResponse:
    """Readiness gate (FR-HL-02).

    Returns 200 ``{"status": "ready"}`` once ``app.state.ready`` is True.
    Otherwise 503 with ``{ready: false, reason: "warming_up"}`` (or
    ``"draining"`` once the lifespan finally-block clears the flag).
    """
    state = request.app.state
    if getattr(state, "ready", False):
        return JSONResponse(status_code=200, content={"status": "ready"})
    reason = getattr(state, "ready_reason", "warming_up")
    return JSONResponse(status_code=503, content={"ready": False, "reason": reason})
