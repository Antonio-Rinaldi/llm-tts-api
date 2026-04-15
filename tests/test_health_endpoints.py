from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_returns_ready_when_dependencies_are_usable(client: TestClient) -> None:
    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_ready_returns_503_when_tts_service_is_unavailable(monkeypatch) -> None:
    from llm_tts_api import dependencies
    from llm_tts_api.main import create_app

    monkeypatch.setattr(dependencies, "get_tts_service", lambda: object())
    app = create_app()

    def _failing_service():
        raise RuntimeError("tts unavailable")

    with TestClient(app) as test_client:
        monkeypatch.setattr(dependencies, "get_tts_service", _failing_service)
        response = test_client.get("/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"

