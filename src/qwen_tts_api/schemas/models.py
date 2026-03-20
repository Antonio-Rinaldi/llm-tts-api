from pydantic import BaseModel


class ModelObject(BaseModel):
    id: str
    object: str = "model"
    created: int = 0
    owned_by: str = "qwen-tts-api"


class ModelListResponse(BaseModel):
    object: str = "list"
    data: list[ModelObject]
