# llm-tts-api — Functional Requirements Specification

**Status:** Draft
**Date:** 2026-05-17
**Source request:** `docs/specs/requests/improvement-request.md`
**Reference codebase (quality bar):** `/Volumes/Coding/Projects/Applications/epub/llm-image-api`
**Mode:** Incremental hardening + selected new features. **Not a rewrite.**

---

## 1. Overview

This FRS captures the functional behavior the improved `llm-tts-api` must exhibit. It is organized by functional area, each with atomic requirements (FR-XX-NN), business rules, and acceptance criteria. UAT cases (UAT-XX-NN) trace back to these IDs and live in `analyst-UAT.md`.

Priority legend: **MUST** (blocking), **SHOULD** (strongly desired), **COULD** (nice-to-have).

## 2. Actors

| Actor | Role |
|---|---|
| API Client | Synthesizes speech via HTTP. May be OpenAI-SDK-style or rich-endpoint-aware. |
| Operator | Deploys/runs the service; sets env vars; manages `voice_map.json` and the Dockerfile; reads logs. |
| Voice Curator | Edits `voice_map.json` to register/update reference voices. (Often the same as Operator.) |
| Developer | Contributes code; relies on CI gates (ruff, mypy, pytest, pip-audit, coverage). |
| Roadmap Reader | Future implementer of the stubbed endpoints; consumes the Roadmap section. |

## 3. Data Entities

| Entity | Key fields | Lifecycle |
|---|---|---|
| **DeviceProfile** | `device` (mps\|cuda\|cpu), `dtype`, `source` (auto\|env_override) | Computed once at startup; immutable per process. |
| **ProviderBinding** | `provider_name`, `default_model`, `allowed_models`, `supports_devices` | Loaded at startup from env; immutable per process. |
| **VoiceEntry** | `id`, `ref_audio_path`, `ref_text`, `language`, `number_lang`, `temperature`, `top_p`, `target_db`, `max_sentences_per_chunk` | Loaded from voice map at startup; hot-reloadable atomically. |
| **InlineRefAudio** | `bytes` (≤ size cap), `content_type`, `transcript` (ref_text equivalent), optional `language` | Per-request; ephemeral temp file; destroyed after response. |
| **SynthesisRequest** | text, voice ref (id OR inline), provider override?, model override?, stream flag, output format, normalization knobs, chunking knobs | Per-request; passes through preprocess → chunk → synth → normalize → encode. |
| **SynthesisResult** | audio bytes (streamed or buffered), provider/model used, chunk count, total duration, request_id | Per-request; written to response body + headers. |
| **ModelCacheEntry** | `provider+model_id`, loaded model handle, last-used timestamp | LRU; size configurable (default 1). |
| **ErrorEnvelope** | `error.type` (category), `error.code` (sub-code), `error.message`, `error.param?`, `error.request_id` | Per-failed-request; logged and returned. |

---

## 4. Functional Requirements

### 4.1 Hardware & Provider Auto-Detection (FR-HW)

**FR-HW-01 (MUST)** — At startup, the service MUST detect the inference device in this order: Apple Silicon MPS, then NVIDIA CUDA, then CPU. The detection result MUST be exposed as a `DeviceProfile`.
*Trace:* request §2 G2, §3 item 1.
*Acceptance:* on Apple Silicon, `DeviceProfile.device == "mps"`; on a CUDA host, `"cuda"`; otherwise `"cpu"`. Detection unit-tested via monkeypatched torch availability (mirroring `llm-image-api/tests/test_device.py`).

**FR-HW-02 (MUST)** — The env var `TTS_DEVICE` (values: `auto|mps|cuda|cpu`) MUST override auto-detection. Default is `auto`.
*Acceptance:* with `TTS_DEVICE=cpu`, the profile is `cpu` even on Apple Silicon.

**FR-HW-03 (MUST)** — Dtype MUST be selected per device with env override `TTS_DTYPE` (`auto|float16|bfloat16|float32`). `auto` resolves to `float16` on MPS/CUDA and `float32` on CPU.
*Acceptance:* startup log line includes `device=<x> dtype=<y> source=<auto|env>`.

**FR-HW-04 (MUST)** — Provider selection MUST be derivable from `DeviceProfile` when `TTS_PROVIDER` is unset (or set to `auto`):
- `mps` → first available of: `mlx_audio`, `voxtral`.
- `cuda` → first available of: `vllm_omni`, future torch-based providers.
- `cpu` → first registered provider that declares CPU support.
*Acceptance:* `GET /health` reports the auto-selected `provider` and the `source` (auto vs override).

**FR-HW-05 (MUST)** — If auto-detection lands on CPU and **no** registered provider supports CPU, the service MUST fail startup with a typed error listing each provider considered and the reason it was rejected. The process MUST exit non-zero.
*Trace:* Round 1 decision.
*Acceptance:* startup log emits `provider_error.no_viable_provider` with the rejection table; process exits ≠ 0.

**FR-HW-06 (MUST)** — `TTS_PROVIDER` MUST remain a valid override (not a default). When set, it bypasses auto-selection but MUST still be validated against the detected device's supported-provider list. Incompatible combinations (e.g. `cuda`-only provider on Apple Silicon) MUST fail startup.

**FR-HW-07 (SHOULD)** — A registered provider MUST declare a `supports_devices: set[Device]` capability. The provider registry MUST use this for auto-selection and validation.

### 4.2 Rich Endpoint Surface (FR-EP)

**FR-EP-01 (MUST)** — A new endpoint `POST /v1/tts/synthesize` MUST exist exposing the full capability surface (see FR-EP-02). It is the source of truth for synthesis behavior.
*Trace:* request §2 G3; Round 1 decision (no `/v2/` jump).

