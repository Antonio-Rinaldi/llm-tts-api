"""Direct unit tests for the request-aware Depends getters in dependencies.py.

Closes SF-2 from S-003's code review: previously only ``get_tts_service``
was indirectly covered (via ``test_conftest.py``); the remaining five
getters (``get_settings``, ``get_model_registry``,
``get_tts_provider_registry``, ``get_stt_service``, ``get_device_profile``)
had no direct test. A bug like swapping ``request.app.state.settings`` for
``request.app.state.app_settings`` in any getter would have gone unnoticed.

Each test declares a tiny probe route that consumes the getter via
``Depends`` and asserts the returned object is identical (``is``) to the
slot set by the ``client`` fixture. Identity is the strongest check; it
proves both the right slot was read AND the wiring is end-to-end.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends
from fastapi.testclient import TestClient

from llm_tts_api.dependencies import (
    get_device_profile,
    get_model_registry,
    get_settings,
    get_stt_service,
    get_tts_provider_registry,
)


def _register_identity_probe(
    client: TestClient, dep_callable: Callable[..., object], slot_name: str
) -> None:
    """Attach a probe route that asserts ``Depends(dep_callable) is app.state.<slot>``.

    The route returns the boolean answer; the test asserts ``True`` so a
    regression that points the getter at a wrong slot is visible.

    Uses the imperative ``Depends()``-as-default form (not ``Annotated[...]``)
    because the Annotated form with a bare ``object`` type confuses FastAPI's
    parameter-classification machinery into treating it as a body/query
    parameter, producing a 422.
    """
    app = client.app  # type: ignore[attr-defined]

    @app.get(f"/__test/dep/{slot_name}")  # type: ignore[misc]
    def _probe(resolved: object = Depends(dep_callable)) -> dict[str, object]:
        expected = getattr(app.state, slot_name)
        return {
            "same_instance": resolved is expected,
            "resolved_type": type(resolved).__name__,
        }


def test_get_settings_returns_app_state_settings(client: TestClient) -> None:
    _register_identity_probe(client, get_settings, "settings")
    response = client.get("/__test/dep/settings")
    assert response.status_code == 200
    assert response.json()["same_instance"] is True


def test_get_model_registry_returns_app_state_model_registry(client: TestClient) -> None:
    _register_identity_probe(client, get_model_registry, "model_registry")
    response = client.get("/__test/dep/model_registry")
    assert response.status_code == 200
    assert response.json()["same_instance"] is True


def test_get_tts_provider_registry_returns_app_state_provider_registry(
    client: TestClient,
) -> None:
    _register_identity_probe(client, get_tts_provider_registry, "provider_registry")
    response = client.get("/__test/dep/provider_registry")
    assert response.status_code == 200
    assert response.json()["same_instance"] is True


def test_get_stt_service_returns_app_state_stt_service(client: TestClient) -> None:
    _register_identity_probe(client, get_stt_service, "stt_service")
    response = client.get("/__test/dep/stt_service")
    assert response.status_code == 200
    assert response.json()["same_instance"] is True


def test_get_device_profile_returns_app_state_device_profile(client: TestClient) -> None:
    _register_identity_probe(client, get_device_profile, "device_profile")
    response = client.get("/__test/dep/device_profile")
    assert response.status_code == 200
    assert response.json()["same_instance"] is True
    assert response.json()["resolved_type"] == "DeviceProfile"
