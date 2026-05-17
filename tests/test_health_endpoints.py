"""Health/readiness endpoint tests.

Post-S-003: ``/ready`` reads ``request.app.state.tts_service`` directly.
The standard ``client`` fixture installs a healthy fake on that slot; the
degraded path is exercised by removing the slot at test time.
"""

from __future__ import annotations

import pytest
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/ready`` returns 503 when ``app.state.tts_service`` is missing or
    raises during access. We simulate by building an app with the lifespan
    bypassed and explicitly NOT populating ``tts_service``."""
    from llm_tts_api.main import TEST_BYPASS_ENV, create_app

    monkeypatch.setenv(TEST_BYPASS_ENV, "1")
    app = create_app()
    # Intentionally do NOT set app.state.tts_service.

    with TestClient(app) as test_client:
        response = test_client.get("/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
