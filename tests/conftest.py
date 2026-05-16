import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tests.fakes.fake_tts_service import FakeTTSService  # noqa: E402  (path setup above)


@pytest.fixture(autouse=True)
def clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = [
        "TTS_PROVIDER",
        "TTS_MLX_AUDIO_MODEL_DEFAULT",
        "TTS_MLX_AUDIO_MODEL_ALLOWED",
        "TTS_VOXTRAL_MODEL_DEFAULT",
        "TTS_VOXTRAL_MODEL_ALLOWED",
        "TTS_VLLM_OMNI_MODEL_DEFAULT",
        "TTS_VLLM_OMNI_MODEL_ALLOWED",
        "STT_MODEL_DEFAULT",
        "STT_MODEL_ALLOWED",
        "TTS_MAX_INPUT_CHARS",
        "TTS_VOICE_MAP_FILE",
        "APP_NAME",
        "APP_ENV",
        "APP_LOG_LEVEL",
    ]
    for key in keys:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def fake_tts_service() -> FakeTTSService:
    """Default fake — happy-path speech returning stub WAV bytes."""
    return FakeTTSService()


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch, fake_tts_service: FakeTTSService
) -> Iterator[TestClient]:
    """``TestClient`` wired with a typed ``FakeTTSService`` on two seams.

    Two seams have to be covered because ``llm_tts_api`` resolves
    ``get_tts_service`` in two distinct ways:

    1. **Lifespan (attribute access)** — ``main.py:lifespan`` does
       ``from llm_tts_api import dependencies`` then
       ``dependencies.get_tts_service()`` at startup to force preload.
       ``monkeypatch.setattr(dependencies, "get_tts_service", …)`` swaps the
       attribute on the module so that direct call yields the fake.

    2. **Route handler (FastAPI Depends)** — routers do
       ``from llm_tts_api.dependencies import get_tts_service`` then
       ``Depends(get_tts_service)``. FastAPI captures the **original**
       callable at route-registration time; the monkeypatch above does NOT
       reach it. ``app.dependency_overrides[get_tts_service] = …`` is
       consulted at request time and correctly returns the fake.

    Both seams pointing at the same ``fake_tts_service`` keeps the test
    surface coherent: assertions on ``fake_tts_service.calls`` reflect every
    invocation, regardless of whether it came from lifespan or a route.

    Also clears the ``get_tts_service`` lru_cache around the test so each
    case starts from a clean resolution.
    """
    from llm_tts_api import dependencies
    from llm_tts_api.main import create_app

    # IMPORTANT: capture the ORIGINAL ``get_tts_service`` reference BEFORE
    # the monkeypatch swaps it out. Routers do
    # ``from llm_tts_api.dependencies import get_tts_service`` at import
    # time and pass THAT reference into ``Depends()``; FastAPI uses it as
    # the dictionary key for ``app.dependency_overrides``. If we instead
    # used the monkeypatched lambda as the key, the override would never
    # match the router's Depends and the real service would be resolved.
    original_get_tts_service = dependencies.get_tts_service

    # ``get_tts_service`` is an ``lru_cache``-wrapped function at module
    # level. Clear it before the monkeypatch so stale singletons from a
    # previous test don't leak into route resolution.
    _safely_cache_clear(original_get_tts_service)
    monkeypatch.setattr(dependencies, "get_tts_service", lambda: fake_tts_service)

    app = create_app()
    app.dependency_overrides[original_get_tts_service] = lambda: fake_tts_service
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        # monkeypatch teardown will restore the original lru_cache wrapper
        # after this fixture exits; nothing to clear here.


def _safely_cache_clear(maybe_cached: object) -> None:
    """Call ``cache_clear`` if present (the function is an lru_cache wrapper)."""
    fn = getattr(maybe_cached, "cache_clear", None)
    if callable(fn):
        fn()