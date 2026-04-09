from pydantic import BaseModel


class SpeechRequest(BaseModel):
    """OpenAI-compatible speech synthesis request payload."""

    model: str
    input: str
    voice: str
    provider: str | None = None
    response_format: str = "wav"
    instructions: str | None = None
    speed: float | None = None
    stream_format: str | None = None
