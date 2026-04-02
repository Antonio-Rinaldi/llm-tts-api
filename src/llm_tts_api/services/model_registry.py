from __future__ import annotations

from llm_tts_api.config import Settings
from llm_tts_api.schemas.models import ModelObject


class ModelRegistry:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def list_models(self) -> list[ModelObject]:
        ids: list[str] = []
        for model in [*self.settings.tts_model_allowed, *self.settings.stt_model_allowed]:
            if model not in ids:
                ids.append(model)
        return [ModelObject(id=model_id) for model_id in ids]

    def is_allowed_tts_model(self, model: str) -> bool:
        return model in self.settings.tts_model_allowed

    def resolve_tts_model(self, model: str | None) -> str:
        if model:
            return model
        return self.settings.tts_model_default

    def resolve_tts_target(self, model: str | None, provider: str | None) -> tuple[str, str]:
        resolved_model = self.resolve_tts_model(model)
        if provider:
            resolved_provider = provider.strip().lower()
        else:
            resolved_provider = self.settings.tts_provider
        return resolved_model, resolved_provider
