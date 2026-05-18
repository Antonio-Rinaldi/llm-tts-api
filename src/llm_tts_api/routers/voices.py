"""Voice CRUD endpoints under ``/v1/tts/voices`` (S-025).

Multipart upload contract (POST / PUT):
    - ``metadata``: JSON-encoded form field validated by :class:`VoiceCreate` /
      :class:`VoiceUpdate`.
    - ``audio``: ``UploadFile`` validated against ``TTS_REFAUDIO_MAX_BYTES``
      (NFR-SE-01), the content-type allow-list, and magic-bytes inspection
      (NFR-SE-02). The audio part is required on POST and optional on PUT.

All blob writes go through :class:`VoiceBlobRepository` and metadata writes
through :class:`VoiceMetadataRepository`; both repos are pulled from
``app.state`` via the Depends getters in :mod:`llm_tts_api.dependencies`.

Path-traversal voice ids are rejected at two seams: the Pydantic ``id``
field (``pattern=...``) on POST, and ``validate_voice_id`` on every
``{voice_id}`` URL path before any I/O.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Path, Request, Response, UploadFile
from pydantic import ValidationError

from llm_tts_api.dependencies import (
    get_settings,
    get_voice_blob_repo,
    get_voice_metadata_repo,
)
from llm_tts_api.errors import OpenAIError, OpenAIHTTPException, invalid_request, voice_error
from llm_tts_api.schemas.voices import (
    VoiceCreate,
    VoiceListResponse,
    VoiceResponse,
    VoiceSummary,
    VoiceUpdate,
)
from llm_tts_api.services.voice_store import (
    VoiceAlreadyExistsError,
    VoiceBlobRepository,
    VoiceIdInvalidError,
    VoiceMetadataRepository,
    VoiceNotFoundError,
    VoiceRecord,
    validate_voice_id,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/tts/voices", tags=["voices"])

_ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset(
    {"audio/wav", "audio/x-wav", "audio/flac", "audio/mpeg"}
)


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _check_magic_bytes(content_type: str, data: bytes) -> bool:
    """Verify the leading bytes match the declared content-type (NFR-SE-02)."""
    if content_type in {"audio/wav", "audio/x-wav"}:
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    if content_type == "audio/flac":
        return data[:4] == b"fLaC"
    if content_type == "audio/mpeg":
        if data[:3] == b"ID3":
            return True
        return len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0
    return False


def _read_audio_validated(
    audio: UploadFile,
    max_bytes: int,
) -> bytes:
    """Read upload body, enforcing size + content-type + magic-bytes checks."""
    content_type = (audio.content_type or "").lower()
    if content_type not in _ALLOWED_CONTENT_TYPES:
        raise invalid_request(
            "audio content-type is not allowed",
            param="audio",
            code="ref_audio_invalid",
        )
    data = audio.file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise invalid_request(
            f"audio exceeds maximum size of {max_bytes} bytes",
            param="audio",
            code="ref_audio_invalid",
        )
    if not data:
        raise invalid_request(
            "audio body is empty",
            param="audio",
            code="ref_audio_invalid",
        )
    if not _check_magic_bytes(content_type, data):
        raise invalid_request(
            "audio magic bytes do not match declared content-type",
            param="audio",
            code="ref_audio_invalid",
        )
    return data


def _parse_metadata_json(raw: str, model_cls: type[VoiceCreate] | type[VoiceUpdate]) -> object:
    """Decode the multipart ``metadata`` form field as JSON + validate."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise invalid_request(
            f"metadata is not valid JSON: {exc.msg}",
            param="metadata",
            code="invalid_parameter",
        ) from exc
    if not isinstance(payload, dict):
        raise invalid_request(
            "metadata must be a JSON object",
            param="metadata",
            code="invalid_parameter",
        )
    try:
        return model_cls(**payload)
    except ValidationError as exc:
        errors = exc.errors()
        loc: tuple[object, ...] = errors[0].get("loc", ()) if errors else ()
        message: str = (
            errors[0].get("msg", "metadata validation failed")
            if errors
            else ("metadata validation failed")
        )
        param = ".".join(str(p) for p in loc) if loc else "metadata"
        if param == "consent_acknowledged":
            raise invalid_request(
                "consent_acknowledged must be true",
                param=param,
                code="consent_required",
            ) from exc
        raise invalid_request(message, param=param, code="invalid_parameter") from exc


