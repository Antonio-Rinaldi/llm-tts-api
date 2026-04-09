from pydantic import BaseModel


class TranscriptionResponse(BaseModel):
    """Placeholder response model for transcription output text."""

    text: str


class TranslationResponse(BaseModel):
    """Placeholder response model for translation output text."""

    text: str
