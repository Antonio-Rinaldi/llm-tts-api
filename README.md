# llm-tts-api

A local-first, OpenAI-compatible TTS API (FastAPI) with pluggable providers,
hardware auto-detection, a voice store with CRUD + seed ingestion, and a
rich synthesis endpoint that supports buffered and streamed responses. The
OpenAI-compatible adapter (`POST /v1/audio/speech`) and the rich endpoint
(`POST /v1/tts/synthesize`) share **one** synthesis pipeline (`synthesize_core`)
— there is no dual code path (BR-9, NFR-PT-03b).

## Quick Start

```bash
# 1. Install (uv handles the venv).
uv sync

# 2. Configure (env-var inventory is exhaustive — see "Configuration").
cp .env.example .env.local    # if a template exists, otherwise create one

# 3. Run.
uv run llm-tts-api
# or:
uv run uvicorn llm_tts_api.main:app --host 0.0.0.0 --port 8000
```

The server logs flip `ready=True` once warmup + voice-seed ingestion finish;
until then `/ready` returns 503 with `{"ready": false, "reason": "warming_up"}`
(during shutdown the same probe reports `"draining"`).

## What This Service Does

- Exposes `POST /v1/audio/speech` (OpenAI shape) and `POST /v1/tts/synthesize`
  (rich shape: streaming, trailers, per-request overrides) — both backed by
  the same `synthesize_core` pipeline.
- Manages a **voice store** under `/v1/tts/voices` (POST / GET list / GET one /
  GET audio / PUT / DELETE) backed by metadata + blob repositories with
  pluggable filesystem, Postgres (optional `[postgres]` extra), and S3
  (optional `[s3]` extra) backends.
- Auto-detects hardware and picks a compatible provider (S-006). MPS →
  `mlx_audio` / `voxtral`; CUDA → `vllm-omni`. `TTS_PROVIDER` overrides.
- Ingests `voice_map.json` seed entries at startup and hot-reloads (≤ 2 s)
  on file changes via `watchfiles` (S-011, NFR-OP-05).
- Emits OpenAI-shaped error envelopes with a typed taxonomy
  (`validation_error`, `voice_error`, `provider_error`, `capacity_error`,
  `internal_error`) plus an `X-Error-Code` header on every error response.

## Hardware Auto-Detection (S-006)

The dependency wiring resolves a `DeviceProfile` from `TTS_DEVICE` (default
`auto`), then selects a provider from the registered providers whose
`supports_devices` set contains the detected device. The first matching
provider wins (registration order is the priority). When `TTS_PROVIDER` is
set, it overrides auto-selection — but the override is still validated
against `supports_devices` and fails fast at startup if incompatible.

Provider × device capability matrix:

| Provider    | `supports_devices` | Auto-picked on            | Notes                                                                  |
|-------------|--------------------|---------------------------|------------------------------------------------------------------------|
| `mlx_audio` | `{mps}`            | Apple Silicon (MPS)       | Reference cloning + named voices. Clone fallback on `AssertionError`. |
| `voxtral`   | `{mps}`            | Apple Silicon (MPS)       | Cloning-only — `invalid_request` if `ref_audio_path` is missing.       |
| `vllm-omni` | `{cuda}`           | NVIDIA CUDA hosts         | Voice optional. Multiple loader import paths, flexible payload shapes. |

If no registered provider declares support for the detected device, startup
raises `provider_error.no_viable_provider`. The CUDA image (Dockerfile.cuda)
should be paired with `TTS_PROVIDER=vllm-omni` or `TTS_DEVICE=cuda` so the
override path validates the choice deterministically.

## Provider vs model

The two concepts are orthogonal and frequently confused:

- **Provider** — the *engine* (a `TTSProviderStrategy` implementation
  registered with `TTSProviderRegistry`). Determines which Python
  package + device the synthesis runs on.
- **Model** — the *checkpoint* loaded into that engine. Selected
  per-provider from `TTS_<PROVIDER>_MODEL_ALLOWED`. Same model name
  can be valid for multiple providers (or not — the allow-list is
  the source of truth).

| Provider name | Typical model id                       | `supports_devices` | Notes                                                                 |
|---------------|----------------------------------------|--------------------|-----------------------------------------------------------------------|
| `mlx_audio`   | `Qwen/Qwen3-TTS-12Hz-0.6B-Base`        | `{mps}`            | Default on Apple Silicon. Reference cloning + named voices.           |
| `voxtral`     | `voxtral/mini-tts`                     | `{mps}`            | Cloning-only — `invalid_request` if `ref_audio_path` is missing.       |
| `vllm-omni`   | (operator-configured)                  | `{cuda}`           | CUDA hosts. Voice optional; multiple loader import paths supported.   |

