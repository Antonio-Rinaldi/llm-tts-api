"""Typed error taxonomy + OpenAI-compatible error envelope (S-009).

Implements FR-ER-01..04 (analyst-frs.md §4.11):

- Five broad ``type`` categories: ``validation_error``, ``voice_error``,
  ``provider_error``, ``capacity_error``, ``internal_error``.
- A non-exhaustive sub-code registry that documents the codes Sprint-2 stories
  emit; arbitrary code strings are still accepted at construction time so
  future sprints can add codes without touching this module.
- Envelope shape: ``{"error": {"type", "code", "message", "param", "request_id"}}``.
- The ``request_id`` field is injected by the FastAPI exception handler at
  render time from :func:`llm_tts_api.observability.current_request_id` (the
  S-004 contextvar seam), so call sites raising :class:`OpenAIHTTPException`
  don't have to thread the id manually.
- The handler also sets ``X-Error-Code`` on every error response (FR-ER-03).
- ``internal_error.unexpected_error`` (FR-ER-04) is produced ONLY by the
  fallback handler in :mod:`llm_tts_api.main`; the client message is generic
  and the original exception's traceback goes to logs only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final, Literal, NoReturn

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from llm_tts_api.observability import current_request_id

logger = logging.getLogger(__name__)

ErrorCategory = Literal[
    "validation_error",
    "voice_error",
    "provider_error",
    "capacity_error",
    "internal_error",
    "config_error",
]

# S-027 — cycle-2 startup-fail error codes. These are NOT returned in an
# HTTP envelope (they fire during the lifespan, before the socket is
# bound); they are surfaced as strings in stderr / structured log lines
# and verified by UAT-PR-11/12/14. Registered here so the taxonomy stays
# in one place and future stories can grep the codes from a single
# module.
CONFIG_ERROR_PRESETS_INVALID: Final = "config_error.presets_invalid"
CONFIG_ERROR_PRESET_PROVIDER_INVALID: Final = "config_error.preset_provider_invalid"
CONFIG_ERROR_PRESETS_UNSAFE_PERMISSIONS: Final = "config_error.presets_unsafe_permissions"

# Sub-code registry — documents codes used by Sprint-2 stories. Not a closed
# set: the handler does not validate ``code`` against this registry. New
# stories can introduce codes without editing this file; this dict exists for
# discoverability and so type-checkers can flag obvious typos at the few
# call sites that use the constants directly.
ERROR_CODES: Final[dict[ErrorCategory, frozenset[str]]] = {
    "validation_error": frozenset(
        {
            "invalid_parameter",
            "voice_required",
            "input_too_long",
            "ref_audio_invalid",
            "consent_required",
            "voice_id_exists",
            "unknown_provider",
            "unknown_model",
            "voice_reference_missing",
            "not_implemented",
            "preset_unknown",
        }
    ),
    "voice_error": frozenset(
        {
            "voice_not_found",
            "voice_blob_missing",
        }
    ),
    "provider_error": frozenset(
        {
            "model_load_failed",
            "synthesis_failed",
            "no_viable_provider",
            "voice_seed_ingest_failed",
            "voice_store_unavailable",
        }
    ),
    "capacity_error": frozenset(
        {
            "queue_full",
            "service_unavailable",
            "timeout",
        }
    ),
    "internal_error": frozenset(
        {
            "unexpected_error",
        }
    ),
    "config_error": frozenset(
        {
            "presets_invalid",
            "preset_provider_invalid",
            "presets_unsafe_permissions",
        }
    ),
}

X_ERROR_CODE_HEADER: Final = "X-Error-Code"
_GENERIC_INTERNAL_MESSAGE: Final = "An unexpected error occurred."


@dataclass(slots=True)
class OpenAIError:
    """Structured OpenAI-style error payload (sans request_id, injected later)."""

    message: str
    type: ErrorCategory
    code: str
    param: str | None = None

    def as_envelope(self, request_id: str) -> dict[str, object]:
        """Serialize to the OpenAI-compatible envelope, including request_id.

        ``request_id`` is rendered as an empty string when called outside a
        request scope (defensive — the handler always has one, but unit tests
        may build envelopes directly).
        """
        return {
            "error": {
                "message": self.message,
                "type": self.type,
                "param": self.param,
                "code": self.code,
                "request_id": request_id,
            }
        }


class OpenAIHTTPException(HTTPException):
    """``HTTPException`` carrying a structured :class:`OpenAIError` payload.

    The error object itself is stored on the exception (``self.error``) so the
    FastAPI handler can render the envelope with the correlation id at the
    moment the response is built (rather than at raise time, which would
    require every call site to thread the id).

    ``detail`` is also populated with a pre-rendered (request_id-less)
    envelope so that any code that catches the exception and inspects
    ``detail`` (legacy path) still sees a recognizable shape.
    """

    error: OpenAIError

    def __init__(self, status_code: int, error: OpenAIError) -> None:
        """Initialize with HTTP status + structured error."""
        super().__init__(status_code=status_code, detail=error.as_envelope("")["error"])
        self.error = error


def _make(
    status_code: int,
    *,
    type: ErrorCategory,
    code: str,
    message: str,
    param: str | None = None,
) -> OpenAIHTTPException:
    return OpenAIHTTPException(
        status_code=status_code,
        error=OpenAIError(message=message, type=type, code=code, param=param),
    )


def invalid_request(
    message: str,
    param: str | None = None,
    code: str = "invalid_parameter",
    status_code: int = 400,
) -> OpenAIHTTPException:
    """Create a ``validation_error`` (FR-ER-02); ``status_code`` allows 409-style conflicts."""
    return _make(status_code, type="validation_error", code=code, message=message, param=param)


def raise_not_implemented(endpoint: str) -> NoReturn:
    """Raise the standard 501 ``not_implemented`` envelope for one endpoint."""
    raise not_implemented(f"Endpoint '{endpoint}' is not implemented yet")


def not_implemented(message: str) -> OpenAIHTTPException:
    """Create a 501 ``validation_error.not_implemented``.

    501 isn't in FR-ER-02's status table; we keep the same category surface
    so the envelope is uniform across the whole API. ``code='not_implemented'``
    keeps the existing client contract.
    """
    return _make(501, type="validation_error", code="not_implemented", message=message, param=None)


def queue_full(message: str = "Server is at capacity; queue is full") -> OpenAIHTTPException:
    """Create a standardized 429 capacity error for admission-queue overflow.

    Refined by S-009 once the typed error taxonomy lands; the type/code values
    here already match the taxonomy that S-010 / S-009 will consume.
    """
    return OpenAIHTTPException(
        status_code=429,
        error=OpenAIError(
            message=message,
            type="capacity_error",
            param=None,
            code="queue_full",
        ),
    )


def internal_error(message: str = "Internal server error") -> OpenAIHTTPException:
    """Create a 500 ``internal_error.unexpected_error`` (FR-ER-04).

    Prefer letting unhandled exceptions reach the generic handler — that path
    logs the traceback. This factory is for explicit raises where the caller
    has already decided to surface a generic message.
    """
    return _make(
        500,
        type="internal_error",
        code="unexpected_error",
        message=message,
        param=None,
    )


def capacity_error(
    code: str, message: str, status_code: int = 429, param: str | None = None
) -> OpenAIHTTPException:
    """Create a ``capacity_error`` (queue_full=429, service_unavailable=503, timeout=504)."""
    return _make(status_code, type="capacity_error", code=code, message=message, param=param)


def provider_error(
    code: str, message: str, status_code: int = 502, param: str | None = None
) -> OpenAIHTTPException:
    """Create a ``provider_error`` (model_load_failed, no_viable_provider, …)."""
    return _make(status_code, type="provider_error", code=code, message=message, param=param)


def voice_error(
    code: str, message: str, status_code: int = 404, param: str | None = None
) -> OpenAIHTTPException:
    """Create a ``voice_error`` (voice_not_found=404, voice_blob_missing=422)."""
    return _make(status_code, type="voice_error", code=code, message=message, param=param)


# ---------------------------------------------------------------------------
# Handlers — registered by ``llm_tts_api.main.create_app``.
# ---------------------------------------------------------------------------


def _envelope_response(status_code: int, error: OpenAIError, request_id: str) -> JSONResponse:
    """Build the JSON response with envelope + ``X-Error-Code`` header (FR-ER-03)."""
    return JSONResponse(
        status_code=status_code,
        content=error.as_envelope(request_id),
        headers={X_ERROR_CODE_HEADER: error.code},
    )


async def openai_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    """Render :class:`OpenAIHTTPException` with the active correlation id."""
    assert isinstance(exc, OpenAIHTTPException)  # noqa: S101 — dispatcher guarantee
    return _envelope_response(exc.status_code, exc.error, current_request_id())


async def http_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    """Wrap bare ``HTTPException`` (e.g. 404 not-found) into the envelope.

    FastAPI / Starlette routes that raise a plain ``HTTPException`` (or that
    FastAPI raises internally for 404s on unmatched paths) bypass the
    :class:`OpenAIHTTPException` handler. Without this fallback, those
    responses would be ``{"detail": "Not Found"}`` — a shape that violates
    FR-ER-01. Map the status to the most plausible category.
    """
    if isinstance(exc, OpenAIHTTPException):
        # Defensive: registered handler should catch first, but the FastAPI
        # exception_handler dispatcher walks MRO; keep this branch correct.
        return await openai_exception_handler(_, exc)
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101 — dispatcher guarantee

    category: ErrorCategory
    code: str
    if exc.status_code == 404:
        category, code = "validation_error", "not_found"
    elif exc.status_code == 405:
        category, code = "validation_error", "method_not_allowed"
    elif exc.status_code in {502, 503, 504}:
        category, code = "capacity_error", "service_unavailable"
    elif exc.status_code in {400, 422}:
        category, code = "validation_error", "invalid_parameter"
    else:
        category, code = "internal_error", "unexpected_error"

    message = str(exc.detail) if exc.detail is not None else _GENERIC_INTERNAL_MESSAGE
    error = OpenAIError(message=message, type=category, code=code, param=None)
    return _envelope_response(exc.status_code, error, current_request_id())


async def validation_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    """Render Pydantic / FastAPI request-validation failures as ``validation_error``.

    FastAPI's built-in handler would emit ``{"detail": [...]}`` — not our
    envelope. We extract the first error's path → ``param`` and concatenate
    the messages, then render via the envelope.
    """
    assert isinstance(exc, RequestValidationError)  # noqa: S101 — dispatcher guarantee
    errors = exc.errors()
    param: str | None = None
    message = "Request validation failed"
    if errors:
        first = errors[0]
        loc = first.get("loc", ())
        if loc:
            # FastAPI prefixes ``("body",)`` / ``("query",)`` — strip the
            # source prefix so ``param`` matches the field name a client sent.
            tail = [str(p) for p in loc if p not in {"body", "query", "path", "header"}]
            param = ".".join(tail) if tail else None
        message = first.get("msg", message)
    error = OpenAIError(
        message=message, type="validation_error", code="invalid_parameter", param=param
    )
    return _envelope_response(422, error, current_request_id())


async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    """Map any uncaught exception to ``internal_error.unexpected_error`` (FR-ER-04).

    Logs the traceback at ERROR. The client message is generic — never the
    original exception text — to satisfy NFR-PV-02 (no path / no internals).
    """
    logger.exception("Unhandled exception while serving request: %s", type(exc).__name__)
    error = OpenAIError(
        message=_GENERIC_INTERNAL_MESSAGE,
        type="internal_error",
        code="unexpected_error",
        param=None,
    )
    return _envelope_response(500, error, current_request_id())
