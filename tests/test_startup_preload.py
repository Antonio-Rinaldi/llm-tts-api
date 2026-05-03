import pytest
from fastapi.testclient import TestClient


def test_startup_preloads_tts_service(monkeypatch) -> None:
    from llm_tts_api import dependencies
    from llm_tts_api.main import create_app

    calls: list[bool] = []

    def _fake_get_tts_service():
        calls.append(True)
        return object()

    monkeypatch.setattr(dependencies, "get_tts_service", _fake_get_tts_service)

    app = create_app()
    with TestClient(app):
        pass

    assert calls == [True]


def test_startup_fails_fast_when_preload_fails(monkeypatch) -> None:
    from llm_tts_api import dependencies
    from llm_tts_api.main import create_app

    def _failing_get_tts_service():
        raise RuntimeError("preload failed")

    monkeypatch.setattr(dependencies, "get_tts_service", _failing_get_tts_service)

    app = create_app()
    with pytest.raises(RuntimeError, match="preload failed"), TestClient(app):
        pass

