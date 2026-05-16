"""Startup preload tests.

These exercise the lifespan path that calls ``dependencies.get_tts_service()``
directly (attribute access on the module). They still use ``monkeypatch``
because the FastAPI ``app.dependency_overrides`` mechanism does NOT intercept
attribute-access calls — only ``Depends`` resolution at request time. The
**payload** has been migrated to the typed ``FakeTTSService`` so any future
interface change surfaces as a typed error rather than an opaque
``object()`` substitution.
"""

import pytest
from fastapi.testclient import TestClient


def test_startup_preloads_tts_service(monkeypatch: pytest.MonkeyPatch) -> None:
    from llm_tts_api import dependencies
    from llm_tts_api.main import create_app
    from tests.fakes.fake_tts_service import FakeTTSService

    calls: list[bool] = []
    fake = FakeTTSService()

    def _fake_get_tts_service() -> FakeTTSService:
        calls.append(True)
        return fake

    getattr(dependencies.get_tts_service, "cache_clear", lambda: None)()
    monkeypatch.setattr(dependencies, "get_tts_service", _fake_get_tts_service)

    app = create_app()
    with TestClient(app):
        pass

    assert calls == [True]


def test_startup_fails_fast_when_preload_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from llm_tts_api import dependencies
    from llm_tts_api.main import create_app

    def _failing_get_tts_service() -> object:
        raise RuntimeError("preload failed")

    getattr(dependencies.get_tts_service, "cache_clear", lambda: None)()
    monkeypatch.setattr(dependencies, "get_tts_service", _failing_get_tts_service)

    app = create_app()
    with pytest.raises(RuntimeError, match="preload failed"), TestClient(app):
        pass