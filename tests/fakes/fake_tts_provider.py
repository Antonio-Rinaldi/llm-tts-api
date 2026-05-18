"""Typed fake :class:`TTSProviderStrategy` for tests.

Returns a deterministic, parseable WAV payload per chunk so router tests
can assert real ``X-Chunks`` / ``X-Total-Duration-Ms`` headers without
loading a heavy TTS model. The fake records every ``synthesize_chunks``
invocation so tests can assert override-propagation (T7).
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass, field
from typing import Any

from llm_tts_api.engine import Device
from llm_tts_api.services.tts_providers.base import SynthesisRequest


def _silent_wav(milliseconds: int = 100, frame_rate: int = 16000) -> bytes:
    """Return a tiny mono PCM16 WAV payload of the given length."""
    n_frames = max(1, int(frame_rate * (milliseconds / 1000)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(frame_rate)
        writer.writeframes(b"\x00\x00" * n_frames)
    return buf.getvalue()


@dataclass
class FakeTTSProvider:
    """Implements the :class:`TTSProviderStrategy` Protocol for tests."""

    provider_name: str = "mlx_audio"
    supports_devices: frozenset[Device] = field(
        default_factory=lambda: frozenset({"mps", "cuda", "cpu"})
    )
    calls: list[SynthesisRequest] = field(default_factory=list)
    chunk_ms: int = 100

    def synthesize_chunks(self, request: SynthesisRequest) -> list[bytes]:
        """Return one tiny WAV payload per chunk in the request."""
        self.calls.append(request)
        return [_silent_wav(milliseconds=self.chunk_ms) for _ in request.chunks]

    def preload(self, model_name: str) -> None:
        """No-op preload so the test fixture can call it during setup."""
        _ = model_name

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(f"FakeTTSProvider has no attribute {name!r}")
