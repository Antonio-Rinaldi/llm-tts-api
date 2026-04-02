from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from llm_tts_api.config import VoiceConfig


@dataclass(slots=True)
class SynthesisRequest:
    model_name: str
    chunks: list[str]
    voice: VoiceConfig
    response_format: str = "wav"


class TTSProviderStrategy(Protocol):
    provider_name: str

    def synthesize_chunks(self, request: SynthesisRequest) -> list[bytes]:
        ...

