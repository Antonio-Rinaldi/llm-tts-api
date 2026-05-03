from fastapi import APIRouter
from fastapi.responses import JSONResponse

from llm_tts_api import dependencies

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness probe used by process supervisors."""
    return {"status": "ok"}


@router.get("/ready")
def ready() -> JSONResponse:
    """Readiness probe used by orchestrators after startup."""
    try:
        dependencies.get_tts_service()
        return JSONResponse(status_code=200, content={"status": "ready"})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=503, content={"status": "degraded", "detail": str(exc)})
