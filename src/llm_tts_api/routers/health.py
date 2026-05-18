"""Liveness + readiness endpoints.

S-006 extends ``/health`` to report the auto-selected provider and the
source label (``auto`` for capability-derived, ``env`` for an explicit
``TTS_PROVIDER`` override). Per UAT-HW-04..05 operators rely on this field
to confirm the boot-time decision matches their expectations.

S-010 will expand this body with semaphore/queue depth fields; the
``provider`` / ``provider_source`` keys are stable from S-006 onward.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


@router.get("/health")
def health(request: Request) -> dict[str, str]:
    """Liveness probe + provider self-report.

    The probe always returns 200 once the process is up; consumers that
    need readiness gating call ``/ready`` instead. The body advertises the
    selected provider and its source so operators can verify
    auto-selection (FR-HW-04..07).
    """
    body: dict[str, str] = {"status": "ok"}
    selection = getattr(request.app.state, "provider_selection", None)
    if selection is not None:
        body["provider"] = selection.provider_name
        body["provider_source"] = selection.source
        body["device"] = selection.device
    return body


@router.get("/ready")
def ready(request: Request) -> JSONResponse:
    """Readiness probe used by orchestrators after startup.

    Reads ``app.state.tts_service`` directly: presence means lifespan finished
    constructing the dependency graph (or a test fixture has injected one).
    Absence (or any other error reading it) signals "not ready".
    """
    try:
        service = getattr(request.app.state, "tts_service", None)
        if service is None:
            raise RuntimeError("tts_service not initialized on app.state")
        return JSONResponse(status_code=200, content={"status": "ready"})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=503, content={"status": "degraded", "detail": str(exc)})
