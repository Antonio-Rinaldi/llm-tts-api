"""Voice-map seed ingestion (S-011).

Bridges the legacy ``voice_map.json`` operator workflow to the voice store
introduced by S-022. At lifespan startup (and on file change), every entry
in the seed file whose ``id`` is NOT already in the metadata repository is
upserted with ``source="seed"`` and ``consent_acknowledged=True`` (operator-
supplied seed is implicit consent per FR-VM-01). CRUD-created records
(``source="crud"``) are never touched.

FR-VM-03 atomicity: the whole file is parsed and validated (schema + ref
audio readable) before any write. If validation fails, NO entries from
that pass are applied and a ``provider_error.voice_seed_ingest_failed``
log line is emitted; the previous store state is preserved.

FR-VM-02 / NFR-OP-05: watchfiles-based reload runs continuously. A
polling fallback (``TTS_VOICE_MAP_WATCH_FORCE_POLLING=1``) is provided
for Docker bind-mount environments where inotify is unreliable (RISK-3).

FR-VM-05: an unset / missing ``TTS_VOICE_MAP_FILE`` is a valid empty
config; the ingestor logs an info line and exits cleanly.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from llm_tts_api.services.config_watcher import ConfigWatcher
from llm_tts_api.services.voice_store.protocols import (
    VoiceBlobRepository,
    VoiceMetadataRepository,
)
from llm_tts_api.services.voice_store.records import VoiceRecord, validate_voice_id

logger = logging.getLogger(__name__)

ERROR_CODE_INGEST_FAILED = "provider_error.voice_seed_ingest_failed"


@dataclass(slots=True, frozen=True)
class _ParsedSeedEntry:
    """One validated voice_map.json entry, ready for upsert."""

    voice_id: str
    ref_audio_path: Path
    transcript: str
    language: str
    number_lang: str
    temperature: float
    top_p: float
    target_db: float
    max_sentences_per_chunk: int


class VoiceSeedIngestor:
    """Run idempotent voice_map.json → voice store ingestion at startup and on change."""

    def __init__(
        self,
        *,
        metadata_repo: VoiceMetadataRepository,
        blob_repo: VoiceBlobRepository,
        seed_file_path: Path | None,
        force_polling: bool = False,
    ) -> None:
        self._metadata_repo = metadata_repo
        self._blob_repo = blob_repo
        self._seed_file_path = seed_file_path
        self._force_polling = force_polling

    @property
    def seed_file_path(self) -> Path | None:
        return self._seed_file_path

    async def ingest_once(self) -> int:
        """Run a single validate-then-write pass. Returns count of new voices ingested.

        FR-VM-03: parse + validate the full file before any write. If any
        entry fails validation, ABORT the pass; the store is untouched
        and ``provider_error.voice_seed_ingest_failed`` is logged.

        FR-VM-01 idempotency: entries whose id is already in the store
        are skipped (regardless of source).
        """
        path = self._seed_file_path
        if path is None or not path.exists():
            logger.info(
                "voice_seed_ingest: no seed file configured (path=%s); skipping",
                path,
            )
            return 0
        try:
            entries = _parse_and_validate(path)
        except _SeedValidationError as exc:
            logger.error(
                "%s reason=%s path=%s",
                ERROR_CODE_INGEST_FAILED,
                exc,
                path,
            )
            return 0

        created = 0
        for entry in entries:
            if await self._metadata_repo.exists(entry.voice_id):
                continue
            try:
                audio_bytes = entry.ref_audio_path.read_bytes()
            except OSError as exc:
                # Validation already confirmed the file existed and was
                # readable; a race between validate and read still aborts
                # the pass per FR-VM-03 atomicity.
                logger.error(
                    "%s reason=read_failed voice_id=%s path=%s error=%s",
                    ERROR_CODE_INGEST_FAILED,
                    entry.voice_id,
                    entry.ref_audio_path,
                    exc,
                )
                return created
            record = VoiceRecord(
                id=entry.voice_id,
                transcript=entry.transcript,
                language=entry.language,
                consent_acknowledged=True,
                number_lang=entry.number_lang,
                target_db=entry.target_db,
                temperature=entry.temperature,
                top_p=entry.top_p,
                max_sentences_per_chunk=entry.max_sentences_per_chunk,
                source="seed",
            )
            await self._blob_repo.put(entry.voice_id, audio_bytes)
            await self._metadata_repo.create(record)
            created += 1
            logger.info("voice_seed_ingest: created voice_id=%s", entry.voice_id)
        return created

    async def watch_and_ingest(self) -> None:
        """Long-running task: re-run ingestion on every seed-file change.

        FR-VM-02 / NFR-OP-05: detect file change within ~2 s. Delegates
        to :class:`ConfigWatcher` (S-029 T1) which owns the watchfiles +
        polling-fallback mechanic. The polling fallback is enabled via
        ``force_polling=True`` for Docker bind-mount environments where
        inotify is unreliable (RISK-3).
        """
        watcher = ConfigWatcher(
            path=self._seed_file_path,
            on_change=self._on_seed_changed,
            force_polling=self._force_polling,
        )
        await watcher.watch()

    async def _on_seed_changed(self) -> None:
        await self.ingest_once()


class _SeedValidationError(ValueError):
    """Internal: raised by ``_parse_and_validate`` to abort a whole pass."""


def _parse_and_validate(path: Path) -> list[_ParsedSeedEntry]:
    """Read+validate the whole seed file. Raises on first failure (FR-VM-03)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise _SeedValidationError(f"invalid_json: {exc}") from exc
    if not isinstance(raw, dict):
        raise _SeedValidationError("root_not_object")

    entries: list[_ParsedSeedEntry] = []
    for voice_id, body in raw.items():
        entries.append(_validate_entry(voice_id, body))
    return entries


