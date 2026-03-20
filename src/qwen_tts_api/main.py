from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from qwen_tts_api.errors import OpenAIHTTPException
from qwen_tts_api.routers.audio import router as audio_router
from qwen_tts_api.routers.chat import router as chat_router
from qwen_tts_api.routers.health import router as health_router
from qwen_tts_api.routers.models import router as models_router
from qwen_tts_api.routers.realtime import router as realtime_router


def _load_env_file(path: Path) -> None:
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
    project_root = Path(__file__).resolve().parents[2]
    _load_env_file(project_root / ".env.example")
    _load_env_file(project_root / ".env.example.local")


def create_app() -> FastAPI:
    app = FastAPI(title="qwen-tts-api")

    @app.exception_handler(OpenAIHTTPException)
    async def openai_exception_handler(_, exc: OpenAIHTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})

    app.include_router(health_router)
    app.include_router(models_router)
    app.include_router(audio_router)
    app.include_router(chat_router)
    app.include_router(realtime_router)
    return app


def run() -> None:
    uvicorn.run("qwen_tts_api.main:app", host="0.0.0.0", port=8000, reload=False)


_load_default_env_files()
app = create_app()


if __name__ == "__main__":
    run()

