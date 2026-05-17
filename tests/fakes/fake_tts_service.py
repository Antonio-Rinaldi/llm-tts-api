"""Typed fake implementing the public ``TTSService`` interface.

Pattern mirrors ``llm_image_api/tests/fakes/fake_engine.py``: a small, typed
class with the same public surface as the real service, configurable knobs
for the failure paths, and a ``calls`` list for assertions. Used via
``app.dependency_overrides[get_tts_service] = lambda: fake_service`` rather
than ``monkeypatch.setattr(dependencies, ...)`` — the override is consulted
at request time and survives the idiomatic ``from llm_tts_api.dependencies
import get_tts_service`` import pattern that the routers use.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

from fastapi.responses import FileResponse, StreamingResponse

from llm_tts_api.schemas.speech import SpeechRequest  # type: ignore[import-untyped]


@dataclass(slots=True)
class FakeTTSService:
    """Public surface matches ``llm_tts_api.services.tts_service.TTSService``.

    Knobs:
      raise_on_create_speech: optional exception to raise on next call.
      stream_payload: bytes returned by ``StreamingResponse`` when stream=True.

    The fake records every ``create_speech`` invocation in ``self.calls`` as
    a tuple ``(request, stream)`` so tests can assert call parameters without
    inspecting the response body.
    """

    raise_on_create_speech: Exception | None = None
    stream_payload: bytes = b"FAKE-WAV-BYTES"
    calls: list[tuple[SpeechRequest, bool]] = field(default_factory=list)

    def create_speech(
        self, request: SpeechRequest, stream: bool = False
    ) -> FileResponse | StreamingResponse:
        """Pretend to synthesize speech; return a stub WAV response."""
        self.calls.append((request, stream))
        if self.raise_on_create_speech is not None:
            raise self.raise_on_create_speech
        # ``FileResponse`` requires a real file on disk and we deliberately
        # never write one (matches AM-01-style statelessness for tests).
        # All test paths that need to introspect the response body should
        # send stream=True so the StreamingResponse path is taken.
        return StreamingResponse(
            io.BytesIO(self.stream_payload),
            media_type="audio/wav",
        )

    def __getattr__(self, name: str) -> Any:
        """Tolerate any non-public attribute access expected by callers.

        TTSService exposes private helpers (``_resolver``, ``_synthesizer``)
        that tests should not poke at; if some legacy test does, surface a
        clear AttributeError instead of an opaque ``object`` failure.
        """
        raise AttributeError(
            f"FakeTTSService has no attribute {name!r}; only the public "
            "TTSService surface is faked. Add a fake for this attribute "
            "in tests/fakes/fake_tts_service.py if a new test needs it."
        )


__all__ = ["FakeTTSService"]
