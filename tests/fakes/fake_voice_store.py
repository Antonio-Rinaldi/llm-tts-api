"""In-memory fakes for the S-022 voice-store Protocols.

The fakes implement the same Protocol surface as the FS-default repos but
hold state in dicts. Tests that want to exercise router/lifespan wiring can
use these without touching the filesystem.
"""

from __future__ import annotations

from llm_tts_api.services.voice_store import (
    VoiceAlreadyExistsError,
    VoiceNotFoundError,
    VoiceRecord,
    validate_voice_id,
)


class FakeVoiceMetadataRepository:
    """Dict-backed VoiceMetadataRepository for tests."""

    def __init__(self) -> None:
        self._records: dict[str, VoiceRecord] = {}

    async def list(self) -> list[VoiceRecord]:
        return list(self._records.values())

    async def get(self, voice_id: str) -> VoiceRecord:
        validate_voice_id(voice_id)
        if voice_id not in self._records:
            raise VoiceNotFoundError(voice_id)
        return self._records[voice_id]

    async def exists(self, voice_id: str) -> bool:
        validate_voice_id(voice_id)
        return voice_id in self._records

    async def create(self, record: VoiceRecord) -> VoiceRecord:
        validate_voice_id(record.id)
        if record.id in self._records:
            raise VoiceAlreadyExistsError(record.id)
        self._records[record.id] = record
        return record

    async def update(self, record: VoiceRecord) -> VoiceRecord:
        validate_voice_id(record.id)
        if record.id not in self._records:
            raise VoiceNotFoundError(record.id)
        self._records[record.id] = record
        return record

    async def delete(self, voice_id: str) -> None:
        validate_voice_id(voice_id)
        if voice_id not in self._records:
            raise VoiceNotFoundError(voice_id)
        del self._records[voice_id]


class FakeVoiceBlobRepository:
    """Dict-backed VoiceBlobRepository for tests."""

    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    async def put(self, voice_id: str, data: bytes) -> None:
        validate_voice_id(voice_id)
        self._blobs[voice_id] = bytes(data)

    async def get(self, voice_id: str) -> bytes:
        validate_voice_id(voice_id)
        if voice_id not in self._blobs:
            raise VoiceNotFoundError(voice_id)
        return self._blobs[voice_id]

    async def exists(self, voice_id: str) -> bool:
        validate_voice_id(voice_id)
        return voice_id in self._blobs

    async def delete(self, voice_id: str) -> None:
        validate_voice_id(voice_id)
        if voice_id not in self._blobs:
            raise VoiceNotFoundError(voice_id)
        del self._blobs[voice_id]
