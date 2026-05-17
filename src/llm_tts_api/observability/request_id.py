"""Request-ID propagation across the async event loop.

Implements FR-OB-01 (SRS §4.9): every request carries a correlation id that
appears in the response headers and on every log line emitted while serving
the request. Downstream stories (S-009 error envelopes, the rich endpoint
response headers in S-013) consume the same `contextvars.ContextVar` to
keep error responses and metadata headers correlated.

The id comes from the inbound ``X-Request-ID`` header when present; otherwise
a fresh UUIDv4 is generated. The middleware sets the contextvar at the start
of the request and resets it on exit so a single ASGI app can serve many
concurrent requests without cross-talk.
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "x-request-id"  # ASGI delivers headers in lowercase.
_NO_REQUEST = ""  # Sentinel for "outside a request" — empty string keeps log lines stable.

# Inbound `X-Request-ID` is echoed verbatim into both the response header AND
# every log line via the request-id-aware log format. A client-supplied value
# containing control characters could forge log lines (log injection). Accept
# only a conservative correlation-id charset; mint a fresh UUID on mismatch.
_SAFE_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")

# ContextVar is the canonical async-safe propagation seam: each request gets
# its own logical context, and asyncio.Task copies the context at task creation
# time. Logging filters and downstream services read this var directly.
request_id_var: ContextVar[str] = ContextVar("request_id", default=_NO_REQUEST)


def current_request_id() -> str:
    """Return the current request's id, or an empty string outside a request."""
    return request_id_var.get()


class RequestIDMiddleware:
    """Pure-ASGI middleware that assigns and propagates an X-Request-ID.

    Implemented as a raw ASGI middleware (not a Starlette ``BaseHTTPMiddleware``)
    because BaseHTTPMiddleware adds a streaming buffer that complicates the
    later streaming-response work in S-015 without giving us anything we need.
    Pure ASGI is also faster.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # Lifespan and websocket scopes pass through without correlation
            # ids; they don't have client-supplied headers in the HTTP sense
            # and the cost of trying is real (lifespan events fire once at
            # startup, where the contextvar would never get reset).
            await self.app(scope, receive, send)
            return

        request_id = _resolve_request_id(scope)
        token = request_id_var.set(request_id)
        try:
            await self.app(scope, receive, _wrap_send(send, request_id))
        finally:
            # Reset even if the inner app raised — leaking a contextvar across
            # requests would corrupt the next request's logs.
            request_id_var.reset(token)


def _resolve_request_id(scope: Scope) -> str:
    """Read the inbound X-Request-ID header or mint a fresh UUIDv4.

    Defense-in-depth: compares header names case-insensitively (spec says
    ASGI delivers lowercase, but we don't trust the wire) and validates the
    decoded value against ``_SAFE_REQUEST_ID_RE``. Anything outside that
    charset (including control characters that would forge log lines)
    drops to a freshly minted UUID.

    ``latin-1`` decoding cannot raise ``UnicodeDecodeError`` (every byte is
    a valid codepoint), so no try/except is needed here — sanitization is
    enforced via the regex check below.
    """
    header_bytes = REQUEST_ID_HEADER.encode("latin-1")
    for name, value in scope.get("headers", []):
        if name.lower() == header_bytes:
            decoded: str = value.decode("latin-1").strip()
            if decoded and _SAFE_REQUEST_ID_RE.match(decoded):
                return decoded
            # Header present but invalid → mint a fresh id below.
            break
    return uuid.uuid4().hex


def _wrap_send(send: Send, request_id: str) -> Send:
    """Inject X-Request-ID into the outbound response headers."""
    header_bytes = (REQUEST_ID_HEADER.encode("latin-1"), request_id.encode("latin-1"))

    async def wrapped_send(message: Message) -> None:
        if message["type"] == "http.response.start":
            # Headers in ASGI are a list of (bytes, bytes) tuples. Build a
            # fresh list to avoid mutating the caller's reference.
            headers = list(message.get("headers", []))
            # Don't duplicate the header if the inner app already set one.
            # Case-insensitive: ASGI spec says lowercase but we don't rely on it.
            already_set = any(name.lower() == header_bytes[0] for name, _ in headers)
            if not already_set:
                headers.append(header_bytes)
                message = {**message, "headers": headers}
        await send(message)

    return wrapped_send
