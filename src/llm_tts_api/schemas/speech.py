from pydantic import BaseModel, ConfigDict


class SpeechRequest(BaseModel):
    """OpenAI-compatible speech synthesis request payload.

    ``extra="forbid"`` blocks the rich-endpoint ``preset`` field from
    leaking onto the OpenAI surface — FR-PR-10 mandates the OpenAI path
    always resolves to ``TTS_DEFAULT_PRESET``, preserving the S-018
    byte-identity invariant (NFR-PT-05). UAT-PR-07 verifies the 422.
    """

    model_config = ConfigDict(extra="forbid")

    model: str
    input: str
    voice: str
    provider: str | None = None
    response_format: str = "wav"
    instructions: str | None = None
    speed: float | None = None
    stream_format: str | None = None
    normalize_db: float | None = None
