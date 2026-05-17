from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from llm_tts_api.config import VoiceConfig
from llm_tts_api.engine import Device


@dataclass(slots=True, frozen=True)
class GenerationOptions:
    """Provider-agnostic generation tuning values."""

    language: str
    temperature: float
    top_p: float


@dataclass(slots=True, frozen=True)
class SynthesisRequest:
    """Normalized request passed from service layer to a provider strategy."""

    model_name: str
    chunks: list[str]
    voice: VoiceConfig
    voice_name: str = ""
    response_format: str = "wav"
    generation: GenerationOptions | None = None


class TTSProviderStrategy(Protocol):
    """Strategy contract implemented by every TTS provider backend.

    ``supports_devices`` declares the set of inference devices a provider
    can run on. The auto-selection layer (S-006 / FR-HW-04..07) uses this
    capability set to pick a viable provider for the detected device, and
    to validate any explicit ``TTS_PROVIDER`` env override.
    """

    provider_name: str
    supports_devices: frozenset[Device]

    def synthesize_chunks(self, request: SynthesisRequest) -> list[bytes]:
        """Generate one WAV payload per input chunk."""
        ...