If a request sets `provider="qwen"` or `provider="Qwen/Qwen3-TTS-12Hz-0.6B-Base"`
the HF-3 error message guides the caller:

```json
{
  "error": {
    "type": "validation_error",
    "code": "invalid_parameter",
    "param": "provider",
    "message": "provider 'qwen' is not supported. Valid providers: mlx_audio, voxtral, vllm-omni. Note: 'qwen' refers to a model family, not a provider — use provider='mlx_audio' with the desired model (e.g. model='Qwen/Qwen3-TTS-12Hz-0.6B-Base')."
  }
}
```

## Endpoints

| Method | Path                                  | Purpose                                                          |
|--------|---------------------------------------|------------------------------------------------------------------|
| `GET`  | `/health`                             | Lock-free liveness (FR-HL-01); body includes queue + cache info. |
| `GET`  | `/ready`                              | Readiness gate (FR-HL-02); 503 during warmup / drain.            |
| `GET`  | `/v1/models`                          | OpenAI-compatible model catalog.                                 |
| `POST` | `/v1/audio/speech`                    | OpenAI-compatible speech (thin translator over `synthesize_core`). |
| `POST` | `/v1/tts/synthesize`                  | Rich endpoint — buffered or streamed (S-013, S-015).             |
| `POST` | `/v1/tts/voices`                      | Create a voice (multipart upload, FR-VS-05).                     |
| `GET`  | `/v1/tts/voices`                      | List voice summaries (FR-VS-06).                                 |
| `GET`  | `/v1/tts/voices/{voice_id}`           | Fetch one voice metadata record.                                 |
| `GET`  | `/v1/tts/voices/{voice_id}/audio`     | Download the voice's reference-audio blob.                       |
| `PUT`  | `/v1/tts/voices/{voice_id}`           | Update metadata; replace blob when an `audio` part is included.  |
| `DELETE` | `/v1/tts/voices/{voice_id}`         | Remove both metadata and blob.                                   |

Placeholder OpenAI surface (`/v1/audio/transcriptions`, `/v1/audio/translations`,
`/v1/audio/voice_consents/*`, `/v1/chat/...`, `/v1/realtime/...`) is URL-stable
and returns `501` (`validation_error.not_implemented`) so OpenAI SDK clients
can probe shapes without surprise.

### `POST /v1/tts/synthesize` — rich endpoint

JSON request body (`SynthesizeRequest`, `extra="forbid"`):

| Field                     | Type             | Notes                                                                 |
|---------------------------|------------------|-----------------------------------------------------------------------|
| `input`                   | string           | Required. Length capped at `TTS_MAX_INPUT_CHARS`.                     |
| `voice`                   | string \| null   | Voice id from the voice store. Required at the handler (envelope `voice_required`) — may instead be supplied by the resolved preset (FR-PR-03). |
| `preset`                  | string \| null   | Named preset (open string; built-ins `fast` / `balanced` / `quality`). Unset → `TTS_DEFAULT_PRESET`. Unknown name → `validation_error.preset_unknown` (FR-PR-07). |
| `provider`                | string \| null   | Optional override (`mlx_audio`, `voxtral`, `vllm-omni`).              |
| `model`                   | string \| null   | Optional model override; must be in the active provider's allow-list. |
| `response_format`         | `"wav"`          | Currently only WAV is supported end-to-end.                           |
| `stream`                  | bool             | `false` (buffered, `Content-Length` set) / `true` (chunked stream).   |
| `normalize_db`            | number \| null   | Per-request RMS target dBFS override.                                 |
| `max_sentences_per_chunk` | int \| null      | Override the voice's semantic-chunk cap (≥ 1).                        |
| `language`                | string \| null   | Override the voice's language label.                                  |
| `number_lang`             | string \| null   | Override the language used for number/date expansion.                 |
| `temperature`             | number \| null   | Override sampling temperature.                                        |
| `top_p`                   | number \| null   | Override nucleus sampling.                                            |

Streaming negotiates HTTP trailing headers — `X-Chunks` and
`X-Total-Duration-Ms` are emitted as **trailers** when the client advertises
`TE: trailers` and uvicorn can support them; otherwise the service simply
omits those two values (G-3). Clients consuming streams MUST NOT depend on
them being present.

### Response header inventory (SRS §5 C-2)

