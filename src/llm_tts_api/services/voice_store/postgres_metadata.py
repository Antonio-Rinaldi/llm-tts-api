"""Postgres-backed voice metadata repository (optional ``[postgres]`` extra).

Implements the :class:`VoiceMetadataRepository` Protocol from S-022 against
PostgreSQL via ``psycopg`` (v3) async. This module is NOT exported from
``llm_tts_api.services.voice_store`` — the selector in ``dependencies.py``
imports it lazily so the default install path never touches ``psycopg``
(NFR-ST-01/02).

Schema is created lazily on the first repository call via ``CREATE TABLE
IF NOT EXISTS`` so cold starts against a fresh database succeed without an
out-of-band migration step (FR-VS-01 acceptance: "idempotent table creation").
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from llm_tts_api.services.voice_store.errors import (
    VoiceAlreadyExistsError,
    VoiceNotFoundError,
)
from llm_tts_api.services.voice_store.records import VoiceRecord, validate_voice_id

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS voice_records (
    id TEXT PRIMARY KEY,
    transcript TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL,
    consent_acknowledged BOOLEAN NOT NULL,
    number_lang TEXT NOT NULL DEFAULT '',
    target_db DOUBLE PRECISION NOT NULL DEFAULT -20.0,
    temperature DOUBLE PRECISION NOT NULL DEFAULT 0.8,
    top_p DOUBLE PRECISION NOT NULL DEFAULT 0.95,
    max_sentences_per_chunk INTEGER NOT NULL DEFAULT 2,
    source TEXT NOT NULL DEFAULT 'crud',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_COLUMNS = (
    "id, transcript, language, consent_acknowledged, number_lang, "
    "target_db, temperature, top_p, max_sentences_per_chunk, source, "
    "created_at, updated_at"
)


def _row_to_record(row: dict[str, Any]) -> VoiceRecord:
    created = row["created_at"]
    updated = row["updated_at"]
    if isinstance(created, datetime) and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if isinstance(updated, datetime) and updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return VoiceRecord(
        id=row["id"],
        transcript=row["transcript"],
        language=row["language"],
        consent_acknowledged=bool(row["consent_acknowledged"]),
        number_lang=row["number_lang"],
        target_db=float(row["target_db"]),
        temperature=float(row["temperature"]),
        top_p=float(row["top_p"]),
        max_sentences_per_chunk=int(row["max_sentences_per_chunk"]),
        source=row["source"],
        created_at=created,
        updated_at=updated,
    )


class PostgresMetadataRepository:
    """Voice metadata persisted in a single ``voice_records`` table."""

    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise ValueError("PostgresMetadataRepository requires a non-empty DSN")
        self._dsn = dsn
        self._init_lock = asyncio.Lock()
        self._initialized = False

    async def _ensure_schema(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            async with await psycopg.AsyncConnection.connect(self._dsn) as conn:
                async with conn.cursor() as cur:
                    await cur.execute(_CREATE_TABLE_SQL)
                await conn.commit()
            self._initialized = True

    async def _connect(self) -> psycopg.AsyncConnection[Any]:
        await self._ensure_schema()
        return await psycopg.AsyncConnection.connect(self._dsn)

    async def list(self) -> list[VoiceRecord]:
        async with await self._connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(f"SELECT {_COLUMNS} FROM voice_records ORDER BY id")
            rows = await cur.fetchall()
        return [_row_to_record(row) for row in rows]

    async def get(self, voice_id: str) -> VoiceRecord:
        validate_voice_id(voice_id)
        async with await self._connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"SELECT {_COLUMNS} FROM voice_records WHERE id = %s",
                (voice_id,),
            )
            row = await cur.fetchone()
        if row is None:
            raise VoiceNotFoundError(voice_id)
        return _row_to_record(row)

    async def exists(self, voice_id: str) -> bool:
        validate_voice_id(voice_id)
        async with await self._connect() as conn, conn.cursor() as cur:
            await cur.execute("SELECT 1 FROM voice_records WHERE id = %s", (voice_id,))
            row = await cur.fetchone()
        return row is not None

    async def create(self, record: VoiceRecord) -> VoiceRecord:
        validate_voice_id(record.id)
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute(
                        f"INSERT INTO voice_records ({_COLUMNS}) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (
                            record.id,
                            record.transcript,
                            record.language,
                            record.consent_acknowledged,
                            record.number_lang,
                            record.target_db,
                            record.temperature,
                            record.top_p,
                            record.max_sentences_per_chunk,
                            record.source,
                            record.created_at,
                            record.updated_at,
                        ),
                    )
                except psycopg.errors.UniqueViolation as exc:
                    await conn.rollback()
                    raise VoiceAlreadyExistsError(record.id) from exc
            await conn.commit()
        return record

    async def update(self, record: VoiceRecord) -> VoiceRecord:
        validate_voice_id(record.id)
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE voice_records SET "
                    "transcript = %s, language = %s, consent_acknowledged = %s, "
                    "number_lang = %s, target_db = %s, temperature = %s, "
                    "top_p = %s, max_sentences_per_chunk = %s, source = %s, "
                    "created_at = %s, updated_at = %s "
                    "WHERE id = %s",
                    (
                        record.transcript,
                        record.language,
                        record.consent_acknowledged,
                        record.number_lang,
                        record.target_db,
                        record.temperature,
                        record.top_p,
                        record.max_sentences_per_chunk,
                        record.source,
                        record.created_at,
                        record.updated_at,
                        record.id,
                    ),
                )
                rowcount = cur.rowcount
            if rowcount == 0:
                await conn.rollback()
                raise VoiceNotFoundError(record.id)
            await conn.commit()
        return record

    async def delete(self, voice_id: str) -> None:
        validate_voice_id(voice_id)
        async with await self._connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM voice_records WHERE id = %s", (voice_id,))
                rowcount = cur.rowcount
            if rowcount == 0:
                await conn.rollback()
                raise VoiceNotFoundError(voice_id)
            await conn.commit()
