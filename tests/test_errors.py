"""S-009 — Typed error envelope tests (FR-ER-01..04 / UAT-ER-01..02 / UAT-OB-04).

Each test pins one acceptance bullet:

- ``test_envelope_shape_*``: UAT-ER-01 — every category renders the same
  five-field envelope with ``request_id``.
- ``test_x_error_code_header_*``: UAT-OB-04 / FR-ER-03 — ``X-Error-Code``
  header parity with ``error.code``.
- ``test_unhandled_exception_*``: UAT-ER-02 / FR-ER-04 — uncaught exception
  becomes ``internal_error.unexpected_error`` with a generic message; the
  exception text (which would contain a path here) does NOT leak to the
  client body, but the traceback IS in the log buffer.
- ``test_request_id_*``: FR-ER-01 — the ``X-Request-ID`` from the response
  appears verbatim as ``error.request_id`` in the envelope body.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from llm_tts_api.errors import (
    OpenAIError,
    OpenAIHTTPException,
    capacity_error,
    invalid_request,
    not_implemented,
    provider_error,
    voice_error,
)
from llm_tts_api.main import TEST_BYPASS_ENV, create_app


@pytest.fixture
def error_app() -> Iterator[TestClient]:
    """Build a minimal app exposing one route per error class for envelope assertions."""
    import os

    os.environ[TEST_BYPASS_ENV] = "1"
    try:
        app = create_app()

        @app.get("/_test/validation")
        def _validation() -> None:
            raise invalid_request("input is missing", param="input")

        @app.get("/_test/voice")
        def _voice() -> None:
            raise voice_error("voice_not_found", "voice 'nova' is not configured")

        @app.get("/_test/provider")
        def _provider() -> None:
            raise provider_error(
                "no_viable_provider",
                "no provider supports device=cpu",
                status_code=500,
            )

        @app.get("/_test/capacity")
        def _capacity() -> None:
            raise capacity_error("queue_full", "admission queue is full", status_code=429)

        @app.get("/_test/not_implemented")
        def _ni() -> None:
            raise not_implemented("Endpoint not implemented yet")

        @app.get("/_test/unhandled")
        def _unhandled() -> None:
            raise RuntimeError("/Users/foo/secret/path.bin")

        from pydantic import BaseModel

        class _ValidateBody(BaseModel):
            n: int

        @app.post("/_test/validate_body")
        def _validate_body(payload: _ValidateBody) -> dict[str, int]:
            return {"ok": payload.n}

        @app.get("/_test/typed_internal")
        def _typed_internal() -> None:
            raise OpenAIHTTPException(
                500,
                error=OpenAIError(
                    message="oops",
                    type="internal_error",
                    code="unexpected_error",
                ),
            )

        with TestClient(app, raise_server_exceptions=False) as client:
            yield client
    finally:
        os.environ.pop(TEST_BYPASS_ENV, None)


# --- UAT-ER-01: envelope shape per category --------------------------------


@pytest.mark.parametrize(
    ("path", "status", "type_", "code", "param"),
    [
        ("/_test/validation", 400, "validation_error", "invalid_parameter", "input"),
        ("/_test/voice", 404, "voice_error", "voice_not_found", None),
        ("/_test/provider", 500, "provider_error", "no_viable_provider", None),
        ("/_test/capacity", 429, "capacity_error", "queue_full", None),
        ("/_test/not_implemented", 501, "validation_error", "not_implemented", None),
        ("/_test/typed_internal", 500, "internal_error", "unexpected_error", None),
    ],
)
def test_envelope_shape_per_category(
    error_app: TestClient,
    path: str,
    status: int,
    type_: str,
    code: str,
    param: str | None,
) -> None:
    response = error_app.get(path)

    assert response.status_code == status
    body = response.json()
    assert set(body.keys()) == {"error"}
    err = body["error"]
    assert err["type"] == type_
    assert err["code"] == code
    assert err["param"] == param
    assert isinstance(err["message"], str) and err["message"]
    assert "request_id" in err and isinstance(err["request_id"], str)


# --- UAT-OB-04 / FR-ER-03: X-Error-Code header parity ----------------------


def test_x_error_code_header_matches_error_code(error_app: TestClient) -> None:
    response = error_app.get("/_test/capacity")

    assert response.headers["X-Error-Code"] == "queue_full"
    assert response.headers["X-Error-Code"] == response.json()["error"]["code"]


def test_x_request_id_echoed_into_envelope(error_app: TestClient) -> None:
    response = error_app.get("/_test/validation", headers={"X-Request-ID": "test-req-abc"})

    assert response.headers["X-Request-ID"] == "test-req-abc"
    assert response.json()["error"]["request_id"] == "test-req-abc"


def test_request_id_is_generated_when_absent(error_app: TestClient) -> None:
    response = error_app.get("/_test/validation")
    rid = response.json()["error"]["request_id"]
    assert rid
    assert response.headers["X-Request-ID"] == rid


# --- UAT-ER-02 / FR-ER-04: unhandled exception path ------------------------


def test_unhandled_exception_returns_generic_internal_error(
    error_app: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR, logger="llm_tts_api.errors")

    response = error_app.get("/_test/unhandled")

    assert response.status_code == 500
    body = response.json()["error"]
    assert body["type"] == "internal_error"
    assert body["code"] == "unexpected_error"
    # Generic — must NOT contain the offending path.
    assert "/Users/foo/secret/path.bin" not in body["message"]
    assert "Traceback" not in body["message"]
    assert response.headers["X-Error-Code"] == "unexpected_error"
    # ...but the traceback IS in the logs (FR-ER-04).
    assert any("Unhandled exception" in record.message for record in caplog.records), (
        f"Expected unhandled-exception log, got: {[r.message for r in caplog.records]}"
    )


# --- HTTPException + RequestValidationError fallbacks ----------------------


def test_unmatched_path_uses_envelope() -> None:
    """FastAPI's 404 for unmatched routes goes through the envelope handler."""
    import os

    os.environ[TEST_BYPASS_ENV] = "1"
    try:
        app = create_app()
        with TestClient(app) as client:
            response = client.get("/__definitely_does_not_exist__")
    finally:
        os.environ.pop(TEST_BYPASS_ENV, None)

    assert response.status_code == 404
    body = response.json()["error"]
    assert body["type"] == "validation_error"
    assert body["code"] == "not_found"
    assert response.headers["X-Error-Code"] == "not_found"