| Header                  | Emitted on                                              | Meaning                                              |
|-------------------------|---------------------------------------------------------|------------------------------------------------------|
| `X-Request-ID`          | always (success and error)                              | Request correlation id (S-004 contextvar).           |
| `X-Provider`            | success (rich endpoint only)                            | Provider used for synthesis.                         |
| `X-Model`               | success (rich endpoint only)                            | Model id used.                                       |
| `X-Device`              | success (rich endpoint only)                            | Inference device (`mps`/`cuda`/`cpu`).               |
| `X-Dtype`               | success (rich endpoint only)                            | Inference dtype.                                     |
| `X-Voice-Source`        | success (rich endpoint and voice-audio GET)             | `seed` or `crud`.                                    |
| `X-Voice-Id`            | success (rich endpoint and voice-audio GET)             | Voice id resolved against the store.                 |
| `X-Chunks`              | non-streamed success, or streamed trailer when feasible | Number of chunks synthesized.                        |
| `X-Total-Duration-Ms`   | non-streamed success, or streamed trailer when feasible | Total audio duration in milliseconds.                |
| `X-Preset-Effective`    | success (rich endpoint only)                            | Resolved preset + effective fields (FR-PR-08).       |
| `X-Preset-Ignored-Knobs`| success (rich endpoint only, when non-empty)            | Preset fields the active pipeline cannot honor (BR-17 / FR-PR-09). |
| `X-Content-Sha256`      | `GET /v1/tts/voices/{id}/audio`                         | SHA-256 of the returned blob.                        |
| `X-Error-Code`          | any error response                                      | Matches the envelope `error.code` (FR-ER-03).        |

The OpenAI-adapter path (`POST /v1/audio/speech`) strips the rich-only
headers (`X-Provider`, `X-Model`, `X-Device`, `X-Dtype`, `X-Voice-Source`,
`X-Voice-Id`, `X-Chunks`, `X-Total-Duration-Ms`, `X-Preset-Effective`,
`X-Preset-Ignored-Knobs`) so the response shape stays byte-identical to
OpenAI (FR-OA-01..03 + NFR-PT-03b).

`X-Preset-Effective` has the shape `<preset_name>(field=value,...)` where
the field list is the post-merge resolved configuration (preset defaults
overlaid by explicit request fields), key-sorted for stability. Example:

```
X-Preset-Effective: quality(language=it,max_sentences_per_chunk=3,model=Qwen/Qwen3-TTS-12Hz-0.6B-Base,normalize_db=-20.0,provider=mlx_audio,response_format=flac,temperature=0.8,top_p=0.95)
X-Preset-Ignored-Knobs: response_format
```

(`response_format=flac` is recorded as effective for transparency, then
the rich pipeline soft-ignores it because only WAV is end-to-end yet —
that fact is surfaced via `X-Preset-Ignored-Knobs` per FR-PR-09.)

## Audio presets (S-027 / S-028 / S-029)

A **preset** is a named bundle of synthesis defaults loaded from
`config/presets.json` (overridable via `TTS_PRESETS_FILE`). The registry
is parsed + schema-validated + permission-checked at startup; any
failure raises `config_error.presets_invalid` /
`config_error.preset_provider_invalid` / `config_error.presets_unsafe_permissions`
and the service refuses to come up (NFR-SE-09 / FR-PR-02 / FR-PR-13).

Three presets ship out of the box:

| Name       | Description                                                          | Pinned `(provider, model)`                          | Notable defaults                                                  |
|------------|----------------------------------------------------------------------|-----------------------------------------------------|-------------------------------------------------------------------|
| `fast`     | Low-TTFB interactive. Conservative sampling, single-sentence chunks. | — (uses the auto-selected provider + its default)   | `temperature=0.7`, `top_p=0.9`, `max_sentences_per_chunk=1`.      |
| `balanced` | Cycle-1 default behaviour. The server default unless overridden.     | — (uses the auto-selected provider + its default)   | `temperature=0.8`, `top_p=0.95`, `max_sentences_per_chunk=2`.     |
| `quality`  | Buffered audiobook chapter. Full post-processing.                    | `mlx_audio` + `Qwen/Qwen3-TTS-12Hz-0.6B-Base`       | `language=en`, `response_format=flac` (soft-ignored — see note), `postprocess.rms_normalize=true`, `postprocess.silence_trim=true`. |

`quality` exercises the FR-PR-13 cross-check at startup: the
`(provider, model)` pin is validated against the active provider's
allow-list, so a misconfigured pin fails startup with
`config_error.preset_provider_invalid` rather than at the first request.

### Precedence (BR-10)

> **Explicit request field > preset defaults > Settings / VoiceRecord defaults.**

The resolver (`resolve_preset` in `services/synthesize_service.py`) is
pure — it reads only the request, the request-scoped registry snapshot,
and `Settings`. The snapshot is captured per-request via
`Depends(get_preset_registry_snapshot)`, so a mid-flight hot-reload
swap cannot tear a resolution (NFR-PR-04 in-flight invariant).