**FR-EP-02 (MUST)** — `POST /v1/tts/synthesize` request body MUST accept (Pydantic, `extra="forbid"`):
- `input` (string, required, ≤ `TTS_MAX_INPUT_CHARS`)
- `voice` (string, required) — id of a voice in the voice store (§4.4)
- `provider` (string, optional override)
- `model` (string, optional override; must be in provider's allow-list)
- `response_format` (enum: `wav` MUST; `mp3|opus|flac|pcm` SHOULD if available)
- `stream` (bool, default `false`)
- `normalize_db` (float, optional override of voice's `target_db`)
- `max_sentences_per_chunk` (int, optional override)
- `language` (string, optional override of voice's `language`)
- `number_lang` (string, optional override of voice's `number_lang`)
- `temperature`, `top_p` (float, optional overrides)

**FR-EP-03 (MUST)** — `voice` is required. If absent → `validation_error.voice_required`. If the id does not exist in the voice store → `voice_error.voice_not_found`. The endpoint MUST NOT accept inline reference-audio bytes; voices are managed exclusively via the CRUD surface (§4.4).

**FR-EP-04 (MUST)** — Response: `200` with body = audio bytes in requested format. Headers MUST include:
- `X-Request-ID` (always)
- `X-Provider`, `X-Model`, `X-Device`, `X-Dtype`
- `X-Chunks` (count), `X-Total-Duration-Ms`
- `Content-Type` matching the format
*Note:* Per OpenAI compatibility, no per-chunk JSON metadata frames — metadata is in headers only.

**FR-EP-05 (MUST)** — When `stream=true`, the response MUST use chunked transfer encoding and write audio bytes as each chunk completes synthesis. Headers in FR-EP-04 MUST be present at response start; `X-Chunks` and `X-Total-Duration-Ms` MAY be omitted from streaming responses (unknown at start) and emitted as response **trailers** if the client supports them. Streaming MUST NOT block the event loop.

### 4.3 OpenAI Adapter (FR-OA)

**FR-OA-01 (MUST)** — `POST /v1/audio/speech` MUST remain available with its current OpenAI-compatible request shape and behavior.
*Trace:* request §5 (OpenAI compatibility constraint).

**FR-OA-02 (MUST)** — `POST /v1/audio/speech` MUST be implemented as a **thin translator** over `POST /v1/tts/synthesize`. No duplicated business logic for chunking, normalization, synthesis, or error mapping.
*Acceptance:* the handler maps OpenAI fields → rich-endpoint fields, delegates, and translates the response back. Code review check: no calls into `SpeechSynthesizer` from this handler that bypass the rich path.

**FR-OA-03 (MUST)** — Streaming via the OpenAI SDK (`with_streaming_response.create(...)`) MUST work end-to-end and deliver chunked audio bytes equivalent to a streamed `/v1/tts/synthesize` call.

**FR-OA-04 (MUST)** — `GET /v1/models` MUST remain available and MUST list the same provider/model pairs the rich endpoint accepts.

### 4.4 Voice CRUD & Pluggable Storage (FR-VS)

Replaces the former inline-ref_audio path (per OQ-3 decision). Voice records are managed via CRUD and persisted through two abstracted backends.

**Endpoint namespace decision:** rich CRUD lives under `/v1/tts/voices/*` (consistent with `/v1/tts/synthesize`). The path `/v1/audio/voices` remains **reserved** for an OpenAI-compatibility adapter — currently 501 stub, mirroring the `/v1/audio/speech` ↔ `/v1/tts/synthesize` pattern. If/when OpenAI publishes a stable voice-management contract, the `/v1/audio/voices` adapter becomes a thin translator over `/v1/tts/voices/*`. This avoids prematurely committing to a shape OpenAI hasn't defined.

**FR-VS-01 (MUST)** — A `VoiceMetadataRepository` Protocol MUST abstract metadata storage with operations: `list`, `get(id)`, `create(record)`, `update(id, record)`, `delete(id)`, `exists(id)`. Two backends MUST be implemented:
- `FsJsonMetadataRepository` — default; stores a JSON document under a service-controlled directory (path via `TTS_VOICE_STORE_DIR`, default `var/voices/`). Atomic writes via tempfile + rename.
- `PostgresMetadataRepository` — opt-in via optional dependency group `[postgres]`; selected when `TTS_VOICE_METADATA_BACKEND=postgres`. Connection string via `TTS_VOICE_METADATA_DSN`.

**FR-VS-02 (MUST)** — A `VoiceBlobRepository` Protocol MUST abstract audio-blob storage with operations: `put(id, bytes)`, `get(id) -> bytes`, `delete(id)`, `exists(id)`. Two backends MUST be implemented:
- `FsBlobRepository` — default; stores `<TTS_VOICE_STORE_DIR>/<id>.wav`. Atomic writes via tempfile + rename.
- `S3BlobRepository` — opt-in via optional dependency group `[s3]`; selected when `TTS_VOICE_BLOB_BACKEND=s3`. Configured via `TTS_VOICE_BLOB_S3_ENDPOINT`, `TTS_VOICE_BLOB_S3_BUCKET`, `TTS_VOICE_BLOB_S3_REGION`, and AWS-SDK-standard credential env vars.

**FR-VS-03 (MUST)** — Default backends (`fs_json` + `fs`) MUST require **zero additional dependencies** beyond the base install. The `[postgres]` and `[s3]` groups MUST be optional extras declared in `pyproject.toml`.

**FR-VS-04 (MUST)** — A voice record schema MUST contain at minimum:
- `id` (string, slug-shaped: `[a-z0-9_-]{1,64}`)
- `transcript` (string, required) — equivalent of `ref_text` for cloning providers
- `language` (string, required)
- `number_lang` (string, optional; defaults to `language`)
- `target_db` (float, optional; default `-20.0`)
- `temperature`, `top_p` (float, optional)
- `max_sentences_per_chunk` (int, optional)
- `consent_acknowledged` (bool, required at create — MUST be `true` to persist)
- `source` (enum: `seed | crud`, set by the service, not the client)
- `created_at`, `updated_at` (timestamps, set by the service)

**FR-VS-05 (MUST)** — `POST /v1/tts/voices` accepts multipart upload (audio file part + JSON metadata part) and creates a record. Failure modes:
- Missing `consent_acknowledged=true` → `validation_error.consent_required`.
- Duplicate `id` → `validation_error.voice_id_exists`.
- Invalid audio (size, content-type, magic bytes per NFR-SE-01..03) → `validation_error.ref_audio_invalid`.
- Schema validation failure → `validation_error` with `param` set.

**FR-VS-06 (MUST)** — `GET /v1/tts/voices` returns a list of voices (id, language, source, created_at). It MUST NOT include `transcript` or any file paths/blob URIs.

**FR-VS-07 (MUST)** — `GET /v1/tts/voices/{id}` returns the full metadata record (excluding paths/URIs). Audio bytes are NEVER returned from this endpoint.

**FR-VS-07b (MUST)** — `GET /v1/tts/voices/{id}/audio` returns the audio blob as `audio/wav` body with metadata in `X-*` headers (`X-Voice-Id`, `X-Voice-Source`, `X-Content-Sha256`). 404 if the voice exists in metadata but the blob is missing → `voice_error.voice_blob_missing`.

**FR-VS-08 (MUST)** — `PUT /v1/tts/voices/{id}` updates metadata. If the multipart payload includes a new audio part, the blob is replaced atomically (write new → switch pointer → delete old). Changing `id` is NOT supported (clients must delete + recreate).

**FR-VS-09 (MUST)** — `DELETE /v1/tts/voices/{id}` removes both metadata and blob. If either delete fails, the operation is retried; persistent failure logs `provider_error` and returns `500`. Soft-delete is NOT in scope.

**FR-VS-10 (MUST)** — Voice resolution at synthesis time (FR-EP-03) goes through the repositories: metadata fetched, blob streamed to a per-request temp file (or directly accessed by reference, depending on backend), used for synthesis, temp artifacts cleaned in `finally`. Concurrent requests for the same voice MUST share read access without locking each other out.

**FR-VS-11 (MUST)** — When the `fs` blob backend is used, voice files MUST live under `TTS_VOICE_STORE_DIR`. Paths derived from the voice id MUST be normalized and sandboxed — any traversal attempt (e.g. id containing `..` or `/`) is rejected at FR-VS-04 validation.

**FR-VS-12 (SHOULD)** — Response headers on a synthesis request that resolved a voice MUST include `X-Voice-Id` and `X-Voice-Source: seed | crud` (derived from the `source` field), enabling clients to distinguish provenance.

### 4.5 Voice Seed Ingestion (FR-VM)

The legacy `voice_map.json` survives as an **ingestion seed**, not the runtime source. Voices listed in it are upserted into the voice store at every startup and on file change.

**FR-VM-01 (MUST)** — At startup, if `TTS_VOICE_MAP_FILE` is set and the file exists, the service MUST parse and validate it. For each entry whose `id` is NOT already in the store, the service MUST upsert a record (`source="seed"`) by copying the referenced ref_audio file (still on disk per the JSON) into the blob store. Entries whose `id` already exists in the store are skipped (idempotent re-startup).

**FR-VM-02 (MUST)** — The seed file MUST be watched (watchfiles). On file change, ingestion runs again with the same idempotent semantics.

**FR-VM-03 (MUST)** — Ingestion MUST be **atomic per-entry and overall**: each entry is validated fully (schema + ref_audio file exists + readable) before any write to the store. If validation fails for any entry, NO entries from that ingestion pass are applied. The previous store state is preserved; an error log line `provider_error.voice_seed_ingest_failed` is emitted with diagnostics.

**FR-VM-04 (MUST)** — In-flight synthesis requests MUST continue using the voice record they resolved at request start; concurrent ingestion does not interrupt them.

**FR-VM-05 (SHOULD)** — When `TTS_VOICE_MAP_FILE` is unset or the file is absent, the service MUST start cleanly without seed ingestion. The store may be empty until voices are created via CRUD.

### 4.6 Concurrency, Queueing & Cancellation (FR-CC)

**FR-CC-01 (MUST)** — Concurrency MUST be gated by an `asyncio.Semaphore` (NOT `threading.Semaphore`). The bound is `TTS_MAX_CONCURRENT_REQUESTS` (default 1).
*Trace:* request §3 item 3 — current implementation blocks the event loop.

**FR-CC-02 (MUST)** — Sync provider calls MUST be dispatched via `anyio.to_thread.run_sync` so the event loop stays responsive.

**FR-CC-03 (MUST)** — A queue admission semaphore MUST cap pending+active requests at `TTS_MAX_QUEUE_DEPTH` (default 8). Requests rejected at admission MUST return `429` with `capacity_error.queue_full`.

**FR-CC-04 (MUST)** — Per-engine serialization (where required by the provider) MUST use `asyncio.Lock`, scoped per (provider, model).

**FR-CC-05 (SHOULD)** — When the client disconnects mid-synthesis, the service SHOULD detect this (via FastAPI request `is_disconnected`) and stop further chunk synthesis at the next chunk boundary. Already-allocated temp files MUST still be cleaned up.

### 4.7 Model Cache & Lifecycle (FR-CA)

**FR-CA-01 (MUST)** — Loaded models MUST be held in an LRU cache keyed by `(provider, model_id)`. Cache size is `TTS_MODEL_CACHE_SIZE` (default `1`).
*Trace:* Round 1 decision.

**FR-CA-02 (MUST)** — Eviction MUST release the underlying resources (provider-defined `unload()` if available; otherwise drop reference and rely on GC). Eviction MUST NOT interrupt an in-flight request using that model.

**FR-CA-03 (MUST)** — Cache validation MUST occur before mutation: the requested `model_id` MUST be in the provider's allow-list AND any local file dependencies MUST exist BEFORE evicting the current entry. Failure → request error with current entry preserved.

**FR-CA-04 (SHOULD)** — `TTS_PRELOAD_MODELS` (comma-separated `provider:model` pairs) MUST cause those models to load during startup warmup, contributing to readiness gating.

### 4.8 Health, Readiness & Lifecycle (FR-HL)

**FR-HL-01 (MUST)** — `GET /health` MUST be lock-free, always return `200`, and include: `status: "ok"`, `version`, `device`, `dtype`, `provider`, `model_loaded` (current cached model ids), `queue_depth`, `concurrent_active`.

**FR-HL-02 (MUST)** — `GET /ready` MUST return `200` only after startup warmup completes (incl. preload of `TTS_PRELOAD_MODELS`) and the voice map is valid. During warmup, shutdown drain, or after an irrecoverable error → `503` with structured body `{ ready: false, reason: "<code>" }`.

**FR-HL-03 (MUST)** — Lifespan singletons (settings, voice map, provider registry, model cache, semaphores, request-id context) MUST be wired into `app.state` during FastAPI lifespan startup and disposed in shutdown.

**FR-HL-04 (MUST)** — On SIGTERM/SIGINT, the service MUST refuse new requests at admission, drain in-flight requests up to `TTS_SHUTDOWN_DRAIN_SECONDS` (default 30s), then force-exit.

**FR-HL-05 (SHOULD)** — At startup, a `psutil`-based memory check SHOULD emit a `WARNING` (soft, not hard fail) if available memory is below a configurable floor (`TTS_MIN_FREE_MEMORY_GB`, default `4`).

### 4.9 Observability (FR-OB)

**FR-OB-01 (MUST)** — Every incoming request MUST be assigned an `X-Request-ID` if absent, propagated through async context, and emitted on every related log line.

**FR-OB-02 (MUST)** — Logs MUST use a single structured format: timestamp, level, logger, request_id (when applicable), message, and key/value extras. JSON-formatted output MUST be opt-in via `APP_LOG_FORMAT=json` (default human-readable).

**FR-OB-03 (MUST)** — Response headers FR-EP-04 MUST be set on all successful responses. Error responses MUST set `X-Request-ID` and `X-Error-Code`.

**FR-OB-04 (SHOULD)** — A minimal in-process metrics surface (`/metrics` Prometheus text format) is OUT of scope for this cycle and SHOULD be tracked in the Roadmap; nothing in the code prevents adding it later.

### 4.10 Configuration & Validation (FR-CF)

**FR-CF-01 (MUST)** — Configuration MUST remain env-driven (`.env`, `.env.local`, process env) with a `Settings` dataclass validated in `__post_init__`. Invalid values MUST fail startup with a clear message.

**FR-CF-02 (MUST)** — All new env vars introduced by this FRS MUST be documented in README and have a `TTS_` or `APP_` prefix. Inventory (non-exhaustive): `TTS_DEVICE`, `TTS_DTYPE`, `TTS_PROVIDER` (now override), `TTS_MAX_INPUT_CHARS`, `TTS_MAX_CONCURRENT_REQUESTS`, `TTS_MAX_QUEUE_DEPTH`, `TTS_MODEL_CACHE_SIZE`, `TTS_PRELOAD_MODELS`, `TTS_VOICE_MAP_FILE` (seed only), `TTS_VOICE_STORE_DIR` (default `var/voices/`), `TTS_VOICE_METADATA_BACKEND` (`fs_json|postgres`, default `fs_json`), `TTS_VOICE_METADATA_DSN` (required when `postgres`), `TTS_VOICE_BLOB_BACKEND` (`fs|s3`, default `fs`), `TTS_VOICE_BLOB_S3_ENDPOINT`, `TTS_VOICE_BLOB_S3_BUCKET`, `TTS_VOICE_BLOB_S3_REGION` (required when `s3`), `TTS_REFAUDIO_MAX_BYTES`, `TTS_SHUTDOWN_DRAIN_SECONDS`, `TTS_INFERENCE_TIMEOUT_SECONDS` (optional, default unset → disabled), `TTS_MIN_FREE_MEMORY_GB`, `APP_LOG_FORMAT`.

**FR-CF-03 (MUST)** — `TTS_INFERENCE_TIMEOUT_SECONDS` MUST default to **unset** (no global timeout). When set to a positive number, `asyncio.wait_for` MUST wrap synthesis and exceeding it returns `499`/`504` with `capacity_error.timeout`.
*Trace:* Round 2 decision (configurable, disabled by default).

### 4.11 Error Model (FR-ER)

**FR-ER-01 (MUST)** — All errors MUST be returned in an OpenAI-compatible envelope:
```
{ "error": { "type": <category>, "code": <sub_code>, "message": <human>, "param": <field|null>, "request_id": <id> } }
```

**FR-ER-02 (MUST)** — `type` (category) MUST be one of:
| `type` | HTTP status range | Examples of `code` |
|---|---|---|
| `validation_error` | 400, 422 | `voice_required`, `input_too_long`, `ref_audio_invalid`, `consent_required`, `voice_id_exists`, `unknown_provider`, `unknown_model` |
| `voice_error` | 404, 422 | `voice_not_found`, `voice_blob_missing` |
| `provider_error` | 502, 500 | `model_load_failed`, `synthesis_failed`, `no_viable_provider`, `voice_seed_ingest_failed`, `voice_store_unavailable` |
| `capacity_error` | 429, 503, 504 | `queue_full`, `service_unavailable`, `timeout` |
| `internal_error` | 500 | `unexpected_error` |
*Trace:* Round 2 decision (broad categories with sub-codes).

**FR-ER-03 (MUST)** — Every error path MUST set `X-Request-ID` and `X-Error-Code` response headers.

**FR-ER-04 (MUST)** — `internal_error.unexpected_error` MUST NOT leak stack traces, file paths, or model internals to clients. Full traceback goes to logs only.

### 4.12 Quality Gates (FR-QG) — engineering-baseline parity

**FR-QG-01 (MUST)** — CI MUST run: `ruff check` + `ruff format --check`, `mypy --strict src/`, `pytest --cov` with `fail_under=80`, `pip-audit`.
*Trace:* request §3 items 14–16.

**FR-QG-02 (MUST)** — All request models MUST set `model_config = ConfigDict(extra="forbid")`. All response models MUST be explicit (no untyped dicts).

**FR-QG-03 (MUST)** — `py.typed` marker MUST ship with the package. Public engine/service interfaces MUST be `Protocol`-typed.

**FR-QG-04 (SHOULD)** — Dockerfile MUST be updated when paths/env vars change; CI MUST verify `docker build` succeeds on every PR.

### 4.13 Documentation (FR-DC)

**FR-DC-01 (MUST)** — README MUST document: hardware auto-detection rules, all new env vars, the rich endpoint surface, the voice-CRUD endpoints under `/v1/tts/voices/*` (incl. consent attestation), the voice-seed ingestion mechanism, the storage-backend selection matrix (defaults vs `[postgres]`/`[s3]` extras), the error taxonomy table.

**FR-DC-02 (MUST)** — `docs/diagrams/` MUST be updated where structure changes (lifespan startup, request flow, voice map reload).

**FR-DC-03 (MUST)** — `docs/openapi/openapi.yaml` MUST cover `/v1/tts/synthesize` AND the updated `/v1/audio/speech` shape (semantics unchanged).

---

### 4.14 Audio-Generation Presets (FR-PR) — *cycle 2*

> Source: `docs/specs/requests/dual-mode-presets-request.md` (PO scoped decisions D1-D10 + BA challenge rounds resolving OQ-1..10).
> Reference: `/Volumes/Coding/Projects/Applications/epub/llm-image-api/config/presets.json` shape.

**FR-PR-01 (MUST)** — The service MUST load named audio-generation presets from `config/presets.json` at startup. Three presets MUST ship out of the box: `fast`, `balanced`, `quality`. Operators MAY add custom presets to the file.

**FR-PR-02 (MUST)** — `config/presets.json` MUST be validated against a Pydantic `PresetConfig` model with `extra="forbid"` at startup. Validation failures MUST cause startup to exit non-zero with error code `config_error.presets_invalid` and a message identifying the offending field path (e.g. `presets.quality.defaults.temperature`).

**FR-PR-03 (MUST)** — Each preset MUST carry a `label`, `description`, and `defaults` block. `defaults` MAY contain any subset of: `provider`, `model`, `temperature`, `top_p`, `max_sentences_per_chunk`, `normalize_db`, `response_format`, `postprocess` (object with `rms_normalize: bool`, `silence_trim: bool`, `denoise: bool`). Unspecified fields fall through to existing system defaults.

**FR-PR-04 (MUST)** — `SynthesizeRequest` MUST accept an optional `preset: str` field. Pydantic-level type is `str` (NOT `Literal[...]`) so operator-added custom presets work without OpenAPI regeneration. Validation against the loaded preset registry happens in the resolver (FR-PR-06).

**FR-PR-05 (MUST)** — The server-side default preset MUST be `balanced`. The default MUST be overridable via env var `TTS_DEFAULT_PRESET`. An invalid `TTS_DEFAULT_PRESET` value MUST cause startup failure (same tier as `config_error.presets_invalid`).

**FR-PR-06 (MUST)** — Preset resolution MUST live in `services/synthesize_service.py` and produce a frozen `EffectiveSynthesisConfig` dataclass consumed by all downstream synthesis code. Resolution precedence (highest-priority wins per-field):
1. Explicit field on the request body
2. Preset's `defaults` block (preset named in request, or server default if omitted)
3. `Settings` / `VoiceRecord` defaults

**FR-PR-07 (MUST)** — When a request passes a `preset` name not present in the loaded registry, the service MUST return `400 validation_error.preset_unknown` with the available preset names listed in the error message.

**FR-PR-08 (MUST)** — When an explicit request field contradicts a preset pin (e.g. preset pins `provider="voxtral"` and request body says `provider="mlx_audio"`), the explicit field MUST win. The conflict MUST be logged at WARN level with the request_id. The response MUST include header `X-Preset-Effective: <preset-name>(field=value,...)` listing the resolved effective config.

**FR-PR-09 (MUST)** — When the active provider cannot honor a preset-supplied knob that is not a hard schema requirement (e.g. preset wants `temperature=0.5` but provider's `synthesize_chunks` ignores temperature), the request MUST succeed, the unsupported knobs MUST be soft-ignored, and the response MUST include header `X-Preset-Ignored-Knobs: knob1,knob2,...`. Postprocessing knobs run in the service-layer and are always honored regardless.

**FR-PR-10 (MUST)** — `POST /v1/audio/speech` (OpenAI-compat path) MUST always use the server default preset. The OpenAI request body MUST NOT accept a `preset` field, and no query-string escape hatch is exposed. This preserves the S-018 byte-identity contract.

**FR-PR-11 (SHOULD)** — `config/presets.json` SHOULD be hot-reloadable via the same `watchfiles` + polling-fallback primitive used for `voice_map.json` (S-011). In-flight requests MUST snapshot the preset registry at request-start and use that snapshot to completion (no mid-request preset changes).

**FR-PR-12 (MUST)** — Custom operator-defined presets in `config/presets.json` are usable on `POST /v1/tts/synthesize` but MUST NOT be enumerated in `/v1/models` or in `docs/openapi/openapi.yaml`. The OpenAPI `preset` field MAY document the three built-ins as informational examples; type stays open-string at the schema level.

**FR-PR-13 (MUST)** — A preset that pins a `(provider, model)` pair not present in any provider's allow-list MUST cause startup failure with error code `config_error.preset_provider_invalid`.

---

### 4.15 Audio Post-Processing (FR-PP) — *cycle 2*

**FR-PP-01 (MUST)** — A new module `services/audio_postprocess.py` MUST expose a pure-function pipeline `postprocess_audio(audio: bytes, *, rms_normalize: bool, silence_trim: bool, denoise: bool, settings: Settings) -> bytes` operating on a fully-assembled WAV body.

**FR-PP-02 (MUST)** — Pipeline ordering MUST be deterministic and documented: **denoise → silence_trim → rms_normalize**. Each step is a no-op when its flag is false. The ordering rationale (denoise removes noise that would otherwise inflate trim thresholds; trim removes leading/trailing silence; normalize sets final loudness) MUST be recorded as a code comment in the module.

**FR-PP-03 (MUST)** — `rms_normalize` MUST target the dBFS value supplied via either the request's `normalize_db` field or the preset's `defaults.normalize_db`. Resolved value of `None` means the step is skipped even when the flag is true (no-op).

**FR-PP-04 (MUST)** — `silence_trim` MUST remove leading + trailing silence below a configurable threshold (default `-50 dBFS`; tunable via `Settings.tts_silence_trim_threshold_db`). MUST preserve at least a small head/tail pad (default 50 ms) so playback doesn't sound clipped.

**FR-PP-05 (SHOULD)** — `denoise` MUST be implemented behind an optional dependency extra `[denoise]` (analogous to `[postgres]` / `[s3]` from Sprint 3). When the extra is not installed, `denoise=true` in a request or preset MUST log a WARN-level message and silently no-op (NOT a request error — operator's choice to deploy without the extra is honored).

**FR-PP-06 (MUST)** — When any post-processing step runs (any of the three flags is effectively true), the response header `X-Postprocess-Applied` MUST list the applied steps (e.g. `X-Postprocess-Applied: silence_trim,rms_normalize`). Absent when no post-processing ran.

**FR-PP-07 (MUST)** — If `preset="quality"` AND `stream=true` are both present on a request, the service MUST silently downgrade to buffered mode, run the full post-processing pipeline, and return a non-streaming response. The downgrade MUST be observable: response header `X-Stream-Downgraded: quality-postproc` MUST be set; no streaming trailers are emitted.

**FR-PP-08 (MUST)** — Post-processing MUST run AFTER provider chunk assembly and BEFORE response encoding (the format conversion step). The insertion point in `synthesize_service.py` is the same wall the streaming-vs-buffered branch lives on.

---

### 4.16 Response Format Extension (FR-FMT) — *cycle 2*

**FR-FMT-01 (MUST)** — `SynthesizeRequest.response_format` MUST be extended from `Literal["wav"]` to `Literal["wav", "wav24", "flac"]`. `wav` = 16-bit PCM (existing); `wav24` = 24-bit PCM; `flac` = FLAC lossless compressed.

**FR-FMT-02 (MUST)** — Each `TTSProviderStrategy` MUST declare a `supported_response_formats: set[Literal["wav", "wav24", "flac"]]` capability (analogous to S-006's `supports_devices`). The mlx_audio / voxtral / vllm_omni providers' day-one declarations MUST be measured (not assumed) before this cycle merges.

**FR-FMT-03 (MUST)** — When a request explicitly sets `response_format` to a value the active provider does NOT declare in `supported_response_formats`, the service MUST return `400 validation_error.format_unsupported` with the supported set in the error message (`message: "Provider 'voxtral' supports only: wav, wav24. Requested: flac"`).

**FR-FMT-04 (MUST)** — When a preset pins a `response_format` not supported by the provider that startup auto-selection would pick, the service MUST refuse to start with `config_error.preset_provider_invalid` (FR-PR-13). Preset+format mismatch is a deployment-time error, not a runtime one.

**FR-FMT-05 (MUST)** — The `quality` preset's default `response_format` MUST be `flac`. The `fast` and `balanced` presets' defaults MUST remain `wav` (so existing /v1/audio/speech callers see no format change).

**FR-FMT-06 (MUST)** — Format conversion MUST occur in the service-layer (`services/synthesize_service.py`), AFTER post-processing (FR-PP-08), using `soundfile` (already a project dep) for FLAC and 24-bit WAV. The provider's native output (typically 16-bit WAV) is the canonical intermediate; conversion is one-way.

**FR-FMT-07 (MUST)** — Response `Content-Type` MUST match the resolved `response_format`: `audio/wav` for `wav` and `wav24`; `audio/flac` for `flac`.

---

## 5. Business Rules

| ID | Rule |
|---|---|
| BR-1 | Voice resolution order: explicit `voice` id → voice map lookup; otherwise inline `ref_audio` is required. Both → error. Neither → error. |
| BR-2 | Provider resolution order: explicit `provider` field → env `TTS_PROVIDER` → auto from `DeviceProfile`. |
| BR-3 | Model resolution order: explicit `model` field → provider's `default_model`. Must be in provider's `allowed_models`. |
| BR-4 | Streaming responses still pass through chunking + per-chunk normalization; the only difference is bytes are flushed per chunk instead of buffered. |
| BR-5 | Hot-reload of voice map never invalidates voice ids referenced by in-flight requests; they finish on the snapshot they began with. |
| BR-6 | Auto-detection picks device first, then provider from device capability; env overrides are validated against device, not the other way around. |
| BR-7 | A configured but currently-incompatible env override (e.g. `TTS_PROVIDER=vllm_omni` on Apple Silicon CPU-only host) is a startup error, not a runtime error. |
| BR-8 | Synthesis-time temp files derived from a voice blob are deleted in `finally`, regardless of exception class. Voice records themselves persist in the store until explicit DELETE. |
| BR-9 | The OpenAI adapter never reads or writes private state of the rich-endpoint service layer — only the public request/response surface. |
| BR-10 (*cycle 2*) | Preset resolution precedence is **explicit request field > preset defaults > Settings/VoiceRecord defaults**. The first-set-wins layer is captured in `EffectiveSynthesisConfig`. |
| BR-11 (*cycle 2*) | Hot-reload of `config/presets.json` never affects in-flight requests; they finish on the preset registry snapshot taken at request-start. |
| BR-12 (*cycle 2*) | `POST /v1/audio/speech` ignores any `preset` field in the body (rejected by `SpeechRequest.extra="forbid"`) AND any `?preset=` query string (no escape hatch). It always resolves to `TTS_DEFAULT_PRESET`. |
| BR-13 (*cycle 2*) | Quality preset + `stream=true` silently downgrades to buffered. The downgrade is observable via response header `X-Stream-Downgraded: quality-postproc`, not via error. |
| BR-14 (*cycle 2*) | Post-processing pipeline order is **denoise → silence_trim → rms_normalize**. Each step is a no-op when its flag is false. |
| BR-15 (*cycle 2*) | Format conversion runs in the service layer AFTER post-processing AND AFTER the provider's native (typically WAV16) output is assembled. Provider native format is the canonical intermediate. |
| BR-16 (*cycle 2*) | Preset-pinned `(provider, model, response_format)` mismatches with auto-selected provider's capabilities are **startup-fail** errors (`config_error.preset_provider_invalid`), not runtime errors. |
| BR-17 (*cycle 2*) | Soft-ignore of unsupported preset knobs (per provider) is reported via `X-Preset-Ignored-Knobs` response header. Service-layer-driven knobs (postprocess, format conversion) are NEVER soft-ignored. |

---

## 6. Roadmap (explicitly OUT of scope for this cycle)

The following stay 501-stubbed but are captured here for follow-up cycles. Each item lists a rough sequencing dependency on this cycle's parity work.

| Roadmap item | Endpoint(s) | Depends on (from this cycle) |
|---|---|---|
| **Formal signed-consent records** | `POST /v1/audio/voice_consents/*` | FR-VS CRUD store; auth (not in this cycle) |
| **STT — transcription / translation** | `/v1/audio/transcriptions`, `/v1/audio/translations` | FR-HW provider registry pattern; new STT provider class |
| **Chat completions** (TTS-flavored) | `/v1/chat/completions`, `/v1/chat/models` | TBD; possibly out-of-charter for a TTS service |
| **Realtime bidirectional** | `/v1/realtime/*` (WebSocket) | FR-CC cancellation; chunk-level streaming infrastructure |
| **Prometheus `/metrics`** | `GET /metrics` | FR-OB structured logging + request ids |
| **Content-addressable audio cache** | implicit | FR-CA model cache; hash key over normalized text + voice + params |
| **Token/sentence-level streaming** | rich endpoint | FR-EP streaming groundwork |
| **Parallel chunk synthesis** | internal | FR-CC concurrency model |
| **SSML / prosody markup** | rich endpoint field | text preprocessing extension |
| **MP3/Opus/Flac encoding** | `response_format` values | encoder integration; ffmpeg or pyav dependency call |
| **In-process rate limiting** | middleware | request id context; in-memory token bucket |
| **Voice preview endpoint** (`GET /v1/voices/{id}/preview`) | derives from voice map | FR-VM listing |

---

## 7. Assumptions (flagged — would invalidate dependent requirements if wrong)

| ID | Assumption | Impact if wrong |
|---|---|---|
| A-1 | `mlx-audio` exposes (or can be wrapped to expose) a way to declare CPU support or refuse load on non-Apple-Silicon. | FR-HW-04/05 logic may need a hardcoded device→provider table instead of capability declarations. |
| A-2 | The current `SpeechSynthesizer` can be refactored in-place to expose an async API surface without changing provider strategies' signatures. | If refactor is too invasive, the "no rewrite" constraint forces a thinner async wrapper, leaving some sync wait points. |
| A-3 | `watchfiles` works reliably inside the Docker container on a bind-mounted config dir. | FR-VM-02 may need a polling fallback for container deployments. |
| A-4 | Trailing response headers are usable by the typical clients of this API (chunked transfer trailers). | FR-EP-05 may degrade to omitting `X-Chunks`/`X-Total-Duration-Ms` on streamed responses entirely. |
| A-5 | All current providers can run with `anyio.to_thread.run_sync` without losing their existing concurrency guarantees. | FR-CC-02 may need provider-specific exceptions. |
| A-PR-1 (*cycle 2*) | Operators ship `balanced` preset defaults that match cycle-1 defaults (`temperature=null`, `top_p=null`, `max_sentences_per_chunk=null`, `normalize_db=null`, `response_format="wav"`) — otherwise existing /v1/audio/speech callers see a behavior change. Documented as a migration note. | UAT-PR-06 byte-identity verifies post-deploy; cycle-1 S-018 paired UAT must still pass with default config. |
| A-PR-2 (*cycle 2*) | `soundfile` (already a project dep) supports writing 24-bit WAV (`subtype="PCM_24"`) and FLAC end-to-end on all supported platforms. | FR-FMT-06 falls back to a different encoder dependency if soundfile coverage proves incomplete. |
| A-PR-3 (*cycle 2*) | Each provider's `synthesize_chunks` can be inspected to determine which knobs it accepts (`temperature`, `top_p`, etc.) without invasive provider refactor. | FR-PR-09 soft-ignore matrix would need a provider-side declaration if reflection is too brittle. |
| A-PR-4 (*cycle 2*) | The `watchfiles` watcher primitive from S-011 generalizes to additional config files (presets.json) without rewriting it. | FR-PR-11 would need a separate watcher implementation otherwise. |

---

## 8. Open Questions

### Cycle 1 open questions (status as of cycle close)
| ID | Question | Blocks |
|---|---|---|
| OQ-1 | Is there an acceptable CPU-viable TTS provider already on the table for future work, or does CPU-only deployment remain unsupported indefinitely? | Roadmap prioritization (not this cycle). |
| OQ-2 | Should `GET /v1/voices` (FR-VM-05) be paginated or expose tags/categories for large voice maps? | FR-VM-05 schema final shape. |
| ~~OQ-3~~ | **RESOLVED:** voice CRUD with pluggable backends; multipart-only on create/update. See FR-VS. | — |
| OQ-4 | Coverage threshold 80% — applied to whole codebase from day one, or ratchet from current → 80%? | CI configuration step. |

### Cycle 2 open questions (all RESOLVED in BA challenge rounds — recorded for trace)
| ID | Question | Resolution |
|---|---|---|
| ~~CY2-OQ-1~~ | Denoise — in cycle, deferred, or feature-flagged? | **Feature-flagged via `[denoise]` extra.** See FR-PP-05. |
| ~~CY2-OQ-2~~ | Quality preset's default `response_format`? | **`flac`** (lossless compressed). See FR-FMT-05. |
| ~~CY2-OQ-3~~ | Per-provider format capability — how declared? | **`supported_response_formats: set[str]` on each provider.** See FR-FMT-02. |
| ~~CY2-OQ-4~~ | `presets.json` hot-reload semantics? | **`watchfiles` + polling fallback; in-flight snapshot at request-start.** See FR-PR-11. |
| ~~CY2-OQ-5~~ | `presets.json` schema validation approach? | **Pydantic `PresetConfig` model with `extra="forbid"`.** See FR-PR-02. |
| ~~CY2-OQ-6~~ | Per-preset perf SLO assertions? | **Soft documentation only; no hard SLO test gates.** Captured in NFR by writer. |
| ~~CY2-OQ-7~~ | Custom operator preset lifecycle in `/v1/models` / OpenAPI? | **Usable, NOT enumerated.** See FR-PR-12. |
| ~~CY2-OQ-8~~ | Backward compat for existing callers? | **Unchanged behavior; `balanced` preset is operator-tunable for exact byte-compat.** See A-PR-1. |
| ~~CY2-OQ-9~~ | Provider that can't honor a preset knob? | **Soft-ignore + `X-Preset-Ignored-Knobs` header.** See FR-PR-09. |
| ~~CY2-OQ-10~~ | Preset resolution layering? | **In `services/synthesize_service.py`; produces frozen `EffectiveSynthesisConfig`.** See FR-PR-06. |

---

## 9. Traceability Summary

| Functional area | Source (request section) |
|---|---|
| Hardware auto-detection (FR-HW) | §2 G2, §3 items 1–2 |
| Rich endpoint (FR-EP) | §2 G3 |
| OpenAI adapter (FR-OA) | §5 (constraints), §2 G3 |
| Voice CRUD & storage (FR-VS) | §2 G4, OQ-3 decision (post-FRS) |
| Voice seed ingestion (FR-VM) | §3 item 17, OQ-3 decision (post-FRS) |
| Concurrency (FR-CC) | §3 item 3, §11 pain points |
| Model cache (FR-CA) | §3 item 4, Round 1 |
| Health/readiness/lifecycle (FR-HL) | §3 items 5–10 |
| Observability (FR-OB) | §3 items 10–11, §4 NFR |
| Configuration (FR-CF) | §3 item 13, Round 2 |
| Error model (FR-ER) | §3 item 9, Round 2 |
| Quality gates (FR-QG) | §3 items 14–16 |
| Documentation (FR-DC) | §3 item 19, §6 success criteria |
| **Audio-generation presets (FR-PR)** *cycle 2* | `requests/dual-mode-presets-request.md` §2 G1-G3, D1-D5, D9-D10; BA Round 1 OQ-1..3, Round 2 OQ-4/5/7, Round 3 OQ-8/10 |
| **Audio post-processing (FR-PP)** *cycle 2* | `requests/dual-mode-presets-request.md` §2 G4, G6, D6, D8; BA Round 1 OQ-1, Round 3 OQ-9 |
| **Format extension (FR-FMT)** *cycle 2* | `requests/dual-mode-presets-request.md` §2 G5, D7; BA Round 1 OQ-2/3 |