from pydantic import BaseModel


class TranscriptionResponse(BaseModel):
    text: str


class TranslationResponse(BaseModel):
    text: str