Conflicts between explicit fields and preset pins are recorded in
`X-Preset-Effective` and logged at WARN. Knobs the active pipeline
cannot honor (e.g. `response_format=flac` before S-033 lands the
format-extension pipeline) are soft-ignored and listed in
`X-Preset-Ignored-Knobs` (BR-17 / FR-PR-09).

### Authoring a custom preset

The presets file is operator-owned. Custom presets are accepted as
long as they pass schema validation and (where applicable) the
provider/model cross-check. Per FR-PR-12 the `/v1/models` catalog
**does not** enumerate preset names; presets are an operator-facing
config surface, not a discovery API.

```json
{
  "audiobook_it": {
    "label": "Italian audiobook",
    "description": "MLX-Audio + Qwen3, Italian, buffered.",
    "defaults": {
      "provider": "mlx_audio",
      "model": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
      "language": "Italian",
      "number_lang": "it",
      "voice": "gold",
      "temperature": 0.8,
      "top_p": 0.95,
      "max_sentences_per_chunk": 3,
      "normalize_db": -20.0,
      "postprocess": {
        "rms_normalize": true,
        "silence_trim": true,
        "denoise": false
      }
    }
  }
}
```

`defaults.provider`, `defaults.model`, and `defaults.voice` are
optional but powerful: a preset that supplies `voice` lets clients
omit it on the request (FR-PR-03 — `voice_required` falls through to
the preset). This is exactly the workflow targeted by the
`audiobook_it` example above: clients hit `POST /v1/tts/synthesize`
with `{"input": "...", "preset": "audiobook_it"}` and nothing else.

### Hot reload (S-029)

The reloader watches `TTS_PRESETS_FILE` via the same `watchfiles`
primitive as the seed map (`ConfigWatcher` in
`services/config_watcher.py`). On every change it re-runs the
startup validation chain MINUS the file-permission check (the
permission posture is startup-only per RISK-PR-3 — the
`mv` + `chmod` race window is the documented limitation). Valid
edits are swapped atomically into `app.state.preset_registry`;
invalid edits log a single WARN line with the would-be
`config_error.*` code and keep the prior registry live
(NFR-SE-10 attack tolerance).

### Voice CRUD (S-022 .. S-025)

`POST /v1/tts/voices` and `PUT /v1/tts/voices/{id}` are **multipart** uploads
with exactly two form parts:

- `metadata` — JSON-encoded `VoiceCreate` / `VoiceUpdate` (see schema below).
  `consent_acknowledged=true` is mandatory on POST (NFR-CP-01); rejection on
  the missing/false case is `validation_error.consent_required`.
- `audio` — `UploadFile`. Size capped by `TTS_REFAUDIO_MAX_BYTES` (NFR-SE-01,
  default 10 MiB). Content-type must be one of `audio/wav`, `audio/x-wav`,
  `audio/flac`, `audio/mpeg`; magic bytes are inspected to confirm the
  declared type (NFR-SE-02). On POST the audio part is required; on PUT it is
  optional (metadata-only update is allowed).

Voice id rules: `^[a-z0-9_-]{1,64}$`. Path traversal is rejected at two
seams: the Pydantic field on POST and `validate_voice_id` on every
`{voice_id}` URL before any I/O.

`VoiceRecord` (the persisted shape):

| Field                     | Type                  | Notes                              |
|---------------------------|-----------------------|------------------------------------|
| `id`                      | string                | matches `[a-z0-9_-]{1,64}`         |
| `transcript`              | string (non-empty)    | reference text for cloning         |
| `language`                | string                | synthesis language label           |
| `consent_acknowledged`    | bool (`true` only)    | NFR-CP-01 attestation              |
| `number_lang`             | string                | override for number/date expansion |
| `target_db`               | float                 | RMS normalization target dBFS      |
| `temperature`             | float                 | generation temperature             |
| `top_p`                   | float                 | nucleus sampling                   |
| `max_sentences_per_chunk` | int ≥ 1               | chunking cap                       |
| `source`                  | `"seed"` \| `"crud"`  | origin marker                      |
| `created_at`              | datetime              | UTC                                |
| `updated_at`              | datetime              | UTC                                |

### Seed voice ingestion (S-011)

`TTS_VOICE_MAP_FILE` points at a JSON document whose keys are voice ids and
whose values are `VoiceConfig`-shaped entries (see "Voice map" below). On
startup the seed ingestor walks every entry, copies the reference audio
into the voice store, and upserts the metadata record with `source="seed"`.
A `watchfiles` task then re-runs the ingestion within ≤ 2 s of any file
mtime change (NFR-OP-05) so operator edits land without a restart.

