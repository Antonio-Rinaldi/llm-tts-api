from pydantic import BaseModel


class ErrorDetail(BaseModel):
    """OpenAI-style error details payload."""

    message: str
    type: str
    param: str | None = None
    code: str


class ErrorEnvelope(BaseModel):
    """Envelope wrapping ``ErrorDetail`` under ``error`` key."""

    error: ErrorDetail
