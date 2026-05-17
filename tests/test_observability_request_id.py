"""Tests for the request-id middleware and contextvar propagation.

S-004 / FR-OB-01.
"""

from __future__ import annotations

import asyncio
import logging
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from llm_tts_api.observability import (
    REQUEST_ID_HEADER,
    RequestIDMiddleware,
    current_request_id,
    request_id_var,
)


def _build_app(record_ids: list[str] | None = None) -> FastAPI:
    """Build a minimal app exercising the middleware.

    The single route records the request id seen via ``current_request_id``
    so we can assert that the middleware actually populated the contextvar
    (not just the response header).
    """
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/probe")
    async def probe() -> dict[str, str]:
        rid = current_request_id()
        if record_ids is not None:
            record_ids.append(rid)
        return {"request_id": rid}

    return app


class TestRequestIDMiddleware:
    def test_inbound_header_is_echoed(self) -> None:
        seen: list[str] = []
        client = TestClient(_build_app(seen))
        resp = client.get("/probe", headers={"X-Request-ID": "abc-123"})
        assert resp.status_code == 200
        assert resp.headers[REQUEST_ID_HEADER] == "abc-123"
        assert resp.json() == {"request_id": "abc-123"}
        assert seen == ["abc-123"]

    def test_id_generated_when_absent(self) -> None:
        seen: list[str] = []
        client = TestClient(_build_app(seen))
        resp = client.get("/probe")
        rid = resp.headers[REQUEST_ID_HEADER]
        # UUID4 hex form: 32 lowercase hex chars.
        assert re.fullmatch(r"[0-9a-f]{32}", rid), f"unexpected id shape: {rid!r}"
        assert resp.json() == {"request_id": rid}
        assert seen == [rid]

    def test_blank_inbound_header_is_replaced(self) -> None:
        client = TestClient(_build_app())
        resp = client.get("/probe", headers={"X-Request-ID": "   "})
        rid = resp.headers[REQUEST_ID_HEADER]
        assert re.fullmatch(r"[0-9a-f]{32}", rid)

    def test_serial_requests_get_distinct_ids(self) -> None:
        """Sanity smoke test — TestClient serial requests don't leak ids."""
        seen: list[str] = []
        client = TestClient(_build_app(seen))
        for header_value in ("alpha", "beta", "gamma"):
            resp = client.get("/probe", headers={"X-Request-ID": header_value})
            assert resp.headers[REQUEST_ID_HEADER] == header_value
        assert seen == ["alpha", "beta", "gamma"]

    @pytest.mark.asyncio
    async def test_concurrent_requests_get_distinct_ids(self) -> None:
        """SF-9: real concurrency test for the contextvar isolation claim.

        Fires 10 in-flight requests with distinct inbound ids and asserts
        each handler saw its own id back. The route awaits a short sleep
        so the requests are genuinely interleaved on the event loop —
        without that, the test reduces to the serial case.
        """
        import asyncio

        import httpx

        app = FastAPI()
        app.add_middleware(RequestIDMiddleware)

        @app.get("/race")
        async def race() -> dict[str, str]:
            # Yield to the loop so other requests can interleave.
            await asyncio.sleep(0.02)
            return {"id": current_request_id()}

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://x") as client:
            inbound_ids = [f"race-{i:02d}" for i in range(10)]
            responses = await asyncio.gather(
                *[client.get("/race", headers={"X-Request-ID": rid}) for rid in inbound_ids]
            )

        # Each request must have observed its own inbound id — no cross-talk.
        for inbound, resp in zip(inbound_ids, responses, strict=True):
            assert resp.status_code == 200
            assert resp.json()["id"] == inbound
            assert resp.headers[REQUEST_ID_HEADER] == inbound

    def test_contextvar_reset_after_request(self) -> None:
        """After the response completes, the contextvar must be back to its default."""
        client = TestClient(_build_app())
        client.get("/probe", headers={"X-Request-ID": "leak-check"})
        assert current_request_id() == ""

    def test_exception_handler_sees_request_id(self) -> None:
        """SF-4 / forward-compatibility: registered exception handlers must
        run while the request-id contextvar is still populated. S-009 (error
        envelope) will depend on this to attach ``error.request_id``.

        Starlette mounts ``ExceptionMiddleware`` inside the user middleware
        stack, so the exception handler executes BEFORE
        ``RequestIDMiddleware`` resets the contextvar. This test pins that
        contract so a future middleware insertion that reorders the stack
        regresses loudly.
        """
        from fastapi.responses import JSONResponse

        app = FastAPI()
        app.add_middleware(RequestIDMiddleware)

        class _Boom(Exception):
            pass

        @app.exception_handler(_Boom)
        async def _handle(_: object, __: _Boom) -> JSONResponse:
            return JSONResponse({"seen_request_id": current_request_id()})

        @app.get("/raises")
        async def raises() -> dict[str, str]:
            raise _Boom("kaboom")

        client = TestClient(app)
        resp = client.get("/raises", headers={"X-Request-ID": "boom-id"})
        assert resp.status_code == 200
        assert resp.json() == {"seen_request_id": "boom-id"}
        assert resp.headers[REQUEST_ID_HEADER] == "boom-id"

    def test_inner_app_can_override_header(self) -> None:
        """If a handler sets its own X-Request-ID, the middleware does NOT duplicate it."""
        app = FastAPI()
        app.add_middleware(RequestIDMiddleware)

        @app.get("/override")
        async def override() -> dict[str, str]:
            from fastapi.responses import JSONResponse

            return JSONResponse(
                {"request_id": current_request_id()},
                headers={REQUEST_ID_HEADER: "handler-set"},
            )

        client = TestClient(app)
        resp = client.get("/override")
        # Header still present, value is whatever the handler set, no duplication.
        assert resp.headers.get_list(REQUEST_ID_HEADER) == ["handler-set"]


class TestRequestIdContextVar:
    def test_default_is_empty(self) -> None:
        # Outside any request scope.
        assert request_id_var.get() == ""
        assert current_request_id() == ""

    def test_set_and_reset(self) -> None:
        token = request_id_var.set("manual-id")
        try:
            assert current_request_id() == "manual-id"
        finally:
            request_id_var.reset(token)
        assert current_request_id() == ""

    @pytest.mark.asyncio
    async def test_propagates_through_asyncio_task(self) -> None:
        """ContextVar must propagate into asyncio.create_task per Python semantics."""

        async def inner() -> str:
            return current_request_id()

        token = request_id_var.set("task-id")
        try:
            result = await asyncio.create_task(inner())
        finally:
            request_id_var.reset(token)
        assert result == "task-id"


class TestRequestIdInLogs:
    """Verify that log records emitted during a request carry the request id."""

    def test_log_record_carries_request_id(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Build a minimal app whose handler emits a log line.
        app = FastAPI()
        app.add_middleware(RequestIDMiddleware)
        logger = logging.getLogger("test.request_id")

        @app.get("/emit")
        async def emit() -> dict[str, str]:
            logger.info("synthesis dispatched")
            return {"ok": "yes"}

        # Install the filter that the production setup_logging() installs,
        # so caplog records get the request_id attribute populated.
        from llm_tts_api.app_logging import RequestIdFilter

        caplog.handler.addFilter(RequestIdFilter())

        client = TestClient(app)
        with caplog.at_level(logging.INFO, logger="test.request_id"):
            client.get("/emit", headers={"X-Request-ID": "log-probe"})

        records = [r for r in caplog.records if r.name == "test.request_id"]
        assert records, "expected at least one captured log record"
        for record in records:
            assert getattr(record, "request_id", None) == "log-probe"
