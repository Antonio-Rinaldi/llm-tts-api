from pydantic import BaseModel


class SpeechRequest(BaseModel):
    model: str
    input: str
    voice: str
    response_format: str = "wav"
    instructions: str | None = None
    speed: float | None = None
    stream_format: str | None = None
