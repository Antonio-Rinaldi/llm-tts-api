from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException


@dataclass(slots=True)
class OpenAIError:
    """Structured OpenAI-style error payload."""

    message: str
    type: str
    code: str
    param: str | None = None

    def as_dict(self) -> dict[str, object]:
        """Serialize to the OpenAI-compatible API envelope."""
        return {
            "error": {
                "message": self.message,
                "type": self.type,
                "param": self.param,
                "code": self.code,
            }
        }


class OpenAIHTTPException(HTTPException):
    """HTTPException wrapper that always carries an OpenAI error payload."""

    def __init__(self, status_code: int, error: OpenAIError) -> None:
        """Initialize exception with HTTP code and standardized error payload."""
        super().__init__(status_code=status_code, detail=error.as_dict()["error"])


def invalid_request(
    message: str, param: str | None = None, code: str = "invalid_parameter"
) -> OpenAIHTTPException:
    """Create a standardized 400 invalid request error."""
    return OpenAIHTTPException(
        status_code=400,
        error=OpenAIError(
            message=message,
            type="invalid_request_error",
            param=param,
            code=code,
        ),
    )


def not_implemented(message: str) -> OpenAIHTTPException:
    """Create a standardized 501 not implemented error."""
    return OpenAIHTTPException(
        status_code=501,
        error=OpenAIError(
            message=message,
            type="not_implemented_error",
            param=None,
            code="not_implemented",
        ),
    )


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
    """Create a standardized 500 server error."""
    return OpenAIHTTPException(
        status_code=500,
        error=OpenAIError(
            message=message,
            type="server_error",
            param=None,
            code="internal_error",
        ),
    )
