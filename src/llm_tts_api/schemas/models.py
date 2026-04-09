from pydantic import BaseModel


class ModelObject(BaseModel):
    """OpenAI-compatible model descriptor."""

    id: str
    object: str = "model"
    created: int = 0
    owned_by: str = "llm-tts-api"


class ModelListResponse(BaseModel):
    """OpenAI-compatible model list response envelope."""

    object: str = "list"
    data: list[ModelObject]
