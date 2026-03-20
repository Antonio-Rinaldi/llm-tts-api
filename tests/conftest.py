import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = [
        "QWEN_TTS_MODEL_DEFAULT",
        "QWEN_TTS_MODEL_ALLOWED",
        "QWEN_STT_MODEL_DEFAULT",
        "QWEN_STT_MODEL_ALLOWED",
        "QWEN_TTS_VOICE_MAP_JSON",
        "QWEN_TTS_VOICE_MAP_FILE",
        "APP_NAME",
        "APP_ENV",
        "APP_LOG_LEVEL",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def client() -> Iterator[TestClient]:
    from qwen_tts_api.main import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
