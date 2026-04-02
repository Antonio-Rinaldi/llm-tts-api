from fastapi import APIRouter, Depends

from llm_tts_api.dependencies import get_model_registry
from llm_tts_api.schemas.models import ModelListResponse
from llm_tts_api.services.model_registry import ModelRegistry

router = APIRouter(prefix="/v1", tags=["models"])


@router.get("/models", response_model=ModelListResponse)
def list_models(model_registry: ModelRegistry = Depends(get_model_registry)) -> ModelListResponse:
    return ModelListResponse(data=model_registry.list_models())
