from __future__ import annotations

from qwen_tts_api.config import Settings
from qwen_tts_api.schemas.models import ModelObject


class ModelRegistry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def list_models(self) -> list[ModelObject]:
        ids: list[str] = []
        for model in [*self.settings.qwen_tts_model_allowed, *self.settings.qwen_stt_model_allowed]:
            if model not in ids:
                ids.append(model)
        return [ModelObject(id=model_id) for model_id in ids]

    def is_allowed_tts_model(self, model: str) -> bool:
        return model in self.settings.qwen_tts_model_allowed

    def resolve_tts_model(self, model: str | None) -> str:
        if model:
            return model
        return self.settings.qwen_tts_model_default