def test_request_validation_error_uses_envelope(error_app: TestClient) -> None:
    """422 from RequestValidationError carries the envelope + param name."""
    response = error_app.post("/_test/validate_body", json={"n": "not-an-int"})

    assert response.status_code == 422, response.text
    body = response.json()["error"]
    assert body["type"] == "validation_error"
    assert body["code"] == "invalid_parameter"
    # ``param`` is the dotted path into the failing field; depending on the
    # FastAPI/Pydantic version it is either ``n`` (single-body-param fast path)
    # or ``payload.n`` (when FastAPI keeps the parameter name in ``loc``).
    assert body["param"] in {"n", "payload.n", "payload"}, body
    assert response.headers["X-Error-Code"] == "invalid_parameter"


# --- Pure-unit: OpenAIError envelope serialization -------------------------


def test_openai_error_envelope_serialization() -> None:
    err = OpenAIError(
        message="hello", type="validation_error", code="input_too_long", param="input"
    )
    assert err.as_envelope("req-1") == {
        "error": {
            "message": "hello",
            "type": "validation_error",
            "code": "input_too_long",
            "param": "input",
            "request_id": "req-1",
        }
    }


def test_openai_http_exception_stores_error() -> None:
    err = OpenAIError(message="bad", type="validation_error", code="invalid_parameter", param="x")
    exc = OpenAIHTTPException(400, error=err)
    assert exc.status_code == 400
    assert exc.error is err
    # ``detail`` is the pre-rendered envelope's inner ``error`` dict (legacy).
    assert exc.detail["type"] == "validation_error"
    assert exc.detail["code"] == "invalid_parameter"
