"""Regression test for the ``client`` fixture override pattern (post-S-003).

The ``client`` fixture uses ``app.dependency_overrides`` so that
``Depends(get_tts_service)`` resolves to the test's ``FakeTTSService``
regardless of how a router imports the dependency. This test pins down
that behavior so a future regression (e.g. dropping the override wiring)
would be caught.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from fastapi.testclient import TestClient

# Idiomatic import — the exact pattern routers use. This mirrors the
# Sprint 5 / S-025 regression test that ships in llm-image-api.
from llm_tts_api.dependencies import get_tts_service
from tests.fakes.fake_tts_service import FakeTTSService


def test_client_fixture_overrides_get_tts_service_via_direct_import(
    client: TestClient,
    fake_tts_service: FakeTTSService,
) -> None:
    """A route declared on the live app that consumes ``Depends(get_tts_service)``
    through the direct-import form must resolve to the fixture's
    ``FakeTTSService`` — not to whatever ``dependencies.get_tts_service``
    returns at module level."""
    app = client.app  # type: ignore[attr-defined]

    @app.get("/__test/whoami")  # type: ignore[misc]
    def _whoami(
        svc: Annotated[FakeTTSService, Depends(get_tts_service)],
    ) -> dict[str, object]:
        return {
            "is_fake": isinstance(svc, FakeTTSService),
            "same_instance": svc is fake_tts_service,
            "type_name": type(svc).__name__,
        }

    response = client.get("/__test/whoami")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "is_fake": True,
        "same_instance": True,
        "type_name": "FakeTTSService",
    }
