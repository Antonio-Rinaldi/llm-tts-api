from __future__ import annotations

from llm_tts_api.config import Settings
from llm_tts_api.schemas.models import ModelObject


class ModelRegistry:
    """Model and provider resolution helper for API requests."""

    def __init__(self, settings: Settings) -> None:
        """Store the validated settings object used for model policy checks."""
        self.settings = settings

    def list_models(self) -> list[ModelObject]:
        """Return a deduplicated list of all visible models across providers."""
        all_models = [
            *self.settings.tts_mlx_audio_model_allowed,
            *self.settings.tts_voxtral_model_allowed,
            *self.settings.tts_vllm_omni_model_allowed,
            *self.settings.stt_model_allowed,
        ]
        ids = list(dict.fromkeys(all_models))
        return [ModelObject(id=model_id) for model_id in ids]

    def is_allowed_tts_model(self, model: str, provider: str) -> bool:
        """Check whether a TTS model is allowed for a specific provider."""
        return model in self.settings.tts_model_allowed_for_provider(provider)

    def resolve_tts_model(self, model: str | None, provider: str) -> str:
        """Resolve explicit request model or fall back to provider default."""
        if model:
            return model
        return self.settings.tts_model_default_for_provider(provider)

    def resolve_tts_target(self, model: str | None, provider: str | None) -> tuple[str, str]:
        """Resolve final (model, provider) tuple with provider validation."""
        resolved_provider = provider.strip().lower() if provider else self.settings.tts_provider
        if resolved_provider not in {"mlx_audio", "voxtral", "vllm-omni"}:
            raise ValueError("provider must be one of 'mlx_audio', 'voxtral', or 'vllm-omni'")
        resolved_model = self.resolve_tts_model(model, resolved_provider)
        return resolved_model, resolved_provider
