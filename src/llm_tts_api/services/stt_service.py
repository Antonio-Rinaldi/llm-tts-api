from __future__ import annotations

from llm_tts_api.errors import not_implemented


class STTService:
    """Abstraction point for future transcription/translation backend integration."""

    def create_transcription(self):
        """Placeholder transcription action following OpenAI-compatible route contract."""
        raise not_implemented("Endpoint '/v1/audio/transcriptions' is not implemented yet")

    def create_translation(self):
        """Placeholder translation action following OpenAI-compatible route contract."""
        raise not_implemented("Endpoint '/v1/audio/translations' is not implemented yet")
