"""Default filesystem-backed metadata repository (single JSON document).

Layout under ``TTS_VOICE_STORE_DIR``:

    <TTS_VOICE_STORE_DIR>/metadata.json   # all VoiceRecord rows, keyed by id

Atomicity strategy: every write writes a new temp file in the same directory
via :func:`tempfile.NamedTemporaryFile` (so the rename stays on one
filesystem) then ``os.replace`` to swap it into place. Reads load the file
afresh — no in-memory cache — so multi-worker deploys see consistent state.
An ``asyncio.Lock`` serializes the read-modify-write sequence within a single
process so two concurrent ``create`` calls cannot interleave.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from llm_tts_api.services.voice_store.errors import (
    VoiceAlreadyExistsError,
    VoiceNotFoundError,
)
from llm_tts_api.services.voice_store.records import VoiceRecord, validate_voice_id

_METADATA_FILENAME = "metadata.json"


def _serialize_record(record: VoiceRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "transcript": record.transcript,
        "language": record.language,
        "consent_acknowledged": record.consent_acknowledged,
        "number_lang": record.number_lang,
        "target_db": record.target_db,
        "temperature": record.temperature,
        "top_p": record.top_p,
        "max_sentences_per_chunk": record.max_sentences_per_chunk,
        "source": record.source,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _deserialize_record(raw: dict[str, Any]) -> VoiceRecord:
    return VoiceRecord(
        id=str(raw["id"]),
        transcript=str(raw.get("transcript", "")),
        language=str(raw["language"]),
        consent_acknowledged=bool(raw.get("consent_acknowledged", False)),
        number_lang=str(raw.get("number_lang", "")),
        target_db=float(raw.get("target_db", -20.0)),
        temperature=float(raw.get("temperature", 0.8)),
        top_p=float(raw.get("top_p", 0.95)),
        max_sentences_per_chunk=int(raw.get("max_sentences_per_chunk", 2)),
        source=raw.get("source", "crud"),
        created_at=_parse_dt(raw.get("created_at")),
        updated_at=_parse_dt(raw.get("updated_at")),
    )


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value)
    return datetime.now(tz=timezone.utc)


class FsJsonMetadataRepository:
    """Single-file JSON metadata store rooted at ``store_dir``."""

    def __init__(self, store_dir: Path) -> None:
        self._store_dir = Path(store_dir)
        self._metadata_path = self._store_dir / _METADATA_FILENAME
        self._lock = asyncio.Lock()
        self._store_dir.mkdir(parents=True, exist_ok=True)

    @property
    def metadata_path(self) -> Path:
        """Return the JSON document path (for diagnostics / tests)."""
        return self._metadata_path

    def _load_all(self) -> dict[str, VoiceRecord]:
        if not self._metadata_path.exists():
            return {}
        raw_text = self._metadata_path.read_text(encoding="utf-8")
        if not raw_text.strip():
            return {}
        raw_data = json.loads(raw_text)
        if not isinstance(raw_data, dict):
            raise ValueError(
                f"{self._metadata_path} must contain a JSON object (got {type(raw_data).__name__})"
            )
        return {key: _deserialize_record(value) for key, value in raw_data.items()}

    def _atomic_write(self, records: dict[str, VoiceRecord]) -> None:
        payload = {key: _serialize_record(value) for key, value in records.items()}
        encoded = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        # Tempfile in the SAME directory keeps ``os.replace`` on one filesystem
        # (rename across mounts is not atomic). ``delete=False`` because we
        # own the cleanup via ``os.replace`` (or the ``except`` branch).
        fd = tempfile.NamedTemporaryFile(  # noqa: SIM115 — cleanup owned via os.replace / except
            dir=self._store_dir,
            prefix=".metadata.",
            suffix=".tmp",
            delete=False,
        )
        tmp_path = Path(fd.name)
        try:
            with fd:
                fd.write(encoded)
                fd.flush()
                os.fsync(fd.fileno())
            os.replace(tmp_path, self._metadata_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    async def list(self) -> list[VoiceRecord]:
        async with self._lock:
            return list(self._load_all().values())

    async def get(self, voice_id: str) -> VoiceRecord:
        validate_voice_id(voice_id)
        async with self._lock:
            records = self._load_all()
            if voice_id not in records:
                raise VoiceNotFoundError(voice_id)
            return records[voice_id]

    async def exists(self, voice_id: str) -> bool:
        validate_voice_id(voice_id)
        async with self._lock:
            return voice_id in self._load_all()

    async def create(self, record: VoiceRecord) -> VoiceRecord:
        validate_voice_id(record.id)
        async with self._lock:
            records = self._load_all()
            if record.id in records:
                raise VoiceAlreadyExistsError(record.id)
            records[record.id] = record
            self._atomic_write(records)
            return record

    async def update(self, record: VoiceRecord) -> VoiceRecord:
        validate_voice_id(record.id)
        async with self._lock:
            records = self._load_all()
            if record.id not in records:
                raise VoiceNotFoundError(record.id)
            records[record.id] = record
            self._atomic_write(records)
            return record

    async def delete(self, voice_id: str) -> None:
        validate_voice_id(voice_id)
        async with self._lock:
            records = self._load_all()
            if voice_id not in records:
                raise VoiceNotFoundError(voice_id)
            del records[voice_id]
            self._atomic_write(records)
