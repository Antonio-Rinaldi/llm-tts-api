from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    """Liveness probe used by process supervisors."""
    return {"status": "ok"}


@router.get("/ready")
def ready() -> dict:
    """Readiness probe used by orchestrators after startup."""
    return {"status": "ready"}
