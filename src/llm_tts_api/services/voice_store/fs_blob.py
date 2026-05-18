"""Default filesystem-backed blob repository.

Layout under ``TTS_VOICE_STORE_DIR``:

    <TTS_VOICE_STORE_DIR>/blobs/<voice_id>.wav

Path safety: every method validates ``voice_id`` against the regex
``^[a-z0-9_-]{1,64}$`` BEFORE touching the filesystem. The ``blobs/``
subdirectory keeps blob files out of the way of the metadata document and
makes a future ``rm -rf blobs/`` cleanup unambiguous.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from llm_tts_api.services.voice_store.errors import VoiceNotFoundError
from llm_tts_api.services.voice_store.records import validate_voice_id

_BLOBS_SUBDIR = "blobs"
_BLOB_SUFFIX = ".wav"


class FsBlobRepository:
    """Blob bytes stored as ``<store_dir>/blobs/<voice_id>.wav``."""

    def __init__(self, store_dir: Path) -> None:
        self._store_dir = Path(store_dir)
        self._blobs_dir = self._store_dir / _BLOBS_SUBDIR
        self._lock = asyncio.Lock()
        self._blobs_dir.mkdir(parents=True, exist_ok=True)

    @property
    def blobs_dir(self) -> Path:
        """Return the blob directory path (for diagnostics / tests)."""
        return self._blobs_dir

    def _path_for(self, voice_id: str) -> Path:
        validate_voice_id(voice_id)
        return self._blobs_dir / f"{voice_id}{_BLOB_SUFFIX}"

    async def put(self, voice_id: str, data: bytes) -> None:
        target = self._path_for(voice_id)
        async with self._lock:
            fd = tempfile.NamedTemporaryFile(  # noqa: SIM115 — cleanup owned via os.replace / except
                dir=self._blobs_dir,
                prefix=f".{voice_id}.",
                suffix=".tmp",
                delete=False,
            )
            tmp_path = Path(fd.name)
            try:
                with fd:
                    fd.write(data)
                    fd.flush()
                    os.fsync(fd.fileno())
                os.replace(tmp_path, target)
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise

    async def get(self, voice_id: str) -> bytes:
        target = self._path_for(voice_id)
        if not target.exists():
            raise VoiceNotFoundError(voice_id)
        return target.read_bytes()

    async def exists(self, voice_id: str) -> bool:
        return self._path_for(voice_id).exists()

    async def delete(self, voice_id: str) -> None:
        target = self._path_for(voice_id)
        async with self._lock:
            if not target.exists():
                raise VoiceNotFoundError(voice_id)
            target.unlink()