def _record_to_response(record: VoiceRecord) -> VoiceResponse:
    return VoiceResponse(
        id=record.id,
        transcript=record.transcript,
        language=record.language,
        consent_acknowledged=record.consent_acknowledged,
        number_lang=record.number_lang,
        target_db=record.target_db,
        temperature=record.temperature,
        top_p=record.top_p,
        max_sentences_per_chunk=record.max_sentences_per_chunk,
        source=record.source,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _record_to_summary(record: VoiceRecord) -> VoiceSummary:
    return VoiceSummary(
        id=record.id,
        language=record.language,
        source=record.source,
        created_at=record.created_at,
    )


MetadataRepoDep = Annotated[VoiceMetadataRepository, Depends(get_voice_metadata_repo)]
BlobRepoDep = Annotated[VoiceBlobRepository, Depends(get_voice_blob_repo)]


@router.get("", response_model=VoiceListResponse)
async def list_voices(repo: MetadataRepoDep) -> VoiceListResponse:
    """List voice summaries (FR-VS-06): id, language, source, created_at."""
    records = await repo.list()
    return VoiceListResponse(data=[_record_to_summary(r) for r in records])


@router.post("", status_code=201, response_model=VoiceResponse)
async def create_voice(
    request: Request,
    repo: MetadataRepoDep,
    blob_repo: BlobRepoDep,
    metadata: Annotated[str, Form()],
    audio: Annotated[UploadFile, File()],
) -> VoiceResponse:
    """Create a voice record + blob via multipart upload (FR-VS-05)."""
    settings = get_settings(request)
    payload = _parse_metadata_json(metadata, VoiceCreate)
    assert isinstance(payload, VoiceCreate)  # noqa: S101 — _parse_metadata_json contract
    if not payload.consent_acknowledged:
        raise invalid_request(
            "consent_acknowledged must be true",
            param="consent_acknowledged",
            code="consent_required",
        )
    audio_bytes = _read_audio_validated(audio, settings.tts_refaudio_max_bytes)
    now = _utcnow()
    record = VoiceRecord(
        id=payload.id,
        transcript=payload.transcript,
        language=payload.language,
        consent_acknowledged=True,
        number_lang=payload.number_lang,
        target_db=payload.target_db,
        temperature=payload.temperature,
        top_p=payload.top_p,
        max_sentences_per_chunk=payload.max_sentences_per_chunk,
        source="crud",
        created_at=now,
        updated_at=now,
    )
    try:
        created = await repo.create(record)
    except VoiceAlreadyExistsError as exc:
        raise OpenAIHTTPException(
            status_code=409,
            error=OpenAIError(
                message=f"voice id {payload.id!r} already exists",
                type="validation_error",
                code="voice_id_exists",
                param="id",
            ),
        ) from exc
    except VoiceIdInvalidError as exc:
        raise invalid_request(str(exc), param="id", code="invalid_parameter") from exc
    try:
        await blob_repo.put(payload.id, audio_bytes)
    except Exception:
        # Best-effort rollback: metadata commit went first, blob write failed.
        # Remove metadata to keep the two stores consistent.
        try:
            await repo.delete(payload.id)
        except Exception:  # noqa: BLE001 — rollback is best-effort
            logger.exception(
                "voice_store_rollback_failed voice_id=%s",
                payload.id,
            )
        raise
    return _record_to_response(created)


@router.get("/{voice_id}", response_model=VoiceResponse)
async def get_voice(
    voice_id: Annotated[str, Path()],
    repo: MetadataRepoDep,
) -> VoiceResponse:
    """Return the full metadata record for one voice (FR-VS-07)."""
    try:
        validate_voice_id(voice_id)
    except VoiceIdInvalidError as exc:
        raise invalid_request(str(exc), param="voice_id") from exc
    try:
        record = await repo.get(voice_id)
    except VoiceNotFoundError as exc:
        raise voice_error("voice_not_found", f"voice {voice_id!r} not found") from exc
    return _record_to_response(record)


@router.get("/{voice_id}/audio")
async def get_voice_audio(
    voice_id: Annotated[str, Path()],
    repo: MetadataRepoDep,
    blob_repo: BlobRepoDep,
) -> Response:
    """Return the audio blob with metadata-bearing ``X-*`` headers (FR-VS-07b)."""
    try:
        validate_voice_id(voice_id)
    except VoiceIdInvalidError as exc:
        raise invalid_request(str(exc), param="voice_id") from exc
    try:
        record = await repo.get(voice_id)
    except VoiceNotFoundError as exc:
        raise voice_error("voice_not_found", f"voice {voice_id!r} not found") from exc
    try:
        data = await blob_repo.get(voice_id)
    except VoiceNotFoundError as exc:
        raise voice_error(
            "voice_blob_missing",
            f"audio for voice {voice_id!r} not found",
            status_code=404,
        ) from exc
    return Response(
        content=data,
        media_type="audio/wav",
        headers={
            "X-Voice-Id": record.id,
            "X-Voice-Source": record.source,
            "X-Content-Sha256": hashlib.sha256(data).hexdigest(),
        },
    )


@router.put("/{voice_id}", response_model=VoiceResponse)
async def update_voice(
    request: Request,
    voice_id: Annotated[str, Path()],
    repo: MetadataRepoDep,
    blob_repo: BlobRepoDep,
    metadata: Annotated[str, Form()],
    audio: Annotated[UploadFile | None, File()] = None,
) -> VoiceResponse:
    """Update a voice record + (optionally) replace its audio blob (FR-VS-08)."""
    settings = get_settings(request)
    try:
        validate_voice_id(voice_id)
    except VoiceIdInvalidError as exc:
        raise invalid_request(str(exc), param="voice_id") from exc
    payload = _parse_metadata_json(metadata, VoiceUpdate)
    assert isinstance(payload, VoiceUpdate)  # noqa: S101
    if not payload.consent_acknowledged:
        raise invalid_request(
            "consent_acknowledged must be true",
            param="consent_acknowledged",
            code="consent_required",
        )
    try:
        existing = await repo.get(voice_id)
    except VoiceNotFoundError as exc:
        raise voice_error("voice_not_found", f"voice {voice_id!r} not found") from exc

    new_blob: bytes | None = None
    if audio is not None and audio.filename:
        new_blob = _read_audio_validated(audio, settings.tts_refaudio_max_bytes)

    if new_blob is not None:
        # Write blob FIRST so a failed put leaves the old metadata + old blob
        # intact (atomic blob replace per FR-VS-08).
        await blob_repo.put(voice_id, new_blob)

    updated = VoiceRecord(
        id=existing.id,
        transcript=payload.transcript,
        language=payload.language,
        consent_acknowledged=True,
        number_lang=payload.number_lang,
        target_db=payload.target_db,
        temperature=payload.temperature,
        top_p=payload.top_p,
        max_sentences_per_chunk=payload.max_sentences_per_chunk,
        source=existing.source,
        created_at=existing.created_at,
        updated_at=_utcnow(),
    )
    saved = await repo.update(updated)
    return _record_to_response(saved)


@router.delete("/{voice_id}", status_code=204)
async def delete_voice(
    voice_id: Annotated[str, Path()],
    repo: MetadataRepoDep,
    blob_repo: BlobRepoDep,
) -> Response:
    """Remove both metadata + blob for a voice (FR-VS-09)."""
    try:
        validate_voice_id(voice_id)
    except VoiceIdInvalidError as exc:
        raise invalid_request(str(exc), param="voice_id") from exc
    try:
        await repo.delete(voice_id)
    except VoiceNotFoundError as exc:
        raise voice_error("voice_not_found", f"voice {voice_id!r} not found") from exc
    # Blob delete is best-effort: it may already be absent (FR-VS-09 retries
    # are out of scope for the synchronous path).
    try:
        if await blob_repo.exists(voice_id):
            await blob_repo.delete(voice_id)
    except VoiceNotFoundError:
        pass
    return Response(status_code=204)


__all__ = ["router"]