Seed-ingested records may be overwritten by subsequent CRUD writes; the
inverse is also true (a seed file edit overwrites a CRUD record carrying
the same id) and is intentional — the seed file is the operator's
configuration source of truth for any id it names.

Set `TTS_VOICE_MAP_WATCH_FORCE_POLLING=1` to fall back to polling when the
filesystem doesn't deliver native change events (e.g. some bind mounts).

## Storage backends (S-022 / S-023 / S-024)

| Backend           | Selector env var                                                 | Required extras       | Notes                                              |
|-------------------|------------------------------------------------------------------|-----------------------|----------------------------------------------------|
| FS metadata       | `TTS_VOICE_METADATA_BACKEND=fs_json` (default)                   | none                  | Single `metadata.json` under `TTS_VOICE_STORE_DIR`. |
| Postgres metadata | `TTS_VOICE_METADATA_BACKEND=postgres` + `TTS_VOICE_METADATA_DSN` | `pip install .[postgres]` | Async pool, schema migrated on startup.        |
| FS blobs          | `TTS_VOICE_BLOB_BACKEND=fs` (default)                            | none                  | `blobs/<id>.wav` under `TTS_VOICE_STORE_DIR`.       |
| S3 blobs          | `TTS_VOICE_BLOB_BACKEND=s3` + `TTS_VOICE_BLOB_S3_BUCKET`         | `pip install .[s3]`   | Endpoint/region optional; AWS creds via env.       |

Selecting an extras-only backend without the extra installed fails fast at
startup with `provider_error.voice_store_unavailable` (or a `missing_extra`
sub-code, where applicable) so misconfiguration cannot defer to the first
request.

## Configuration — full env-var inventory

Every env var below is consumed by `Settings.__post_init__` (see
`src/llm_tts_api/config.py`). The `tests/test_docs_inventory.py` test
asserts this list stays in sync with the code.

### App identity

- `APP_NAME` (default `llm-tts-api`).
- `APP_ENV` (default `development`).
- `APP_LOG_LEVEL` (default `INFO`).
- `APP_LOG_FORMAT` — `text` (default) or `json` (S-004 structured logs).

### Provider routing (S-006)

- `TTS_PROVIDER` — `auto` (default) / `mlx_audio` / `voxtral` / `vllm-omni`.
- `TTS_MLX_AUDIO_MODEL_DEFAULT` — default model for the MLX-audio provider.
- `TTS_MLX_AUDIO_MODEL_ALLOWED` — CSV allow-list for the MLX-audio provider.
- `TTS_VOXTRAL_MODEL_DEFAULT` — default model for the Voxtral provider.
- `TTS_VOXTRAL_MODEL_ALLOWED` — CSV allow-list for the Voxtral provider.
- `TTS_VLLM_OMNI_MODEL_DEFAULT` — default model for the vLLM-Omni provider.
- `TTS_VLLM_OMNI_MODEL_ALLOWED` — CSV allow-list for the vLLM-Omni provider.

### STT (placeholder)

- `STT_MODEL_DEFAULT` — default STT model id (the transcription/translation
  surface is currently 501-stubbed).
- `STT_MODEL_ALLOWED` — CSV allow-list for STT.

### Limits and runtime knobs (S-007 / S-008 / S-010 / S-012)

- `TTS_MAX_INPUT_CHARS` — input length cap (default 4096, minimum 256).
- `TTS_MAX_CONCURRENT_REQUESTS` — concurrent-synthesis cap (default 1; see
  "Sizing recommendations" below).
- `TTS_MAX_QUEUE_DEPTH` — admission-queue depth (default 8). `0` disables
  queueing (synthesis is rejected with `capacity_error.queue_full` once
  the concurrency semaphore is busy).
- `TTS_MODEL_CACHE_SIZE` — resident model LRU cap (default 1, minimum 1).
- `TTS_PRELOAD_MODELS` — CSV of `provider:model` pairs warmed at startup.
- `TTS_INFERENCE_TIMEOUT_SECONDS` — per-request synthesis budget (unset →
  disabled; positive number → `asyncio.wait_for` wraps the call).
- `TTS_SHUTDOWN_DRAIN_SECONDS` — graceful-drain budget on SIGTERM (default
  30 s; FR-HL-04).
- `TTS_MIN_FREE_MEMORY_GB` — soft low-memory warning floor (default 4; `0`
  disables the probe). Logs a single WARNING line at startup if free memory
  is below the floor; never blocks.

### Device / dtype (S-005 / S-006)

- `TTS_DEVICE` — `auto` (default) / `mps` / `cuda` / `cpu`.
- `TTS_DTYPE` — `auto` (default) / `float16` / `bfloat16` / `float32`.

### Voice store (S-022 / S-023 / S-024 / S-025)

