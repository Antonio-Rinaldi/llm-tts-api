from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness probe used by process supervisors."""
    return {"status": "ok"}


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
