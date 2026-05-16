from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_returns_ready_when_dependencies_are_usable(client: TestClient) -> None:
    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_ready_returns_503_when_tts_service_is_unavailable(
    monkeypatch: "pytest.MonkeyPatch",  # noqa: F821 — runtime import below
) -> None:
    """``/ready`` reaches into ``dependencies.get_tts_service`` directly
    (attribute access on the module), so we monkeypatch the module to
    simulate a failed lookup here. The standard ``client`` fixture installs
    the happy-path fake; this test exercises the failure branch."""
    from llm_tts_api import dependencies
    from llm_tts_api.main import create_app
    from tests.fakes.fake_tts_service import FakeTTSService

    # First install a healthy fake so app startup succeeds.
    healthy_fake = FakeTTSService()
    # Guarded clear (the attribute is an lru_cache wrapper before patching).
    getattr(dependencies.get_tts_service, "cache_clear", lambda: None)()
    monkeypatch.setattr(dependencies, "get_tts_service", lambda: healthy_fake)

    app = create_app()

    def _failing_service() -> FakeTTSService:
        raise RuntimeError("tts unavailable")

    with TestClient(app) as test_client:
        # Swap in the failing factory after startup so /ready sees the
        # failure on the live request.
        monkeypatch.setattr(dependencies, "get_tts_service", _failing_service)
        response = test_client.get("/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    # monkeypatch teardown restores the lru_cache wrapper; no manual clear.

