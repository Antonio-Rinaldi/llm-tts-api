"""Logging setup with request-id propagation and optional JSON output.

Implements FR-OB-02 + NFR-OB-02 + NFR-PV-02 (SRS §4.9, NFR §7):

* Every record carries a ``request_id`` attribute (populated from the
  ``contextvars.ContextVar`` set by ``RequestIDMiddleware``). When emitted
  outside a request (startup, lifespan, background tasks) the value is the
  literal ``"-"`` so log lines stay column-aligned.
* The default human-readable format includes the request id. Setting
  ``APP_LOG_FORMAT=json`` switches to single-line JSON suitable for log
  aggregators.
* Redaction is the operator's responsibility at the call site: this module
  doesn't truncate or mask payloads. NFR-PV-02 says INFO+ logs must not echo
  raw input text or audio bytes — that's enforced at the producer (route
  handlers, services), not here. This module's contract is "preserve
  whatever the producer chose to log" plus the request-id correlation.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from llm_tts_api.observability.request_id import current_request_id

_NO_REQUEST_MARKER = "-"


class RequestIdFilter(logging.Filter):
    """Attach the current request id (or ``"-"``) to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        rid = current_request_id() or _NO_REQUEST_MARKER
        record.request_id = rid
        return True


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per record.

    Field set is deliberately small: ``ts``, ``level``, ``logger``,
    ``request_id``, ``message``. Extras attached to the record (via
    ``logger.info("...", extra={...})``) are merged in. Exception info, if
    present, is folded into ``exc_info`` as a string.
    """

    # Standard LogRecord attributes that should NOT bleed into the JSON
    # output (they're internals or already represented by named fields).
    _RESERVED = frozenset(
        {
            "args",
            "asctime",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "message",
            "module",
            "msecs",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "thread",
            "threadName",
            "taskName",
            "request_id",
        }
    )

    def formatTime(  # noqa: N802 — overriding stdlib name
        self, record: logging.LogRecord, datefmt: str | None = None
    ) -> str:
        """Emit ISO-8601 UTC. Overrides stdlib's `time.localtime`-based path.

        Stdlib `Formatter.formatTime` builds a tz-naive `time.struct_time`
        and feeds it to `strftime`, where `%z` yields an empty string on
        most platforms — producing shapeless `ts` values like
        `"2026-05-17T17:35:55"`. Going through `datetime` with an explicit
        `tz=timezone.utc` gives us `"2026-05-17T15:35:55.123456+00:00"`,
        which log aggregators parse cleanly.
        """
        return datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", _NO_REQUEST_MARKER),
            "message": record.getMessage(),
        }
        # Merge user-supplied extras while preserving the standard fields.
        for key, value in record.__dict__.items():
            if key not in self._RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level_name: str = "INFO", *, log_format: str | None = None) -> str:
    """Configure root and uvicorn loggers and return the normalized level name.

    ``log_format`` overrides the ``APP_LOG_FORMAT`` env var when set. ``"json"``
    selects the JSON formatter; any other value (or ``None`` / unset) selects
    the human-readable formatter.
    """
    level = getattr(logging, (level_name or "INFO").upper(), logging.INFO)
    selected_format = (log_format or os.environ.get("APP_LOG_FORMAT", "text")).strip().lower()

    formatter: logging.Formatter
    if selected_format == "json":
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s %(levelname)-5s %(name)s [%(request_id)s] | %(message)s"
        )

    request_id_filter = RequestIdFilter()

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Replace existing handlers (idempotent: re-running setup_logging in
    # tests or after a config change must not duplicate output).
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(request_id_filter)
    root_logger.addHandler(stream_handler)

    # Keep uvicorn logs aligned with app verbosity AND give them the same
    # request-id-aware formatter. Uvicorn installs its own handlers; we
    # strip them and let the root logger handle propagation.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "llm_tts_api"):
        named_logger = logging.getLogger(name)
        named_logger.setLevel(level)
        for handler in list(named_logger.handlers):
            named_logger.removeHandler(handler)
        named_logger.propagate = True

    return logging.getLevelName(level)
