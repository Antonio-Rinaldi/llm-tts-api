from pydantic import BaseModel, Field


class SpeechRequest(BaseModel):
    model: str
    input: str = Field(..., max_length=4096)
    voice: str
    response_format: str = "wav"
    instructions: str | None = None
    speed: float | None = None
    stream_format: str | None = None
