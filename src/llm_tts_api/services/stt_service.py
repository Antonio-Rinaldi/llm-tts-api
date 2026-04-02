from __future__ import annotations

from llm_tts_api.errors import not_implemented


class STTService:
    """Abstraction point for future transcription/translation backend integration."""

    def create_transcription(self):
        raise not_implemented("Endpoint '/v1/audio/transcriptions' is not implemented yet")

    def create_translation(self):
        raise not_implemented("Endpoint '/v1/audio/translations' is not implemented yet")
