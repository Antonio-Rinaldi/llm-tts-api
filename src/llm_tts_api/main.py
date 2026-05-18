"""Application factory + lifespan.

The lifespan is the only seam where process-wide singletons are constructed
(S-003 / FR-HL-03). All routers consume them via ``Depends(get_*)`` which
reads from ``app.state``. No module-level ``@lru_cache`` factories survive.

Tests bypass lifespan construction by setting ``LLM_TTS_API_TEST_NO_LIFESPAN=1``
in the environment; in that mode lifespan exits without touching ``app.state``
and the test fixture injects whichever fakes it wants (see ``tests/conftest.py``).
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from llm_tts_api.app_logging import setup_logging
from llm_tts_api.dependencies import build_default_dependencies
from llm_tts_api.errors import (
    OpenAIHTTPException,
    http_exception_handler,
    openai_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from llm_tts_api.observability import RequestIDMiddleware
from llm_tts_api.routers.audio import router as audio_router
from llm_tts_api.routers.chat import router as chat_router
from llm_tts_api.routers.health import router as health_router
from llm_tts_api.routers.models import router as models_router
from llm_tts_api.routers.realtime import router as realtime_router

logger = logging.getLogger(__name__)

TEST_BYPASS_ENV = "LLM_TTS_API_TEST_NO_LIFESPAN"


def _emit_low_memory_warning(threshold_gb: int) -> None:
    """FR-HL-05: emit a single WARNING log line if free memory is below floor.

    Soft check — never raises, never blocks startup. ``threshold_gb=0``
    disables the probe. ``psutil`` import failures degrade silently (the
    check is SHOULD, not MUST).
    """
    if threshold_gb <= 0:
        return
    try:
        import psutil  # type: ignore[import-untyped]
    except ImportError:
        logger.debug("psutil not installed; skipping low-memory probe")
        return
    try:
        free_bytes = int(psutil.virtual_memory().available)
    except Exception:  # noqa: BLE001 — defensive: the check is best-effort
        logger.debug("psutil.virtual_memory() failed; skipping low-memory probe")
        return
    free_gb = free_bytes / (1024**3)
    threshold_bytes = threshold_gb * (1024**3)
    if free_bytes < threshold_bytes:
        logger.warning(
            "low_memory_at_startup threshold_gb=%d free_gb=%.2f",
            threshold_gb,
            free_gb,
        )


async def _drain_concurrency(app: FastAPI, drain_seconds: int) -> None:
    """Wait for in-flight synthesis to finish, up to ``drain_seconds`` (FR-HL-04).

    "Finished" means the concurrency semaphore is fully available again
    (i.e. ``_value == capacity``). We poll on a short interval rather than
    re-acquiring the semaphore because re-acquiring would race with a
    queued waiter; passive observation is enough for graceful drain.
    """
    sem = getattr(app.state, "concurrency_semaphore", None)
    settings = getattr(app.state, "settings", None)
    if sem is None or settings is None:
        return
    capacity = int(getattr(settings, "tts_max_concurrent_requests", 0) or 0)
    if capacity <= 0:
        return
    if drain_seconds <= 0:
        return
    logger.info("shutdown: draining in-flight requests up to %ds", drain_seconds)
    deadline = asyncio.get_running_loop().time() + drain_seconds
    while True:
        available = int(getattr(sem, "_value", capacity))
        if available >= capacity:
            logger.info("shutdown: drain complete")
            return
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            logger.warning(
                "shutdown: drain timed out after %ds (in_flight=%d)",
                drain_seconds,
                capacity - available,
            )
            return
        await asyncio.sleep(min(0.05, remaining))


def _load_env_file(path: Path) -> None:
    """Load key/value pairs from a dotenv-style file into ``os.environ``."""
    if not path.exists() or not path.is_file():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ[key] = value


def _load_default_env_files() -> None:
    """Load project-level ``.env`` and ``.env.local`` files when present."""
    project_root = Path(__file__).resolve().parents[2]
    _load_env_file(project_root / ".env")
    _load_env_file(project_root / ".env.local")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    setup_logging(os.getenv("APP_LOG_LEVEL", "INFO"))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Construct process-wide singletons and stash them on ``app.state``.

        S-010 lifecycle: ``app.state.ready`` flips True only after the
        full dependency graph is wired (warmup + preload complete). On
        shutdown the ``finally`` block clears the flag, sets the reason
        to ``draining``, and waits up to ``TTS_SHUTDOWN_DRAIN_SECONDS``
        for active synthesis to release the concurrency semaphore.

        When ``LLM_TTS_API_TEST_NO_LIFESPAN`` is truthy, skip construction —
        the test fixture is responsible for populating ``app.state`` with
        whatever fakes/stubs it needs.
        """
        drain_seconds = 0
        try:
            if not _test_bypass_active():
                deps = build_default_dependencies()
                app.state.settings = deps.settings
                app.state.device_profile = deps.device_profile
                app.state.provider_selection = deps.provider_selection
                app.state.model_registry = deps.model_registry
                app.state.provider_registry = deps.provider_registry
                app.state.model_cache = deps.model_cache
                app.state.tts_service = deps.tts_service
                app.state.stt_service = deps.stt_service
                # S-007 producer slots (consumed by S-010 /health for queue_depth
                # and concurrent_active fields). See sprint-impl-2 Service Interface.
                app.state.concurrency_semaphore = deps.concurrency_semaphore
                app.state.queue_semaphore = deps.queue_semaphore
                app.state.model_locks = deps.model_locks
                _emit_low_memory_warning(deps.settings.tts_min_free_memory_gb)
                drain_seconds = deps.settings.tts_shutdown_drain_seconds
                app.state.ready = True
                app.state.ready_reason = "ready"
            yield
        finally:
            app.state.ready = False
            app.state.ready_reason = "draining"
            if drain_seconds > 0:
                await _drain_concurrency(app, drain_seconds)

    app = FastAPI(title="llm-tts-api", lifespan=lifespan)
    # FR-HL-02: /ready must return 503 from process start until the lifespan
    # finishes warmup. Initialize the flag here so the very first probe
    # (which can race the lifespan startup task) sees False, not AttributeError.
    app.state.ready = False
    app.state.ready_reason = "warming_up"
    app.add_middleware(RequestIDMiddleware)

    # S-009 error envelope handlers. Order matters: register the most-specific
    # handler (OpenAIHTTPException) first so it wins over the broader
    # ``HTTPException`` handler that catches bare 404s from the router.
    app.add_exception_handler(OpenAIHTTPException, openai_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    app.include_router(health_router)
    app.include_router(models_router)
    app.include_router(audio_router)
    app.include_router(chat_router)
    app.include_router(realtime_router)
    return app


def _test_bypass_active() -> bool:
    """Return ``True`` when the lifespan should skip singleton construction.

    Test bypass is signalled by ``LLM_TTS_API_TEST_NO_LIFESPAN`` being a
    truthy string (``1``, ``true``, ``yes`` — case-insensitive). Anything
    else, including unset, runs the real lifespan.
    """
    raw = os.environ.get(TEST_BYPASS_ENV, "").strip().lower()
    return raw in {"1", "true", "yes"}


def run() -> None:
    """Run the API server with uvicorn default local settings.

    `.env` / `.env.local` are loaded HERE (the CLI entry) rather than at
    module-import time. Library-import callers (tests, tools) get the
    actual process env unchanged, which keeps `monkeypatch.setenv` and CI
    env vars authoritative.
    """
    _load_default_env_files()
    uvicorn.run(
        "llm_tts_api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level=os.getenv("APP_LOG_LEVEL", "INFO").lower(),
    )


# Module-level app is required for `uvicorn llm_tts_api.main:app` style
# launches AND for the ``run()`` CLI which uses the same module-path string.
# Env-file loading is deliberately NOT done here — see ``run()``.
app = create_app()


if __name__ == "__main__":
    run()