- `TTS_VOICE_STORE_DIR` (default `var/voices`) — root for FS-default storage
  (`metadata.json` and `blobs/<id>.wav`).
- `TTS_VOICE_METADATA_BACKEND` — `fs_json` (default) or `postgres`.
- `TTS_VOICE_METADATA_DSN` — required when backend is `postgres`.
- `TTS_VOICE_BLOB_BACKEND` — `fs` (default) or `s3`.
- `TTS_VOICE_BLOB_S3_BUCKET` — required when backend is `s3`.
- `TTS_VOICE_BLOB_S3_ENDPOINT` — optional override for non-AWS S3
  (e.g. `http://localhost:9000` for MinIO).
- `TTS_VOICE_BLOB_S3_REGION` — optional AWS region; empty defers to
  aiobotocore's normal resolution.
- `TTS_REFAUDIO_MAX_BYTES` — per-upload audio cap (NFR-SE-01, default
  `10485760` = 10 MiB).

### Audio-generation presets (S-027 / FR-PR — cycle 2)

- `TTS_PRESETS_FILE` (default `config/presets.json`) — path to the JSON
  registry of named presets loaded + validated at startup
  (FR-PR-01 / FR-PR-02). Hot-reloaded by S-029.
- `TTS_DEFAULT_PRESET` (default `balanced`) — preset applied when a
  request omits `preset`. Must match one of the names defined in the
  presets file or startup fails with `config_error.presets_invalid`
  (FR-PR-05).
- `TTS_SILENCE_TRIM_THRESHOLD_DB` (default `-50.0`) — dBFS floor used by
  the `silence_trim` postprocess step (consumed by S-031). Declared
  here so misconfiguration is rejected at `Settings.__post_init__`.

### Seed voice map (S-011 / FR-VM)

- `TTS_VOICE_MAP_FILE` — path to the seed `voice_map.json`. Unset / missing
  file is a valid empty config (FR-VM-05).

(Additional non-Settings runtime knob: `TTS_VOICE_MAP_WATCH_FORCE_POLLING`
forces the watchfiles loop to poll instead of using native filesystem
events.)

### Test bypass

- `LLM_TTS_API_TEST_NO_LIFESPAN=1` skips the lifespan singleton construction
  in unit-level router tests (the test conftest sets it automatically).

## Sizing recommendations (SRS §5 C-1)

`TTS_MAX_CONCURRENT_REQUESTS` defaults to `1` because the safe-by-default
value works on small hosts without overcommitting the inference device.
On the reference deployment (≥ 32 GB Apple Silicon, A-5) operators are
encouraged to raise it.

| Host class                      | `TTS_MAX_CONCURRENT_REQUESTS` | `TTS_MAX_QUEUE_DEPTH` | Notes                          |
|---------------------------------|-------------------------------|-----------------------|--------------------------------|
| < 16 GB Apple Silicon / laptop  | `1` (default)                 | `4`                   | Conservative, no overcommit.   |
| ≥ 32 GB Apple Silicon (reference) | `2`                         | `8` (default)         | NFR-PF-04 reference profile.   |
| Single CUDA GPU box             | `1`–`2` depending on VRAM     | `8`                   | Provider is `vllm-omni`.       |

`/health` (lock-free, FR-HL-01) reports `queue_depth` and `concurrent_active`
so operators can tune the pair from observed pressure rather than guessing.

## Error taxonomy

Every error response uses the OpenAI envelope shape
`{"error": {"message", "type", "code", "param", "request_id"}}` and carries
the `X-Error-Code` header (matches `error.code`). The taxonomy is closed
for `type` and partially closed for `code` (new sub-codes can be added
without changing the categories):

