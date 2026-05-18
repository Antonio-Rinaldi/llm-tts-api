"""Pydantic schemas for the voice-CRUD endpoints (S-025).

Mirrors :class:`llm_tts_api.services.voice_store.records.VoiceRecord` but in
HTTP-edge shape: ``extra="forbid"`` to reject unknown fields, no internal
file/blob path fields exposed to clients (FR-VS-06 / FR-VS-07).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class VoiceCreate(BaseModel):
    """Multipart-JSON ``metadata`` part on POST /v1/tts/voices."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., pattern=r"^[a-z0-9_-]{1,64}$")
    transcript: str = Field(..., min_length=1)
    language: str = Field(..., min_length=1)
    consent_acknowledged: bool
    number_lang: str = ""
    target_db: float = -20.0
    temperature: float = 0.8
    top_p: float = 0.95
    max_sentences_per_chunk: int = 2


class VoiceUpdate(BaseModel):
    """Multipart-JSON ``metadata`` part on PUT /v1/tts/voices/{id}.

    ``id`` is taken from the path; clients cannot change it (FR-VS-08).
    """

    model_config = ConfigDict(extra="forbid")

    transcript: str = Field(..., min_length=1)
    language: str = Field(..., min_length=1)
    consent_acknowledged: bool
    number_lang: str = ""
    target_db: float = -20.0
    temperature: float = 0.8
    top_p: float = 0.95
    max_sentences_per_chunk: int = 2


class VoiceResponse(BaseModel):
    """Full record returned by POST / GET /v1/tts/voices/{id} / PUT."""

    model_config = ConfigDict(extra="forbid")

    id: str
    transcript: str
    language: str
    consent_acknowledged: bool
    number_lang: str
    target_db: float
    temperature: float
    top_p: float
    max_sentences_per_chunk: int
    source: Literal["seed", "crud"]
    created_at: datetime
    updated_at: datetime


class VoiceSummary(BaseModel):
    """Slim record for list endpoint (FR-VS-06): id, language, source, created_at."""

    model_config = ConfigDict(extra="forbid")

    id: str
    language: str
    source: Literal["seed", "crud"]
    created_at: datetime


class VoiceListResponse(BaseModel):
    """Envelope for GET /v1/tts/voices."""

    model_config = ConfigDict(extra="forbid")

    data: list[VoiceSummary]
