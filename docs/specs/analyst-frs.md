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

---

## 8. Open Questions

| ID | Question | Blocks |
|---|---|---|
| OQ-1 | Is there an acceptable CPU-viable TTS provider already on the table for future work, or does CPU-only deployment remain unsupported indefinitely? | Roadmap prioritization (not this cycle). |
| OQ-2 | Should `GET /v1/voices` (FR-VM-05) be paginated or expose tags/categories for large voice maps? | FR-VM-05 schema final shape. |
| ~~OQ-3~~ | **RESOLVED:** voice CRUD with pluggable backends; multipart-only on create/update. See FR-VS. | — |
| OQ-4 | Coverage threshold 80% — applied to whole codebase from day one, or ratchet from current → 80%? | CI configuration step. |

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