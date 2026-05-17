"""Tests for setup_logging + formatters (S-004 / FR-OB-02 / NFR-OB-02).

Supersedes the original minimal test which only asserted the debug-level
behavior — that assertion is preserved as ``test_setup_logging_uses_debug_level``.
"""

from __future__ import annotations

import io
import json
import logging

import pytest

from llm_tts_api.app_logging import (
    JsonFormatter,
    RequestIdFilter,
    setup_logging,
)
from llm_tts_api.observability.request_id import request_id_var


def _capture_handler(logger: logging.Logger, formatter: logging.Formatter) -> io.StringIO:
    """Attach an in-memory handler to the given logger and return its buffer."""
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(formatter)
    handler.addFilter(RequestIdFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return buffer


@pytest.fixture(autouse=True)
def _reset_root_logger() -> None:
    """Restore root logger state after every test in this module."""
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(logging.WARNING)


# ----- Preserved-from-original ----------------------------------------------


def test_setup_logging_uses_debug_level() -> None:
    level_name = setup_logging("DEBUG")
    assert level_name == "DEBUG"
    assert logging.getLogger("llm_tts_api").level == logging.DEBUG


# ----- RequestIdFilter ------------------------------------------------------


class TestRequestIdFilter:
    def test_default_marker_when_outside_request(self) -> None:
        record = logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="x",
            args=(),
            exc_info=None,
        )
        RequestIdFilter().filter(record)
        assert record.request_id == "-"

    def test_uses_contextvar_inside_request(self) -> None:
        token = request_id_var.set("rid-42")
        try:
            record = logging.LogRecord(
                name="t",
                level=logging.INFO,
                pathname=__file__,
                lineno=1,
                msg="x",
                args=(),
                exc_info=None,
            )
            RequestIdFilter().filter(record)
            assert record.request_id == "rid-42"
        finally:
            request_id_var.reset(token)


# ----- JsonFormatter --------------------------------------------------------


class TestJsonFormatter:
    def test_emits_single_line_json(self) -> None:
        logger = logging.getLogger("test.json.basic")
        buffer = _capture_handler(logger, JsonFormatter())
        try:
            logger.info("hello world")
        finally:
            logger.handlers.clear()

        line = buffer.getvalue().strip()
        # Single line, no embedded newlines that would break log aggregators.
        assert "\n" not in line
        payload = json.loads(line)
        assert payload["message"] == "hello world"
        assert payload["level"] == "INFO"
        assert payload["logger"] == "test.json.basic"
        assert payload["request_id"] == "-"
        assert "ts" in payload

    def test_includes_extras(self) -> None:
        logger = logging.getLogger("test.json.extras")
        buffer = _capture_handler(logger, JsonFormatter())
        try:
            logger.info("with extras", extra={"voice_id": "alloy", "chunks": 3})
        finally:
            logger.handlers.clear()

        payload = json.loads(buffer.getvalue().strip())
        assert payload["voice_id"] == "alloy"
        assert payload["chunks"] == 3

    def test_request_id_propagates_into_payload(self) -> None:
        logger = logging.getLogger("test.json.rid")
        buffer = _capture_handler(logger, JsonFormatter())
        token = request_id_var.set("ctx-7")
        try:
            logger.info("inside request")
        finally:
            request_id_var.reset(token)
            logger.handlers.clear()

        payload = json.loads(buffer.getvalue().strip())
        assert payload["request_id"] == "ctx-7"

    def test_exception_info_included(self) -> None:
        logger = logging.getLogger("test.json.exc")
        buffer = _capture_handler(logger, JsonFormatter())
        try:
            try:
                raise RuntimeError("boom")
            except RuntimeError:
                logger.exception("caught it")
        finally:
            logger.handlers.clear()

        payload = json.loads(buffer.getvalue().strip())
        assert "exc_info" in payload
        assert "RuntimeError" in payload["exc_info"]
        assert "boom" in payload["exc_info"]


# ----- setup_logging --------------------------------------------------------


class TestSetupLogging:
    def test_default_is_human_readable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("APP_LOG_FORMAT", raising=False)
        level = setup_logging("INFO")
        assert level == "INFO"
        stream_handlers = [
            h for h in logging.getLogger().handlers if isinstance(h, logging.StreamHandler)
        ]
        assert len(stream_handlers) == 1
        assert not isinstance(stream_handlers[0].formatter, JsonFormatter)

    def test_json_format_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_LOG_FORMAT", "json")
        setup_logging("INFO")
        stream_handlers = [
            h for h in logging.getLogger().handlers if isinstance(h, logging.StreamHandler)
        ]
        assert len(stream_handlers) == 1
        assert isinstance(stream_handlers[0].formatter, JsonFormatter)

    def test_json_format_via_explicit_argument(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("APP_LOG_FORMAT", raising=False)
        setup_logging("INFO", log_format="json")
        stream_handlers = [
            h for h in logging.getLogger().handlers if isinstance(h, logging.StreamHandler)
        ]
        assert isinstance(stream_handlers[0].formatter, JsonFormatter)

    def test_idempotent_no_duplicate_handlers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("APP_LOG_FORMAT", raising=False)
        setup_logging("INFO")
        setup_logging("DEBUG")
        stream_handlers = [
            h for h in logging.getLogger().handlers if isinstance(h, logging.StreamHandler)
        ]
        # Two invocations must leave exactly one handler (the second
        # replaces the first); not two stacked handlers that would
        # duplicate every log line.
        assert len(stream_handlers) == 1

    def test_normalizes_level_name(self) -> None:
        assert setup_logging("debug") == "DEBUG"
        assert setup_logging("INFO") == "INFO"
        # Garbage falls back to INFO.
        assert setup_logging("nonsense") == "INFO"
