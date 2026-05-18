# TTS — Voice CRUD (`/v1/tts/voices/*`)

## Purpose
Captures the POST / GET-list / GET-one / GET-audio / PUT / DELETE flows that back the voice store from the HTTP edge. Multipart uploads use `metadata` (JSON-encoded `VoiceCreate` / `VoiceUpdate`) + `audio` (`UploadFile`) parts; metadata + blob writes go through the two repositories on `app.state`.

## Participants
- `list_voices`, `create_voice`, `get_voice`, `get_voice_audio`, `update_voice`, `delete_voice` — `routers/voices.py`
- `_parse_metadata_json`, `_read_audio_validated`, `_check_magic_bytes` — `routers/voices.py`
- `validate_voice_id` (path-traversal guard) — `services/voice_store/records.py`
- `VoiceMetadataRepository`, `VoiceBlobRepository` — see [../class/voice-store.md](../class/voice-store.md)

## Narrative
Every write checks consent (`consent_acknowledged=true` → otherwise `validation_error.consent_required`, NFR-CP-01) and validates the audio part against size (`TTS_REFAUDIO_MAX_BYTES`, NFR-SE-01), content-type allow-list, and magic bytes (NFR-SE-02). Path-traversal voice ids are rejected at two seams: the Pydantic `id: str = Field(..., pattern=...)` on POST, and `validate_voice_id` on every URL `{voice_id}` before any I/O.

**POST.** Metadata is written first, then the blob. On a blob-write failure the router runs a best-effort metadata rollback (`repo.delete(id)`) to keep the two stores consistent.

**PUT.** Metadata is read for existence; if an `audio` part is provided, the blob is written FIRST so a failure leaves the old metadata + old blob intact (atomic blob replace per FR-VS-08); metadata is then updated.

**DELETE.** Metadata is removed first (this is the authoritative existence record); blob delete is best-effort (already-absent is fine).

**GET /audio.** Reads metadata for the side effect of `X-Voice-Source` + `X-Voice-Id` headers, fetches the blob, and computes a SHA-256 in `X-Content-Sha256` so a client can verify the bytes it got.

## Diagram

```mermaid
sequenceDiagram
    autonumber
    participant Client
    participant R as routers/voices
    participant Repo as voice_metadata_repo
    participant Blob as voice_blob_repo

    Note over Client,R: --- POST /v1/tts/voices ---
    Client->>R: multipart {metadata, audio}
    R->>R: _parse_metadata_json(metadata, VoiceCreate)
    R->>R: assert consent_acknowledged
    R->>R: _read_audio_validated(audio, max_bytes)
    R->>Repo: create(VoiceRecord(source=crud))
    Repo-->>R: persisted record
    R->>Blob: put(id, audio_bytes)
    alt blob put OK
        R-->>Client: 201 VoiceResponse
    else blob put fails
        R->>Repo: delete(id)  (rollback)
        R-->>Client: 5xx
    end

    Note over Client,R: --- GET /v1/tts/voices ---
    Client->>R: GET /v1/tts/voices
    R->>Repo: list()
    Repo-->>R: list[VoiceRecord]
    R-->>Client: 200 VoiceListResponse (summaries)

    Note over Client,R: --- GET /v1/tts/voices/{voice_id} ---
    Client->>R: GET /v1/tts/voices/{id}
    R->>R: validate_voice_id(id)
    R->>Repo: get(id)
    alt found
        Repo-->>R: VoiceRecord
        R-->>Client: 200 VoiceResponse
    else missing
        Repo-->>R: VoiceNotFoundError
        R-->>Client: 404 voice_error.voice_not_found
    end

    Note over Client,R: --- GET /v1/tts/voices/{voice_id}/audio ---
    Client->>R: GET /v1/tts/voices/{id}/audio
    R->>Repo: get(id)
    R->>Blob: get(id)
    alt both present
        Blob-->>R: bytes
        R->>R: sha256(data) → X-Content-Sha256
        R-->>Client: 200 audio/wav + X-Voice-Id + X-Voice-Source + X-Content-Sha256
    else blob missing
        Blob-->>R: VoiceNotFoundError
        R-->>Client: 404 voice_error.voice_blob_missing
    end

    Note over Client,R: --- PUT /v1/tts/voices/{voice_id} ---
    Client->>R: multipart {metadata, [audio]}
    R->>R: validate_voice_id(id); _parse_metadata_json(...)
    R->>R: assert consent_acknowledged
    R->>Repo: get(id) (existence)
    opt audio present
        R->>R: _read_audio_validated(audio, max_bytes)
        R->>Blob: put(id, new_bytes) (replace BEFORE metadata update)
    end
    R->>Repo: update(VoiceRecord(updated_at=now))
    Repo-->>R: persisted record
    R-->>Client: 200 VoiceResponse

    Note over Client,R: --- DELETE /v1/tts/voices/{voice_id} ---
    Client->>R: DELETE /v1/tts/voices/{id}
    R->>R: validate_voice_id(id)
    R->>Repo: delete(id)
    opt blob exists
        R->>Blob: delete(id) (best-effort)
    end
    R-->>Client: 204
```

## Notes
- Multipart `audio` content-types allowed: `audio/wav`, `audio/x-wav`, `audio/flac`, `audio/mpeg`. Magic-byte mismatch → `validation_error.ref_audio_invalid`.
- POST rollback (delete metadata on blob failure) is best-effort: a second exception during the rollback path is logged but not re-raised — the caller still sees the original blob failure.
- PUT writes the blob BEFORE updating metadata so a blob-write failure leaves the old metadata + old blob both intact (atomic replace per FR-VS-08).
- See [../class/voice-store.md](../class/voice-store.md) for the repository Protocols and backend matrix.