| Type                | Code                            | When                                                                  |
|---------------------|---------------------------------|-----------------------------------------------------------------------|
| `validation_error`  | `invalid_parameter`             | Generic Pydantic / business-rule validation failure.                  |
| `validation_error`  | `voice_required`                | Rich endpoint received no `voice`.                                    |
| `validation_error`  | `input_too_long`                | `input` exceeds `TTS_MAX_INPUT_CHARS`.                                |
| `validation_error`  | `ref_audio_invalid`             | Voice CRUD: bad content-type, magic bytes, or size.                   |
| `validation_error`  | `consent_required`              | Voice CRUD: missing/false `consent_acknowledged`.                     |
| `validation_error`  | `voice_id_exists`               | Voice CRUD: POST with an id already in use.                           |
| `validation_error`  | `unknown_provider`              | Request override names an unknown provider.                           |
| `validation_error`  | `unknown_model`                 | Model not in the active provider's allow-list.                        |
| `validation_error`  | `voice_reference_missing`       | Resolved voice has no reference audio on disk.                        |
| `validation_error`  | `not_implemented`               | 501 stub endpoint hit (chat / realtime / transcriptions / translations). |
| `validation_error`  | `preset_unknown`                | Request `preset` name not in the loaded preset registry (FR-PR-07).   |
| `voice_error`       | `voice_not_found`               | Voice CRUD: id does not exist in the metadata repo.                   |
| `voice_error`       | `voice_blob_missing`            | Voice CRUD: metadata exists but the blob is absent.                   |
| `provider_error`    | `model_load_failed`             | Provider failed to load a model.                                      |
| `provider_error`    | `synthesis_failed`              | Provider raised during chunk synthesis.                               |
| `provider_error`    | `no_viable_provider`            | No registered provider supports the detected device.                  |
| `provider_error`    | `voice_seed_ingest_failed`      | Seed ingestion failed at startup (S-011).                             |
| `provider_error`    | `voice_store_unavailable`       | Backend extra (`[postgres]` / `[s3]`) missing or repo init failed.    |
| `capacity_error`    | `queue_full`                    | Admission queue refused work (S-007 / S-010).                         |
| `capacity_error`    | `service_unavailable`           | Downstream backend (Postgres / S3) unavailable.                       |
| `capacity_error`    | `timeout`                       | `TTS_INFERENCE_TIMEOUT_SECONDS` exceeded.                             |
| `internal_error`    | `unexpected_error`              | Unhandled exception fallback (FR-ER-04); message is generic.          |
| `config_error`      | `presets_invalid`               | Startup-fail: `presets.json` schema violation or unknown `TTS_DEFAULT_PRESET` (FR-PR-02 / FR-PR-05). |
| `config_error`      | `preset_provider_invalid`       | Startup-fail: a preset pins a `(provider, model)` outside any provider's allow-list (FR-PR-13).     |
| `config_error`      | `presets_unsafe_permissions`    | Startup-fail: `presets.json` is world-writable or owned by a different uid (NFR-SE-09).             |

## Voice biometric notice (NFR-CP-01 / NFR-PV-04)

> Voice records (audio + metadata) processed by the voice-CRUD endpoints
> constitute **biometric data**.
>
> The service **does** persist these records in the configured store
> backend; deletion is supported (FR-VS-09) and is the operator's
> responsibility for data-subject requests.
>
> The minimal `consent_acknowledged` attestation enforced at FR-VS-05 /
> NFR-CP-01 is **not** a substitute for upstream consent capture in the
> operator's jurisdiction — formal signed-consent records remain a Roadmap
> item.
>
> Voice cloning processes biometric data and operators are responsible for
> upstream consent in their jurisdiction. The voice-CRUD create operation
> enforces a minimal consent attestation: `consent_acknowledged=true` MUST
> be present in the metadata for `POST /v1/tts/voices` to succeed; the
> attestation is stored with the record (FR-VS-04). Formal, signed-consent
> records remain Roadmap.

This is documentation + minimal enforcement, not a compliance guarantee.

## Voice map (seed file)

`TTS_VOICE_MAP_FILE` points at a JSON object whose values are
`VoiceConfig`-shaped entries:

```json
{
  "gold": {
    "ref_audio_path": "/absolute/path/to/gold.wav",
    "ref_text": "Ciao, questa e una voce di riferimento.",
    "language": "Italian",
    "number_lang": "it",
    "temperature": 0.8,
    "top_p": 0.95,
    "target_db": -20.0,
    "max_sentences_per_chunk": 2
  }
}
```

For Qwen-style voice cloning, use short, clean reference audio: 3–10 seconds
(best around ~10 s selected from a longer 30 s+ recording), WAV format, mono,
≥ 16 kHz (24 kHz is a solid default). `ref_text` MUST be the exact
transcript of the chosen clip — alignment strongly improves cloning
stability. A pre-cleaning recipe:

```bash
ffmpeg -y -i input.wav \
  -af "aformat=channel_layouts=mono,aresample=24000:resampler=soxr,highpass=f=80,lowpass=f=8000,afftdn=nf=-23,acompressor=threshold=-21dB:ratio=2.5:attack=4:release=60:makeup=2" \
  -ar 24000 -sample_fmt s16 ref_audio.wav
```

## Performance baseline

NFR-PF-01 sets a +10% regression budget on p50 and p95 between the Sprint-1
baseline and any later re-measurement against the same input + voice +
warm model. The methodology, measurement table, and RISK-8 byte-identity
relaxation contract live in [`docs/perf/baseline.md`](docs/perf/baseline.md).

The S-021 cycle close-out re-runs the measurement on the rich endpoint and
the OpenAI adapter (which now share `synthesize_core`) and appends the new
row to that table.

