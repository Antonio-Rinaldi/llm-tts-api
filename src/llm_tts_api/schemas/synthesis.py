"""Pydantic request schema for ``POST /v1/tts/synthesize`` (S-013).

Implements FR-EP-02. ``extra="forbid"`` (NFR-MT-04) rejects unknown fields
at the Pydantic layer so typos surface as ``validation_error`` with
``param=<unknown_field>`` rather than being silently dropped.

The ``voice`` field is intentionally optional at the Pydantic layer so
the router can raise the dedicated ``validation_error.voice_required``
(400) envelope instead of Pydantic's generic missing-field 422. The
``input`` field stays required because UAT-EP-05 distinguishes the
"voice missing" error code from generic input validation, while UAT-EP-04
verifies the length-limit branch handled by the router.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SynthesizeRequest(BaseModel):
    """Rich endpoint request body.

    See FR-EP-02 for the field inventory. Streaming and trailers (FR-EP-05)
    are wired by S-015; this story persists the ``stream`` boolean on the
    request so the handler signature is stable before that work lands.
    """

    model_config = ConfigDict(extra="forbid")

    input: str = Field(..., description="Text to synthesize.")
    voice: str | None = Field(
        default=None,
        description="Voice id resolved against the voice store (required at the handler).",
    )
    provider: str | None = Field(default=None, description="Optional provider override.")
    model: str | None = Field(default=None, description="Optional model override.")
    response_format: Literal["wav"] = Field(default="wav")
    stream: bool = Field(default=False, description="Wired by S-015; the S-013 handler buffers.")
    normalize_db: float | None = Field(default=None, description="Per-request RMS target dBFS.")
    max_sentences_per_chunk: int | None = Field(default=None, ge=1)
    language: str | None = None
    number_lang: str | None = None
    temperature: float | None = None
    top_p: float | None = None
