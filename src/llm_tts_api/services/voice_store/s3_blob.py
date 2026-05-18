"""Optional S3-backed blob repository (S-024).

Implements :class:`VoiceBlobRepository` against an S3-compatible object
store (AWS S3, MinIO, …). Available only when the ``[s3]`` optional
extra is installed: importing this module without ``aiobotocore`` raises
``ModuleNotFoundError`` — the selector in
:mod:`llm_tts_api.dependencies` translates that into a startup-time
``provider_error.missing_extra`` per NFR-ST-02.

Layout: each blob is stored as ``<voice_id>.wav`` directly under the
bucket root (optionally prefixed via ``TTS_VOICE_BLOB_S3_PREFIX`` later
sprints). Voice ids are validated against :data:`VOICE_ID_PATTERN`
before any network call so a malformed id cannot smuggle into the S3
key.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from aiobotocore.session import AioSession, get_session  # type: ignore[import-not-found]
from botocore.exceptions import ClientError  # type: ignore[import-not-found]

from llm_tts_api.services.voice_store.errors import VoiceNotFoundError, VoiceStoreError
from llm_tts_api.services.voice_store.records import validate_voice_id

_BLOB_SUFFIX = ".wav"
# Error codes aiobotocore/botocore returns when a key or bucket is absent.
# S3 itself answers ``NoSuchKey`` but the ``head_object`` API uses HTTP 404
# without a typed code, hence the dual check.
_MISSING_KEY_CODES = frozenset({"NoSuchKey", "NoSuchBucket", "404"})


class S3BlobRepository:
    """Blob bytes stored as ``s3://<bucket>/<voice_id>.wav``.

    The bucket existence check runs lazily on first access and is
    memoised via ``_bucket_verified`` — a single startup-time HEAD
    against a misconfigured bucket would otherwise block lifespan from
    an asyncio-aware path. Subsequent operations short-circuit the
    check.
    """

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None = None,
        region_name: str | None = None,
        session: AioSession | None = None,
    ) -> None:
        if not bucket:
            raise ValueError("S3BlobRepository requires a non-empty bucket name")
        self._bucket = bucket
        self._endpoint_url = endpoint_url or None
        self._region_name = region_name or None
        self._session = session or get_session()
        self._bucket_verified = False

    @property
    def bucket(self) -> str:
        """Return the S3 bucket name (for diagnostics / tests)."""
        return self._bucket

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[Any]:
        async with self._session.create_client(
            "s3",
            endpoint_url=self._endpoint_url,
            region_name=self._region_name,
        ) as client:
            yield client

    def _key_for(self, voice_id: str) -> str:
        validate_voice_id(voice_id)
        return f"{voice_id}{_BLOB_SUFFIX}"

    async def _ensure_bucket(self, client: Any) -> None:
        if self._bucket_verified:
            return
        try:
            await client.head_bucket(Bucket=self._bucket)
        except ClientError as exc:
            raise VoiceStoreError(f"S3 bucket {self._bucket!r} not reachable: {exc}") from exc
        self._bucket_verified = True

    async def put(self, voice_id: str, data: bytes) -> None:
        key = self._key_for(voice_id)
        async with self._client() as client:
            await self._ensure_bucket(client)
            await client.put_object(Bucket=self._bucket, Key=key, Body=data)

    async def get(self, voice_id: str) -> bytes:
        key = self._key_for(voice_id)
        async with self._client() as client:
            await self._ensure_bucket(client)
            try:
                response = await client.get_object(Bucket=self._bucket, Key=key)
            except ClientError as exc:
                if _is_missing(exc):
                    raise VoiceNotFoundError(voice_id) from exc
                raise
            async with response["Body"] as stream:
                payload = await stream.read()
                # Boto returns bytes for non-streaming reads; tighten the
                # type for callers.
                return bytes(payload)

    async def exists(self, voice_id: str) -> bool:
        key = self._key_for(voice_id)
        async with self._client() as client:
            await self._ensure_bucket(client)
            try:
                await client.head_object(Bucket=self._bucket, Key=key)
            except ClientError as exc:
                if _is_missing(exc):
                    return False
                raise
            return True

    async def delete(self, voice_id: str) -> None:
        key = self._key_for(voice_id)
        async with self._client() as client:
            await self._ensure_bucket(client)
            try:
                await client.head_object(Bucket=self._bucket, Key=key)
            except ClientError as exc:
                if _is_missing(exc):
                    raise VoiceNotFoundError(voice_id) from exc
                raise
            await client.delete_object(Bucket=self._bucket, Key=key)


def _is_missing(exc: ClientError) -> bool:
    """Return ``True`` when ``exc`` represents a missing key/bucket."""
    err = exc.response.get("Error", {}) if hasattr(exc, "response") else {}
    code = str(err.get("Code", ""))
    if code in _MISSING_KEY_CODES:
        return True
    # head_object on S3 returns HTTP 404 with empty Code on missing keys.
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return bool(status == 404)