## Example request

Rich endpoint:

```bash
curl -X POST "http://localhost:8000/v1/tts/synthesize" \
  -H "Content-Type: application/json" \
  -d '{
    "input": "Il 15/04/2026 abbiamo 2 appuntamenti.",
    "voice": "gold",
    "stream": false
  }' \
  --output speech.wav
```

OpenAI-compatible:

```bash
curl -X POST "http://localhost:8000/v1/audio/speech" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
    "provider": "mlx_audio",
    "voice": "gold",
    "input": "Il 15/04/2026 abbiamo 2 appuntamenti.",
    "response_format": "wav"
  }' \
  --output speech.wav
```

Rich endpoint with explicit preset + override (precedence demo):

```bash
curl -X POST "http://localhost:8000/v1/tts/synthesize" \
  -H "Content-Type: application/json" \
  -d '{
    "input": "Il 15/04/2026 abbiamo 2 appuntamenti.",
    "voice": "gold",
    "preset": "quality",
    "language": "it",
    "stream": false
  }' \
  --output speech.flac
# Response includes:
#   X-Preset-Effective: quality(language=it,max_sentences_per_chunk=3,model=Qwen/...,normalize_db=-20.0,provider=mlx_audio,response_format=flac,temperature=0.8,top_p=0.95)
#   X-Preset-Ignored-Knobs: response_format
```

## Voice cloning (S-022 .. S-025)

Voice cloning is a two-step workflow rooted entirely in the voice
store. The cycle-1 inline `ref_audio` field on the rich request has
been retired — the canonical surface is now:

1. **Register the reference voice** via `POST /v1/tts/voices`
   (multipart: `metadata` JSON + `audio` blob). The service hashes,
   validates, and persists the blob through the configured
   `VoiceBlobRepository`. `consent_acknowledged=true` is mandatory
   on POST (NFR-CP-01).
2. **Synthesize with the registered id** by referencing
   `voice="<id>"` on `POST /v1/tts/synthesize` (or on `POST /v1/audio/speech`).

```bash
# Step 1 — register
curl -X POST "http://localhost:8000/v1/tts/voices" \
  -F 'metadata={"id":"gold","transcript":"Ciao, questa è una voce di riferimento.","language":"Italian","consent_acknowledged":true};type=application/json' \
  -F 'audio=@./gold.wav;type=audio/wav'

# Step 2 — synthesize using the registered id
curl -X POST "http://localhost:8000/v1/tts/synthesize" \
  -H "Content-Type: application/json" \
  -d '{
    "input": "Buongiorno, oggi vi racconto una storia.",
    "voice": "gold"
  }' \
  --output speech.wav

# Step 3 (optional) — delete when no longer needed (FR-VS-09)
curl -X DELETE "http://localhost:8000/v1/tts/voices/gold"
```

Operators wanting to ship a voice catalog as part of the deployment
populate the seed map (`TTS_VOICE_MAP_FILE` → `voice_map.json`) instead
of running CRUD POSTs. Seed-ingested records are upserted at startup
with `source="seed"` and are hot-reloaded when the file changes.

## Testing

```bash
uv run pytest                      # standard suite
uv run pytest tests/test_docs_inventory.py   # cross-reference docs↔code
```

Quality gates:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict src/
uv run pytest
uv run pip-audit
```

## Project layout

- `src/llm_tts_api/main.py` — `create_app` + lifespan; mounts every router.
- `src/llm_tts_api/config.py` — `Settings` + env-var parsing.
- `src/llm_tts_api/errors.py` — OpenAI error envelope + handlers.
- `src/llm_tts_api/services/synthesize_service.py` — shared `synthesize_core`.
- `src/llm_tts_api/services/voice_store/` — repos (FS / Postgres / S3) +
  seed ingestor + `VoiceRecord`.
- `src/llm_tts_api/services/tts_providers/` — provider strategies, registry,
  auto-selection.
- `src/llm_tts_api/routers/{health,models,audio,synthesize,voices,chat,realtime}.py`
  — HTTP surface.
- `docs/diagrams/{class,sequence}/` — Mermaid diagrams kept in sync with the
  current code (S-019).
- `docs/openapi/openapi.yaml` — OpenAPI 3.1 spec.
- `docs/perf/baseline.md` — performance baseline and regression policy.
- `docs/specs/` — software spec, FRS, NFR, UAT.
- `tests/` — unit and integration suite (375+ tests).

## Project documents

- Software spec: [`docs/specs/software-spec.md`](docs/specs/software-spec.md)
- Architecture: [`docs/architecture.md`](docs/architecture.md)
- Sprint plans + journal: [`docs/planning/`](docs/planning/)
