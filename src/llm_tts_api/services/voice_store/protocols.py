"""Repository Protocols for voice metadata and audio blobs.

These two Protocols are the contract Step-2 stories (Postgres + S3 backends)
implement and Step-3/Sprint-4 stories (CRUD endpoints, seed ingestion, rich
endpoint) consume. The shapes are intentionally narrow: every method is
async, every voice id is validated against ``VOICE_ID_PATTERN`` by the
implementation, and ``get/put/delete`` return predictable shapes regardless
of backend.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from llm_tts_api.services.voice_store.records import VoiceRecord


@runtime_checkable
class VoiceMetadataRepository(Protocol):
    """Persistence boundary for :class:`VoiceRecord` metadata."""

    async def list(self) -> list[VoiceRecord]:
        """Return every record. Order is unspecified."""
        ...

    async def get(self, voice_id: str) -> VoiceRecord:
        """Return the record for ``voice_id``.

        Raises :class:`VoiceNotFoundError` if absent; raises
        :class:`VoiceIdInvalidError` if the id is malformed.
        """
        ...

    async def exists(self, voice_id: str) -> bool:
        """Return ``True`` iff a record exists for ``voice_id``.

        Raises :class:`VoiceIdInvalidError` on malformed ids.
        """
        ...

    async def create(self, record: VoiceRecord) -> VoiceRecord:
        """Insert ``record`` and return the persisted copy.

        Raises :class:`VoiceAlreadyExistsError` if ``record.id`` is taken.
        """
        ...

    async def update(self, record: VoiceRecord) -> VoiceRecord:
        """Replace the record identified by ``record.id`` and return it.

        Raises :class:`VoiceNotFoundError` if no record exists.
        """
        ...

    async def delete(self, voice_id: str) -> None:
        """Remove the record for ``voice_id``.

        Raises :class:`VoiceNotFoundError` if absent.
        """
        ...


@runtime_checkable
class VoiceBlobRepository(Protocol):
    """Persistence boundary for reference-audio blob bytes.

    Single shape across backends: ``put`` accepts ``bytes`` and ``get``
    returns ``bytes``. Streaming is not part of S-022's contract —
    Sprint-4's rich endpoint can layer a streaming adapter on top.
    """

    async def put(self, voice_id: str, data: bytes) -> None:
        """Store ``data`` under ``voice_id``, replacing any prior blob.

        Raises :class:`VoiceIdInvalidError` on malformed ids.
        """
        ...

    async def get(self, voice_id: str) -> bytes:
        """Return the blob bytes for ``voice_id``.

        Raises :class:`VoiceNotFoundError` if absent.
        """
        ...

    async def exists(self, voice_id: str) -> bool:
        """Return ``True`` iff a blob exists for ``voice_id``."""
        ...

    async def delete(self, voice_id: str) -> None:
        """Remove the blob for ``voice_id``.

        Raises :class:`VoiceNotFoundError` if absent.
        """
        ...