def _validate_entry(voice_id: object, body: object) -> _ParsedSeedEntry:
    if not isinstance(voice_id, str):
        raise _SeedValidationError(f"voice_id_not_string: {voice_id!r}")
    try:
        validate_voice_id(voice_id)
    except Exception as exc:
        raise _SeedValidationError(f"voice_id_invalid voice_id={voice_id!r} reason={exc}") from exc
    if not isinstance(body, dict):
        raise _SeedValidationError(f"entry_not_object voice_id={voice_id!r}")

    ref_audio_path = body.get("ref_audio_path")
    transcript = body.get("ref_text", "")
    language = body.get("language")
    number_lang = body.get("number_lang", "")
    temperature = body.get("temperature", 0.8)
    top_p = body.get("top_p", 0.95)
    target_db = body.get("target_db", -20.0)
    max_sentences = body.get("max_sentences_per_chunk", 2)

    if not isinstance(ref_audio_path, str) or not ref_audio_path:
        raise _SeedValidationError(f"missing_ref_audio_path voice_id={voice_id!r}")
    if not isinstance(transcript, str):
        raise _SeedValidationError(f"transcript_not_string voice_id={voice_id!r}")
    if not isinstance(language, str) or not language.strip():
        raise _SeedValidationError(f"missing_language voice_id={voice_id!r}")
    if not isinstance(number_lang, str):
        raise _SeedValidationError(f"number_lang_not_string voice_id={voice_id!r}")
    if not isinstance(temperature, (int, float)) or not 0.0 <= float(temperature) <= 2.0:
        raise _SeedValidationError(f"temperature_invalid voice_id={voice_id!r}")
    if not isinstance(top_p, (int, float)) or not 0.0 < float(top_p) <= 1.0:
        raise _SeedValidationError(f"top_p_invalid voice_id={voice_id!r}")
    if not isinstance(target_db, (int, float)):
        raise _SeedValidationError(f"target_db_not_numeric voice_id={voice_id!r}")
    if not isinstance(max_sentences, int) or max_sentences < 1:
        raise _SeedValidationError(f"max_sentences_per_chunk_invalid voice_id={voice_id!r}")

    audio_path = Path(ref_audio_path)
    if not audio_path.exists() or not audio_path.is_file():
        raise _SeedValidationError(f"ref_audio_missing voice_id={voice_id!r} path={ref_audio_path}")
    if not os.access(audio_path, os.R_OK):
        raise _SeedValidationError(
            f"ref_audio_unreadable voice_id={voice_id!r} path={ref_audio_path}"
        )

    return _ParsedSeedEntry(
        voice_id=voice_id,
        ref_audio_path=audio_path,
        transcript=transcript,
        language=language,
        number_lang=number_lang,
        temperature=float(temperature),
        top_p=float(top_p),
        target_db=float(target_db),
        max_sentences_per_chunk=max_sentences,
    )


def resolve_seed_file_path() -> Path | None:
    """Return the ``TTS_VOICE_MAP_FILE`` path if set and present, else ``None``.

    Mirrors :func:`Settings._resolve_voice_map_path` but returns ``None``
    for missing files instead of raising — the seed ingestor treats
    "not configured" and "configured-but-absent" the same per FR-VM-05.
    """
    raw = os.environ.get("TTS_VOICE_MAP_FILE", "").strip()
    if not raw:
        return None
    candidate = Path(raw)
    if not candidate.exists() or not candidate.is_file():
        return None
    return candidate


def force_polling_from_env() -> bool:
    """Return ``True`` when watchfiles should use its polling backend (RISK-3).

    Set ``TTS_VOICE_MAP_WATCH_FORCE_POLLING=1`` inside Docker / on bind-
    mounts where inotify-based watching is unreliable.
    """
    raw = os.environ.get("TTS_VOICE_MAP_WATCH_FORCE_POLLING", "").strip().lower()
    return raw in {"1", "true", "yes"}
