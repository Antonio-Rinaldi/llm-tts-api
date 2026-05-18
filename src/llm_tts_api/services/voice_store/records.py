"""Voice record schema + id validation.

The :class:`VoiceRecord` dataclass is the single source of truth for voice
metadata across the FS, Postgres, and S3 backends. Repository implementations
serialize/deserialize this exact shape; CRUD endpoints and seed ingestion
construct/consume it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

VOICE_ID_PATTERN: str = r"^[a-z0-9_-]{1,64}$"
VOICE_ID_REGEX: re.Pattern[str] = re.compile(VOICE_ID_PATTERN)

VoiceSource = Literal["seed", "crud"]


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass(slots=True)
class VoiceRecord:
    """Single voice metadata record.

    Fields mirror :class:`llm_tts_api.config.VoiceConfig` for the legacy
    synthesis-time consumers, plus voice-store metadata (id, consent flag,
    provenance, timestamps). Blob bytes are NOT part of this record — they
    are stored separately via :class:`VoiceBlobRepository`.
    """

    id: str
    transcript: str
    language: str
    consent_acknowledged: bool
    number_lang: str = ""
    target_db: float = -20.0
    temperature: float = 0.8
    top_p: float = 0.95
    max_sentences_per_chunk: int = 2
    source: VoiceSource = "crud"
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)


def validate_voice_id(voice_id: str) -> str:
    """Validate a voice id against :data:`VOICE_ID_PATTERN`.

    Returns the id on success so callers can use it inline. Raises
    :class:`VoiceIdInvalidError` on rejection — the regex anchors `^$`,
    bans path separators, dots, and anything outside `[a-z0-9_-]`.
    """
    from llm_tts_api.services.voice_store.errors import VoiceIdInvalidError

    if not isinstance(voice_id, str) or not VOICE_ID_REGEX.match(voice_id):
        raise VoiceIdInvalidError(f"voice id {voice_id!r} must match {VOICE_ID_PATTERN}")
    return voice_id
