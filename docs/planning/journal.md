# llm-tts-api — Story Journal

**Status:** Draft
**Date:** 2026-05-17
**Cycle:** Incremental parity with llm-image-api + selected features (NOT a rewrite)
**Sources:** `docs/specs/software-spec.md`, `docs/specs/analyst-frs.md`, `docs/specs/writer-nfr.md`, `docs/specs/analyst-UAT.md`
**Out of scope for this journal:** the SRS Roadmap (§11) and `improvement-analysis.md` — those feed future cycles.

---

## Overview

| Metric | Value |
|---|---|
| Cycle-1 stories | 25 (S-001..S-026, all DONE) |
| Cycle-2 stories | 10 (S-027..S-036, all Not started) |
| **Total** | **35** |
| User stories (cycle 1) | 9 |
| Technical stories (cycle 1 + cycle 2) | 26 |
| Cycle-1 parallel groups | 5 (A–E) |
| Cycle-2 parallel groups | 5 (F–J) |
| Critical path length (cycle 1) | 8 stories |
| Critical path length (cycle 2) | 5 stories |
| Functional areas covered | 16/16 (SRS §4.1–4.16) |

**Update note (post-OQ-3):** Voice enrollment was pulled into this cycle. The original S-014 (inline ref_audio on synthesize) is removed; replaced by **S-022..S-025** (voice repository abstraction + CRUD endpoints + optional backends). S-011 is re-scoped from "voice map hot-reload" to "voice seed ingestion". S-013 loses the `ref_audio` request field.

### Dependency overview

```
Group A (foundation, no deps)
   S-001 CI scaffolding (ratchet)
   S-002 Perf baseline capture
   S-003 Lifespan + app.state singletons
   S-004 Request-ID middleware + structured logging
   S-005 Hardware detection module

Group B (depends on A)
   S-006 Provider capability + auto-selection           ← S-003, S-005
   S-007 Async-correct concurrency model                ← S-003
   S-008 LRU model cache                                ← S-003
   S-009 Typed error taxonomy + envelope                ← S-004
   S-010 Health/Ready split + graceful drain            ← S-003, S-007
   S-012 Configuration inventory + env validation       ← (independent; uses A)
   S-022 Voice repository protocols + FS default        ← S-003, S-012
   S-023 Postgres metadata backend (optional extra)     ← S-022
   S-024 S3 blob backend (optional extra)               ← S-022

Group B' (depends on B core voice repo)
   S-025 Voice CRUD endpoints (/v1/tts/voices/*)        ← S-022, S-009
   S-011 Voice seed ingestion (re-scoped from hot-reload) ← S-022, S-025

Group C (depends on B/B')
   S-013 Rich endpoint POST /v1/tts/synthesize          ← S-006, S-007, S-008, S-009, S-011, S-012, S-025
   S-015 Streaming response with headers/trailers       ← S-013
   S-016 Client-disconnect cancellation                 ← S-013, S-007

Group D (depends on C)
   S-017 OpenAI adapter as thin translator              ← S-013
   S-018 Byte-identity paired UAT (rich vs OpenAI)      ← S-013, S-017

Group E (cross-cutting polish, late)
   S-019 Documentation refresh (README/diagrams/OpenAPI) ← all features stabilised
   S-020 Dockerfile + CI docker build update             ← S-012, S-010
   S-021 Performance validation against baseline         ← all features
   S-026 Code-duplication refactor (cycle-end cleanup)   ← S-019, S-020, S-021
```

---

## Stories

### Group A — Foundation (parallel-safe, no internal dependencies)

#### S-001: CI quality gate scaffolding
**Type:** Technical
**Status:** DONE (Sprint 1)
**Depends on:** None
**Parallel group:** A
**Refs:** FR-QG-01..04, NFR-MT-01..04, NFR-SE-05
**Description:** Wire ruff (check + format check), `mypy --strict src/`, `pytest --cov` with `--cov-fail-under`, and `pip-audit` into a CI workflow. Adopt a coverage ratchet per OQ-1: measure current coverage on day 1, set `--cov-fail-under` to that number, raise the floor with every PR that improves it (never lower). End-of-cycle target is 80%. Add `py.typed` to the package.
**Acceptance criteria:**
- CI workflow exists and runs all four gates on PRs and main.
- `ruff check` and `ruff format --check` pass on `src/` and `tests/`.
- `mypy --strict src/` passes (zero errors).
- Coverage threshold is set; final-cycle target is ≥80% (per OQ-1 decision).
- `pip-audit` runs and fails on configured severity threshold.
- `py.typed` marker is shipped with the package.

---

#### S-002: Baseline performance capture
**Type:** Technical
**Status:** DONE (Sprint 1; scaffolding done, numbers row pending operator)
**Depends on:** None
**Parallel group:** A
**Refs:** NFR-PF-01, A-7
**Description:** Per OQ-7, the perf baseline lives in-repo at `docs/perf/baseline.md`, updated per cycle. Measure current `/v1/audio/speech` p50/p95 latency on the reference Apple Silicon host for a representative input (e.g. ~500-char Italian text, voice `alloy`), and persist the numbers + methodology there. This is the gate S-021 will compare against.
**Acceptance criteria:**
- `docs/perf/baseline.md` exists with the input text, voice, host spec, methodology, p50/p95 numbers, and timestamp.
- Methodology is reproducible (commit SHA recorded; script or REST file referenced).
- Baseline file is referenced from README's Performance section (introduced in S-019).

---

#### S-003: Lifespan + app.state singletons
**Type:** Technical
**Status:** DONE (Sprint 1)
**Depends on:** None
**Parallel group:** A
**Refs:** FR-HL-03, NFR-OP-01, A-2, RISK-2
**Description:** Refactor service bootstrap to a FastAPI `lifespan` context manager that constructs settings, voice map, provider registry, model cache, semaphores, and request-id context as singletons hung off `app.state`. Replace ad-hoc `@lru_cache` factories that leak across tests. Disposal happens on shutdown. This is the foundational refactor everything else hangs on.
**Acceptance criteria:**
- Application uses `FastAPI(lifespan=...)`; no module-level singletons remain for the listed objects.
- `app.state.settings`, `app.state.voice_map`, `app.state.provider_registry`, `app.state.model_cache`, `app.state.queue_semaphore`, `app.state.concurrency_semaphore` are present after startup.
- Existing tests still pass (or are minimally updated to use dependency override / `app.state`).
- Test fixture in `conftest.py` uses `LLM_TTS_API_TEST_NO_LIFESPAN` (or equivalent) toggle to bypass real startup, mirroring llm-image-api.

---

#### S-004: Request-ID middleware + structured logging baseline
**Type:** Technical
**Status:** DONE (Sprint 1)
**Depends on:** None
**Parallel group:** A
**Refs:** FR-OB-01..02, NFR-OB-01..02, NFR-PV-02..03
**Description:** Add an ASGI middleware that assigns an `X-Request-ID` (taking from inbound header if present, otherwise generating a UUID), propagates it via `contextvars`, and emits it on every log line. Reconfigure logging with a consistent format and optional JSON output via `APP_LOG_FORMAT=json`. Log lines at INFO and above are payload-free (no input text, no audio bytes); DEBUG may include truncated text snippets (≤80 chars).
**Acceptance criteria:**
- Middleware sets and returns `X-Request-ID` on all responses.
- Log lines include `request_id` field whenever serving a request.
- `APP_LOG_FORMAT=json` produces one valid JSON object per log line.
- No payload text appears in INFO-or-higher log lines under unit-test inspection.
- Covered by UAT-OB-01..03.

---

#### S-005: Hardware detection module
**Type:** Technical
**Status:** DONE (Sprint 1)
**Depends on:** None
**Parallel group:** A
**Refs:** FR-HW-01..03, UAT-HW-01/03/06
**Description:** Introduce `engine/device.py` (or equivalent location under `src/llm_tts_api/`) with `detect_device()` and `detect_dtype()` mirroring the llm-image-api pattern: MPS → CUDA → CPU fallback, dtype derived per device. Honor env overrides `TTS_DEVICE` and `TTS_DTYPE`.
**Acceptance criteria:**
- `detect_device()` returns `mps|cuda|cpu` per the rule above.
- `detect_dtype()` defaults to `float16` on MPS/CUDA and `float32` on CPU.
- Env overrides take precedence and are validated.
- Unit tests with monkeypatched `torch.backends.mps.is_available` and `torch.cuda.is_available` cover all three branches (UAT-HW-01, UAT-HW-03).
- Module exposes a `DeviceProfile` dataclass consumed by S-006.

---

### Group B — Core services (depend on A)

#### S-006: Provider capability + auto-selection
**Type:** Technical
**Status:** DONE (Sprint 2)
**Depends on:** S-003, S-005
**Parallel group:** B
**Refs:** FR-HW-04..07, BR-2, BR-6, BR-7, A-1, RISK-1, UAT-HW-04..05
**Description:** Extend `TTSProviderStrategy` (or the provider registry) to declare a `supports_devices: set[Device]` capability. Implement auto-selection from `DeviceProfile`: pick the first registered provider supporting the detected device. `TTS_PROVIDER` becomes an override and is validated against device capability — incompatible combinations fail startup. CPU with no viable provider is a hard startup failure listing each rejected provider.
**Acceptance criteria:**
- All three current providers declare their `supports_devices` set.
- With env unset on Apple Silicon, `/health` reports the auto-selected provider.
- With `TTS_PROVIDER=vllm_omni` on Apple Silicon → startup fails with a clear error (UAT-HW-05).
- With `TTS_DEVICE=cpu` and no CPU-viable provider → startup fails with `provider_error.no_viable_provider` listing rejected providers (UAT-HW-04).
- Fallback to a hardcoded device→provider table is acceptable if RISK-1 materializes; either is covered.

---

#### S-007: Async-correct concurrency model
**Type:** Technical
**Status:** DONE (Sprint 2)
**Depends on:** S-003
**Parallel group:** B
**Refs:** FR-CC-01..04, NFR-PF-02, NFR-PF-04, NFR-SC-01..03, RISK-2
**Description:** Replace `threading.Semaphore` with `asyncio.Semaphore` for the concurrency ceiling. Add a separate queue-admission semaphore bounded by `TTS_MAX_QUEUE_DEPTH` (default 8) that returns `429 capacity_error.queue_full` on overflow. Per-(provider, model) `asyncio.Lock` serializes engine calls where required. Dispatch synchronous provider calls via `anyio.to_thread.run_sync` so the event loop stays responsive. **No rewrite of `SpeechSynthesizer`** — refactor in place; residual sync-wrapped sections are acceptable.
**Acceptance criteria:**
- `/health` responds in ≤50 ms p95 during in-flight synthesis (UAT-CC-02, NFR-PF-02).
- 4 parallel requests with `TTS_MAX_CONCURRENT_REQUESTS=2` complete in ~2× single-request wall-clock (UAT-CC-01).
- Excess requests beyond admission cap return `429 capacity_error.queue_full` (UAT-CC-03).
- No use of `threading.Semaphore` remains in the synthesis path (lint check or grep in CI).

---

#### S-008: LRU model cache
**Type:** Technical
**Status:** DONE (Sprint 2)
**Depends on:** S-003
**Parallel group:** B
**Refs:** FR-CA-01..04, NFR-SC-04, BR-3
**Description:** Introduce an LRU cache keyed by `(provider, model_id)` with size `TTS_MODEL_CACHE_SIZE` (default 1). Validate that the requested model_id is in the provider's allow-list AND file dependencies exist **before** evicting the current entry. Honor `TTS_PRELOAD_MODELS` (comma-separated `provider:model`) at startup, contributing to readiness gating. Eviction calls a provider `unload()` if available.
**Acceptance criteria:**
- Sequential requests `m1 → m2 → m1` cause 3 loads with cache size 1 (UAT-CA-01).
- Request with bogus model_id does NOT evict the current entry (UAT-CA-02).
- `TTS_PRELOAD_MODELS` populates the cache during startup; first synthesis incurs no load latency (UAT-CA-03).
- Memory footprint at default cache size + Voxtral-class model ≤60% of available RAM on 32 GB host (NFR-SC-04).

---

#### S-009: Typed error taxonomy + envelope
**Type:** Technical
**Status:** DONE (Sprint 2)
**Depends on:** S-004
**Parallel group:** B
**Refs:** FR-ER-01..04, NFR-SE-04, NFR-OB-03
**Description:** Implement the broad error categories (`validation_error`, `voice_error`, `provider_error`, `capacity_error`, `internal_error`) with documented sub-codes. Wrap all error paths in a single FastAPI exception handler that emits the OpenAI-compatible envelope `{ error: { type, code, message, param?, request_id } }`. Every error response sets `X-Request-ID` and `X-Error-Code` headers. `internal_error.unexpected_error` MUST NOT leak tracebacks, paths, or internal state to clients.
**Acceptance criteria:**
- All four envelope fields plus `request_id` present on every error response (UAT-ER-01).
- Unexpected exception with sensitive path text produces a generic message + traceback only in logs (UAT-ER-02).
- `X-Error-Code` matches `error.code` (UAT-OB-04).
- Error code inventory documented in code (`errors.py` or equivalent) and surfaced for README import in S-019.

---

#### S-010: Health/Ready split + graceful drain
**Type:** Technical
**Status:** DONE (Sprint 2)
**Depends on:** S-003, S-007
**Parallel group:** B
**Refs:** FR-HL-01/02/04/05, NFR-RL-01/05, NFR-OP-04
**Description:** Make `/health` lock-free, always 200, returning `device`, `dtype`, `provider`, `model_loaded`, `queue_depth`, `concurrent_active`, `version`. Make `/ready` reflect actual capability: 503 during warmup, drain, or invalid voice map; 200 only when fully serving. On SIGTERM, refuse admissions and drain in-flight up to `TTS_SHUTDOWN_DRAIN_SECONDS` (default 30) before exit. Optional psutil low-memory WARNING at startup.
**Acceptance criteria:**
- `/health` returns 200 during startup, synthesis, and drain (UAT-HL-01).
- `/ready` returns 503 during warmup, 200 post-warmup (UAT-HL-02).
- SIGTERM drains and exits 0 within drain budget (UAT-HL-03); forced exit when exceeded (UAT-HL-04).
- Low-memory warning emitted when threshold breached (UAT-HL-05).

---

#### S-011: Voice seed ingestion (legacy JSON → store)
**Type:** Technical
**Status:** DONE (Sprint 3)
**Depends on:** S-022, S-025
**Parallel group:** B'
**Refs:** FR-VM-01..05, NFR-OP-05, RISK-3
**Description:** Re-scoped per OQ-3. At every startup, if `TTS_VOICE_MAP_FILE` is set and exists, parse and validate it; for each entry whose `id` is NOT in the voice store, upsert a record with `source="seed"` (metadata via `VoiceMetadataRepository`, audio copied via `VoiceBlobRepository`). Existing entries left untouched (idempotent across restarts). Watch the file with `watchfiles`; on change, re-run ingestion with the same idempotent semantics. Ingestion is atomic per pass: any single-entry validation failure aborts the entire pass, leaves the store unchanged, logs `provider_error.voice_seed_ingest_failed`. Polling fallback if watchfiles unreliable in Docker (RISK-3). Missing or unset seed file is OK — service starts cleanly.
**Acceptance criteria:**
- Empty-store startup populates the store from the seed file (UAT-VM-01).
- Restart with existing CRUD voices leaves them untouched; new seeds added (UAT-VM-02).
- File change re-ingests within 2 s (UAT-VM-03, NFR-OP-05).
- Invalid edit leaves store unchanged; error logged (UAT-VM-04).
- Unset seed env var → service starts; empty `GET /v1/tts/voices` (UAT-VM-05).
- UAT-VM-03 also runs inside the container image in CI.

---

#### S-012: Configuration inventory + env validation
**Type:** Technical
**Status:** DONE (Sprint 2)
**Depends on:** None (but logical fit in Group B alongside other refactors)
**Parallel group:** B
**Refs:** FR-CF-01..03, NFR-OP-03, OQ-3 (multipart vs base64)
**Description:** Catalog all new env vars (`TTS_DEVICE`, `TTS_DTYPE`, `TTS_PROVIDER` as override, `TTS_MAX_INPUT_CHARS`, `TTS_MAX_CONCURRENT_REQUESTS`, `TTS_MAX_QUEUE_DEPTH`, `TTS_MODEL_CACHE_SIZE`, `TTS_PRELOAD_MODELS`, `TTS_VOICE_MAP_FILE`, `TTS_REFAUDIO_MAX_BYTES`, `TTS_SHUTDOWN_DRAIN_SECONDS`, `TTS_INFERENCE_TIMEOUT_SECONDS`, `TTS_MIN_FREE_MEMORY_GB`, `APP_LOG_FORMAT`). Extend `Settings` dataclass with `__post_init__` validation that fails fast on invalid values. `TTS_INFERENCE_TIMEOUT_SECONDS` is default-unset; when set to a positive number, `asyncio.wait_for` wraps synthesis and a breach returns 504 `capacity_error.timeout`.
**Acceptance criteria:**
- All env vars are parsed and validated; invalid value → startup exits non-zero with named-var message (UAT-CF-01).
- Default-unset timeout: 60 s synthesis succeeds (UAT-CF-02).
- Configured timeout = 2 s: 30 s synthesis is interrupted with 504 (UAT-CF-03).
- README inventory check in S-019 finds every new var documented (UAT-CF-04).

---

### Group B (voice storage) — Repository abstractions & CRUD

#### S-022: Voice repository protocols + default FS backends
**Type:** Technical
**Status:** DONE (Sprint 3)
**Depends on:** S-003, S-012
**Parallel group:** B
**Refs:** FR-VS-01..04, FR-VS-10..11, NFR-SE-03, NFR-ST-01, NFR-ST-03, NFR-PV-01, NFR-PV-05
**Description:** Define `VoiceMetadataRepository` and `VoiceBlobRepository` Protocols. Implement default backends `FsJsonMetadataRepository` and `FsBlobRepository` under `TTS_VOICE_STORE_DIR` (default `var/voices/`). Atomic writes via `tempfile` + `os.replace`; in-process `asyncio.Lock` on write paths; sandboxed id-derived paths (slug pattern `[a-z0-9_-]{1,64}`; reject `..`/`/`). Wire repositories into `app.state` via lifespan (S-003).
**Acceptance criteria:**
- Both Protocols defined with full CRUD operation surface (FR-VS-01/02).
- Default backends pass unit tests for create/get/list/update/delete + atomicity.
- Path-safety test: malformed ids never escape `TTS_VOICE_STORE_DIR` (UAT-VS-06).
- Concurrent reads + write: no corruption; serial write order.
- Base install (no extras) imports and runs default backends; no Postgres/S3 imports leaked into the default path (NFR-ST-01).

---

#### S-023: Postgres metadata backend (optional extra)
**Type:** Technical
**Status:** DONE (Sprint 3)
**Depends on:** S-022
**Parallel group:** B
**Refs:** FR-VS-01, NFR-ST-02
**Description:** Implement `PostgresMetadataRepository` behind the same Protocol. Add optional dependency group `[postgres]` to `pyproject.toml` (e.g. `psycopg[binary]` + minimal SQL or `sqlalchemy[asyncio]`). Selected when `TTS_VOICE_METADATA_BACKEND=postgres`; reads connection from `TTS_VOICE_METADATA_DSN`. Schema migration: idempotent `CREATE TABLE IF NOT EXISTS` at startup. Selecting `postgres` without the extra installed fails startup with `config_error.missing_extra`.
**Acceptance criteria:**
- `pip install .` does NOT install psycopg/sqlalchemy.
- `pip install .[postgres]` enables the backend.
- Same Protocol-level tests pass against a Postgres-backed instance (CI integration job using a service container, or marked `@pytest.mark.integration` and skipped without a DSN).
- Without the extra: startup with `TTS_VOICE_METADATA_BACKEND=postgres` fails with `config_error.missing_extra` (UAT-VS-12).

---

#### S-024: S3 blob backend (optional extra)
**Type:** Technical
**Status:** DONE (Sprint 3)
**Depends on:** S-022
**Parallel group:** B
**Refs:** FR-VS-02, NFR-ST-02
**Description:** Implement `S3BlobRepository` behind the same Protocol. Optional dependency group `[s3]` (e.g. `aiobotocore` or `boto3`). Selected when `TTS_VOICE_BLOB_BACKEND=s3`; reads `TTS_VOICE_BLOB_S3_ENDPOINT`, `TTS_VOICE_BLOB_S3_BUCKET`, `TTS_VOICE_BLOB_S3_REGION` + standard AWS env credentials. Idempotent bucket existence check at startup; clear error if bucket is missing or unreachable. Selecting `s3` without the extra → `config_error.missing_extra`.
**Acceptance criteria:**
- `pip install .` does NOT install boto3/aiobotocore.
- `pip install .[s3]` enables the backend.
- Protocol-level tests pass against MinIO or AWS S3 (integration job; `@pytest.mark.integration`).
- Without the extra: startup with `TTS_VOICE_BLOB_BACKEND=s3` fails with `config_error.missing_extra` (UAT-VS-12 variant).

---

#### S-025: Voice CRUD endpoints
**Type:** User
**Status:** DONE (Sprint 3)
**Depends on:** S-022, S-009
**Parallel group:** B'
**Refs:** FR-VS-04..09, FR-VS-12, NFR-SE-01..02, NFR-CP-01
**Description:** Implement REST CRUD under `/v1/tts/voices/*`: `POST /v1/tts/voices` (multipart: audio + metadata JSON), `GET /v1/tts/voices` (list, no audio), `GET /v1/tts/voices/{id}` (metadata only, never audio), `GET /v1/tts/voices/{id}/audio` (dedicated endpoint returning audio/wav body), `PUT /v1/tts/voices/{id}` (replace metadata + optionally blob, atomically), `DELETE /v1/tts/voices/{id}`. Note: `/v1/audio/voices/*` stays as 501 stub — reserved as future OpenAI-compat adapter (same pattern as `/v1/audio/speech` over `/v1/tts/synthesize`). Enforce `consent_acknowledged=true` at create. Validate audio per NFR-SE-01..02 (size + content-type + magic bytes). Use the in-`app.state` `VoiceMetadataRepository` and `VoiceBlobRepository` — never touches backends directly.
**Acceptance criteria:**
- Create succeeds with valid multipart payload (UAT-VS-01); returns `201`.
- Consent missing → `400 validation_error.consent_required` (UAT-VS-02).
- Duplicate id → `409 validation_error.voice_id_exists` (UAT-VS-03).
- Oversized / corrupt audio → `400 validation_error.ref_audio_invalid` (UAT-VS-04, UAT-VS-05).
- Path-traversal id → `400 validation_error` (UAT-VS-06).
- Metadata-only GET (UAT-VS-07) and audio-only GET (UAT-VS-08, plus blob-missing UAT-VS-08b); list contains no paths/URIs.
- PUT replaces metadata + blob atomically (UAT-VS-09).
- DELETE removes both metadata and blob (UAT-VS-10).

---

### Group C — Rich endpoint surface (depends on B, B')

#### S-013: Rich endpoint `POST /v1/tts/synthesize`
**Type:** User
**Status:** DONE (Sprint 4)
**Depends on:** S-006, S-007, S-008, S-009, S-011, S-012, S-025
**Parallel group:** C
**Refs:** FR-EP-01..05, NFR-MT-04, BR-1..4, BR-9, header inventory (SRS §5 C-2)
**Description:** Implement the new richer endpoint that becomes the source of truth for synthesis. Pydantic request model with `extra="forbid"`: `input`, `voice` (required, resolved against the voice store), `provider`, `model`, `response_format`, `stream`, `normalize_db`, `max_sentences_per_chunk`, `language`, `number_lang`, `temperature`, `top_p`. **No `ref_audio` field** — voices are managed exclusively via CRUD (S-025). Response is raw audio bytes with the full header inventory from SRS §5 C-2 (`X-Request-ID`, `X-Provider`, `X-Model`, `X-Device`, `X-Dtype`, `X-Voice-Source` ∈ `{seed, crud}`, `X-Voice-Id`, `X-Chunks`, `X-Total-Duration-Ms`).
**Acceptance criteria:**
- Synthesis with a CRUD-created voice returns 200 with full header set including `X-Voice-Source=crud` (UAT-EP-01, UAT-VS-11).
- Unknown field is rejected with 422 + `param` (UAT-EP-03).
- Input at limit succeeds; over limit returns `validation_error.input_too_long` (UAT-EP-04).
- Missing `voice` → `validation_error.voice_required` (UAT-EP-05).
- Unknown `voice` id → `voice_error.voice_not_found` (UAT-EP-06).
- Per-request overrides (`normalize_db`, chunking) take effect (UAT-EP-07).

---

#### S-014: ~~Inline ref_audio acceptance + validation~~ — **RETIRED**
**Status:** Retired (post-OQ-3 scope change).
**Replaced by:** S-022 (repository abstraction) + S-025 (voice CRUD). Inline upload is no longer accepted on `/v1/tts/synthesize`; voices are uploaded via the CRUD surface and referenced by id.

---

#### S-015: Streaming response with headers/trailers
**Type:** User
**Status:** DONE (Sprint 4)
**Depends on:** S-013
**Parallel group:** C
**Refs:** FR-EP-05, A-4, SRS §5 G-3 (trailer fallback)
**Description:** Implement `stream=true` on the rich endpoint. Use chunked transfer encoding; flush bytes per synthesized chunk (no full buffering). All `X-*` headers from FR-EP-04 set at response start. `X-Chunks` and `X-Total-Duration-Ms` emitted as response **trailers** when client advertises `TE: trailers` and uvicorn supports it; otherwise **omitted** (never faked, never block the stream waiting).
**Acceptance criteria:**
- Streamed response yields first audio byte before total duration / 2 (UAT-EP-02, NFR-PF-03).
- Trailers emitted when supported; omitted cleanly when not (per A-4).
- Streaming does not block the event loop (covered indirectly by NFR-PF-02 in S-007).

---

#### S-016: Client-disconnect cancellation
**Type:** Technical
**Status:** DONE (Sprint 4)
**Depends on:** S-013, S-007
**Parallel group:** C
**Refs:** FR-CC-05
**Description:** Detect client disconnection via FastAPI's `Request.is_disconnected()` (polled at chunk boundaries). On detection, stop further chunk synthesis at the next boundary. Already-allocated temp files cleaned (covered by S-014).
**Acceptance criteria:**
- Client drops connection at 1 s during a >5 s synthesis; further chunks stop at the next boundary; logs note the cancellation (UAT-CC-04).
- No orphan temp files remain after disconnection.

---

### Group D — OpenAI compatibility refactor (depends on C)

#### S-017: OpenAI adapter as thin translator
**Type:** User
**Status:** DONE (Sprint 5)
**Depends on:** S-013
**Parallel group:** D
**Refs:** FR-OA-01..04, NFR-PT-03, BR-9
**Description:** Refactor `POST /v1/audio/speech` to be a thin translator over `/v1/tts/synthesize`: map OpenAI fields to rich-endpoint fields, delegate to the rich service path, translate the response back to OpenAI shape. **No** direct calls into `SpeechSynthesizer` from this handler. Streaming via `client.audio.speech.with_streaming_response.create(...)` must work end-to-end. `GET /v1/models` lists the same provider/model pairs the rich endpoint accepts.
**Acceptance criteria:**
- OpenAI-shaped request works unchanged (UAT-OA-01).
- OpenAI SDK streaming works against the local service (UAT-OA-02).
- Code review check finds no bypass calls into the synthesizer; handler is <~30 LOC of translation (UAT-OA-03).
- `/v1/models` and rich-endpoint catalog match (UAT-OA-04).

---

#### S-018: Byte-identity paired UAT (rich vs OpenAI)
**Type:** Technical
**Status:** DONE (Sprint 5)
**Depends on:** S-013, S-017
**Parallel group:** D
**Refs:** NFR-PT-03b (SRS §5 Resolution G-1), RISK-8
**Description:** Implement a paired UAT (UAT-OA-05) that synthesizes the same effective request through both endpoints on a warm model and `sha256`-compares the audio bytes. If providers prove non-deterministic (RISK-8), relax the test to `±1 sample length + perceptual-hash threshold` per documented relaxation, and update SRS §5 G-1 + this story with the relaxation.
**Acceptance criteria:**
- Paired test exists and runs in CI.
- Byte-identity holds for at least one provider/model combo on warm load.
- If relaxation is applied, the relaxation threshold + rationale is recorded in `docs/perf/baseline.md` (or a sibling doc) and referenced from SRS §5.

---

### Group E — Cross-cutting polish (late, depends on features)

#### S-019: Documentation refresh
**Type:** Technical
**Status:** DONE (Sprint 6)
**Depends on:** S-006..S-017 (substance complete enough to document)
**Parallel group:** E
**Refs:** FR-DC-01..03, NFR-MT-06, NFR-CP-01, NFR-PV-04
**Description:** Refresh README, `docs/diagrams/`, and `docs/openapi/openapi.yaml`. README sections: Hardware Auto-Detection rules, full env-var inventory (matching S-012), Rich endpoint examples, **Voice-CRUD endpoints** under `/v1/tts/voices/*` (incl. consent attestation), seed-ingestion mechanism, storage-backend selection matrix (defaults vs `[postgres]`/`[s3]` extras), Error taxonomy table, **Voice biometric notice** (per NFR-CP-01/NFR-PV-04), Sizing recommendations (resolving SRS §5 C-1). Diagrams: startup, /v1/tts/synthesize (buffered & streamed), voice CRUD + seed ingestion flow. OpenAPI covers `/v1/tts/synthesize`, `/v1/tts/voices/*`, and `/v1/audio/speech`.
**Acceptance criteria:**
- README contains all required sections (UAT-DC-01); every new env var is documented (UAT-CF-04).
- Sequence diagrams reflect the new lifespan/singleton flow (UAT-DC-02).
- OpenAPI spec exercises both endpoints (UAT-DC-03).
- README has the biometric notice section verbatim per NFR-CP-01.

---

#### S-020: Dockerfile + CI docker build update
**Type:** Technical
**Status:** DONE (Sprint 6)
**Depends on:** S-012, S-010, S-011
**Parallel group:** E
**Refs:** NFR-OP-02, FR-QG-04
**Description:** Per OQ-5, ship **two image variants**: `Dockerfile` (default, CPU/MPS-friendly; runs on Linux x86_64 with CPU path) and `Dockerfile.cuda` (CUDA-enabled base image, CUDA torch wheel). Both: (a) reflect new env vars (S-012), (b) handle SIGTERM and drain (S-010), (c) expose `/health` + `/ready`, (d) mount `TTS_VOICE_STORE_DIR` and `TTS_VOICE_MAP_FILE` as volumes (configuration-by-volume), (e) builder stage isolates compiler toolchain, (f) final image runs as non-root, (g) pinned base image digest. CI builds both; smoke-tests that each built image starts and `/health` returns 200.
**Acceptance criteria:**
- `docker build -t llm-tts-api:ci .` succeeds in CI (UAT-QG-05).
- Built image starts and `/health` returns 200.
- Image runs as non-root; final stage contains no compiler toolchain.
- Voice map + ref audio readable from a mounted volume; container restart picks up changes.

---

#### S-021: Performance validation against baseline
**Type:** Technical
**Status:** DONE (Sprint 6)
**Depends on:** S-002, S-007, S-013, S-017 (feature surface stable)
**Parallel group:** E
**Refs:** NFR-PF-01..04, RISK-2
**Description:** Re-run the baseline scenario (from S-002) against the new code path (via `/v1/tts/synthesize` for the rich measurement; via `/v1/audio/speech` for the OpenAI-adapter measurement) and confirm ≤+10% regression on p50 and p95. Validate event-loop responsiveness (NFR-PF-02). Record post-cycle numbers in `docs/perf/baseline.md`.
**Acceptance criteria:**
- New-code p50 and p95 within +10% of baseline numbers.
- `/health` p95 ≤50 ms during in-flight synthesis (NFR-PF-02 / UAT-CC-02).
- Concurrent-throughput check (UAT-CC-01) passes within ±20%.
- Post-cycle numbers and date appended to `docs/perf/baseline.md`.

---

#### S-026: Code-duplication refactor (cycle-end cleanup)
**Type:** Technical
**Status:** DONE (Sprint 6)
**Depends on:** S-019, S-020, S-021 (all production code, docs, and validation complete first)
**Parallel group:** E (sequential terminal — runs AFTER the rest of Sprint 6)
**Refs:** NFR-MT-01..04, BR-9, NFR-PT-03
**Description:** End-of-cycle duplication sweep. With all production code, docs, and perf validation in place, identify and consolidate accidental duplication that accumulated during incremental sprints (1–6): repeated header inventories, parallel error-construction sites, voice-store path-validation copies, provider-allow-list scattering, test fixture duplication across routers, etc. **Constraint: behavior-preserving only.** Every test passing before this story must pass after, byte-for-byte where audio is involved (S-018 paired UAT is the gate). No new features, no API surface changes, no schema changes. The output is a smaller, more uniform codebase with the same observable behavior — measured by: (a) net LOC reduction in `src/llm_tts_api/`, (b) zero new mypy errors, (c) zero test changes other than removing tests that became redundant by construction (and only with explicit justification per removal), (d) S-018 byte-identity test continues to pass unchanged.
**Acceptance criteria:**
- Net production LOC reduction ≥ 3% vs pre-refactor master (measured `tokei` or `cloc` on `src/llm_tts_api/`).
- All gates green: ruff, ruff format, `mypy --strict src/`, `pytest`, `pip-audit`.
- S-018 byte-identity paired UAT passes unchanged.
- No new dependencies introduced.
- No public API or response-shape changes (`docs/openapi/openapi.yaml` byte-identical OR diff is purely cosmetic — comments, ordering — with explicit justification).
- Per-consolidation rationale recorded in implementation notes (what was duplicated, where it lives now, what behavior is preserved).

---

## Functional area → story coverage

| SRS area | Covered by |
|---|---|
| 4.1 Auto-detection | S-005, S-006 |
| 4.2 Rich endpoint | S-013, S-015 |
| 4.3 OpenAI adapter | S-017, S-018 |
| 4.4 Voice CRUD & storage | S-022, S-023, S-024, S-025, S-013 (resolution at synth time) |
| 4.5 Voice seed ingestion | S-011 |
| 4.6 Concurrency | S-007, S-016 |
| 4.7 Model cache | S-008 |
| 4.8 Lifecycle / health | S-003, S-010 |
| 4.9 Observability | S-004, S-009 (X-Error-Code) |
| 4.10 Configuration | S-012 |
| 4.11 Error model | S-009 |
| 4.12 Quality gates | S-001, S-020 |
| 4.13 Documentation | S-019 |

All 13 areas covered; no gaps.

## Highlights

- **Critical path** (cannot be shortened): S-003 → S-022 → S-025 → S-013 → S-017 → S-018 → S-021 (≈8 stories, all serial).
- **Foundation parallelism (Group A)**: S-001..S-005 can all start day one with no inter-dependency.
- **Most consequential single stories**: **S-022** (voice repository abstraction) now gates voice-related work; **S-013** (rich endpoint) still gates Groups C/D/E.
- **Highest-risk stories** mapped to risks:
  - S-007 → RISK-2 (async refactor scope vs "no rewrite")
  - S-011 → RISK-3 (watchfiles in Docker)
  - S-025 → RISK-4 (DoS via voice uploads — now applies at CRUD layer)
  - S-018 → RISK-8 (provider non-determinism)
  - S-023 / S-024 → optional-extras testing matrix risk (need integration jobs for Postgres/S3)
- **All previous OQs resolved.** Decisions:
  - OQ-1: ratchet 80% (S-001).
  - OQ-3: voice CRUD with pluggable backends in-cycle (S-022..S-025).
  - OQ-5: two Docker variants — default + CUDA (S-020).
  - OQ-7: `docs/perf/baseline.md` in-repo (S-002, S-021).

## Next step (cycle 1)

Cycle 1 closed — all 25 stories DONE across Sprints 1–6. Master at 380 tests passing, mypy --strict clean across 52 source files. See `docs/planning/sprint-log.md` for cycle-1 sprint dispositions.

---

# Cycle 2 — Dual-mode audio presets

**Status:** Draft
**Date:** 2026-05-19
**Cycle:** Dual-mode audio generation (named presets `fast`/`balanced`/`quality` + post-processing pipeline + response_format extension)
**Sources:** `docs/specs/software-spec.md` §4.14-4.16, `docs/specs/analyst-frs.md` §4.14-4.16 (FR-PR/PP/FMT), `docs/specs/writer-nfr.md` §11b, `docs/specs/requests/dual-mode-presets-request.md`

## Cycle-2 overview

| Metric | Value |
|---|---|
| New stories | 10 (S-027..S-036) |
| User stories | 0 |
| Technical stories | 10 |
| Parallel groups | 5 (F–J) |
| Critical path length | 5 stories (F → G → H → I → J) |
| Functional areas covered | 3/3 (SRS §4.14, §4.15, §4.16) |

### Cycle-2 dependency overview

```
Group F (cycle-2 foundation, no internal deps)
   S-027 Presets config + Pydantic schema + startup validation

Group G (depends on F)
   S-028 Preset resolution + EffectiveSynthesisConfig            ← S-027
   S-029 Preset hot-reload + in-flight snapshot                  ← S-027

Group H (depends on G; all parallel)
   S-030 Custom-preset isolation (OpenAPI/v1/models lifecycle)   ← S-028
   S-031 Post-processing service-layer module                    ← S-028
   S-033 Format extension (wav24/flac + per-provider capability) ← S-028

Group I (depends on H)
   S-032 Quality-stream downgrade                                ← S-031
   S-034 Observability log + cycle-2 response headers            ← S-028, S-031, S-033

Group J (terminal — depends on all cycle-2 production code)
   S-035 S-018 byte-identity regression gate + tamper assertion  ← all above
   S-036 Cycle-2 docs refresh (README + diagrams + OpenAPI)      ← all above
```

## Cycle-2 Stories

### Group F — Cycle-2 foundation (no internal deps)

#### S-027: Presets configuration foundation
**Type:** Technical
**Status:** Not started
**Depends on:** None (cycle-1 S-003 lifespan + S-012 Settings are DONE)
**Parallel group:** F
**Refs:** FR-PR-01, FR-PR-02, FR-PR-05, FR-PR-13, NFR-SE-09, NFR-PR-02
**Description:** Introduce `config/presets.json` and the new `PresetConfig` Pydantic model (`extra="forbid"`) at the package boundary. Ship three built-in presets `fast`/`balanced`/`quality` per the cycle-2 SRS §4.14. Wire startup validation into the lifespan: parse `presets.json`, validate the schema, validate the file-permission posture (NFR-SE-09: owned-by-service-user + not world-writable), validate the `TTS_DEFAULT_PRESET` env var resolves to a defined preset name, validate every preset-pinned `(provider, model)` is in the corresponding provider's allow-list. Each validation failure exits non-zero with the corresponding `config_error.*` code from cycle-2 §4.16. The loaded registry hangs off `app.state.preset_registry`.
**Acceptance criteria:**
- `config/presets.json` exists with the three built-in presets (defaults derived from cycle-2 SRS §4.14, the cycle-1 baseline, and the per-preset response_format from FR-FMT-05).
- `PresetConfig` Pydantic model rejects unknown fields with a clear field-path message.
- Startup wires `app.state.preset_registry` from the validated file (or exits with `config_error.presets_invalid`).
- Startup refuses to start when `presets.json` is world-writable or owner-mismatched (`config_error.presets_unsafe_permissions`).
- Startup refuses to start when `TTS_DEFAULT_PRESET` names an unknown preset.
- Startup refuses to start when any preset pins a `(provider, model)` outside the provider allow-lists (`config_error.preset_provider_invalid`).
- Resolution overhead from the loaded registry is ≤1 ms p95 (NFR-PR-02).
- Covered by UAT-PR-11, UAT-PR-12, UAT-PR-13, UAT-PR-14.

---

### Group G — Resolution & lifecycle (depend on F)

#### S-028: Preset resolution + EffectiveSynthesisConfig
**Type:** Technical
**Status:** Not started
**Depends on:** S-027
**Parallel group:** G
**Refs:** FR-PR-04, FR-PR-06, FR-PR-07, FR-PR-08, FR-PR-09, FR-PR-10, BR-10, BR-12, BR-17
**Description:** Add `preset: str | None` to `SynthesizeRequest` (open string at Pydantic, validated against the loaded registry by the resolver — see FR-PR-12 / S-030). Implement the resolver in `services/synthesize_service.py` producing a frozen `EffectiveSynthesisConfig` dataclass consumed downstream by all synthesis paths. Precedence per BR-10: explicit field > preset defaults > Settings / VoiceRecord defaults. Unknown preset name ⇒ `400 validation_error.preset_unknown` listing available preset names. Conflict between explicit field and preset pin ⇒ explicit wins + WARN log + `X-Preset-Effective` header populated. Provider-incompatible knobs soft-ignored + `X-Preset-Ignored-Knobs` header. `POST /v1/audio/speech` (OpenAI adapter) ignores any body/query `preset` and always resolves to `TTS_DEFAULT_PRESET` (BR-12 — preserves S-018 byte-identity).
**Acceptance criteria:**
- `SynthesizeRequest.preset` is `str | None`; not `Literal[...]` (per FR-PR-04 — accommodates operator-defined presets).
- Unknown preset returns 400 `validation_error.preset_unknown` with the available preset names in the message.
- Explicit field overrides preset pin; WARN log records the override with request_id; `X-Preset-Effective` shows resolved values.
- Provider-incompatible knobs are soft-ignored; `X-Preset-Ignored-Knobs` lists them.
- OpenAI adapter ignores body/query preset; always applies `TTS_DEFAULT_PRESET`.
- Covered by UAT-PR-01..07.

---

#### S-029: Preset hot-reload + in-flight snapshot
**Type:** Technical
**Status:** Not started
**Depends on:** S-027 (NOT S-028 — registry primitive is orthogonal to resolution code)
**Parallel group:** G
**Refs:** FR-PR-11, NFR-SE-10, NFR-PR-03, NFR-PR-04, BR-11, RISK-3, RISK-PR-3
**Description:** Reuse the `watchfiles` + polling-fallback primitive from cycle-1 S-011 (voice-map ingestion) to watch `config/presets.json`. On change notification: parse + validate the new file against `PresetConfig`; **only if validation succeeds** atomically swap `app.state.preset_registry`; on validation failure, log WARN with field-path and keep the prior good registry live (NFR-SE-10 attack-tolerant). In-flight requests MUST snapshot the registry at request-start and use that snapshot to completion (NFR-PR-04 — captured via a request-scoped attribute set in the middleware or resolver entry point). Reload latency ≤ 2 s (NFR-PR-03), matching cycle-1 voice-map reload SLO.
**Acceptance criteria:**
- Watcher fires on `presets.json` mtime change within ≤ 2 s on Linux/macOS; polling fallback used in Docker per RISK-3.
- Valid new file is atomically swapped into `app.state.preset_registry`.
- Invalid new file is rejected with WARN log; prior registry stays live; service continues serving.
- In-flight requests use the registry snapshot taken at request-start.
- Covered by UAT-PR-08, UAT-PR-09, UAT-PR-15.

---

### Group H — Consumers of resolution (depend on G; all parallel)

#### S-030: Custom-preset isolation (OpenAPI / `/v1/models` lifecycle)
**Type:** Technical
**Status:** Not started
**Depends on:** S-028
**Parallel group:** H
**Refs:** FR-PR-12
**Description:** Codify the custom-preset isolation contract: operator-added presets in `config/presets.json` are usable on `POST /v1/tts/synthesize` (resolver accepts any name in the loaded registry) BUT are NOT enumerated in `/v1/models` and NOT in `docs/openapi/openapi.yaml`. The OpenAPI `preset` field remains `type: string` with the three built-ins as informational examples; custom names never appear. The S-019 `tests/test_docs_inventory.py` is extended to assert that `/v1/models` response body contains no preset names (defense in depth) and OpenAPI `preset` field type is `string`, not `Literal`. Includes a test that adds a custom preset and confirms it works on `/v1/tts/synthesize` AND is invisible to `/v1/models` + OpenAPI.
**Acceptance criteria:**
- A custom preset (e.g. `cinematic`) added to `presets.json` is usable on `/v1/tts/synthesize` after reload.
- The same preset name does not appear in `/v1/models` response body anywhere.
- `docs/openapi/openapi.yaml` `preset` field is open-string type.
- Extended docs-inventory test pins both invariants.
- Covered by UAT-PR-10.

---

#### S-031: Post-processing service-layer module
**Type:** Technical
**Status:** Not started
**Depends on:** S-028
**Parallel group:** H
**Refs:** FR-PP-01..06, FR-PP-08, NFR-PP-01, NFR-PP-02, NFR-CP-03, BR-14
**Description:** Create `src/llm_tts_api/services/audio_postprocess.py` exposing a pure-function pipeline `postprocess_audio(audio: bytes, *, rms_normalize: bool, silence_trim: bool, denoise: bool, settings: Settings) -> bytes`. Pipeline order (codified in module docstring): **denoise → silence_trim → rms_normalize**. Each step is a no-op when its flag is false. `rms_normalize` uses `settings.tts_normalize_db_default` (or the resolved request value) as the target dBFS. `silence_trim` uses `settings.tts_silence_trim_threshold_db` (default `-50 dBFS`) and preserves a 50 ms head/tail pad. `denoise` is feature-flagged: optional dep extra `[denoise]` adds `noisereduce` (or equivalent); when the extra is not installed and `denoise=true` is resolved, the step logs WARN and no-ops. The post-processing step runs after provider chunk assembly and before format conversion (FR-PP-08), in `services/synthesize_service.py::synthesize_core`. Buffer is request-scoped (NFR-PP-02 — no module-level retention, no logging of audio bytes). When any step runs, response header `X-Postprocess-Applied` lists the applied steps; absent when none.
**Acceptance criteria:**
- Module exists; pipeline order documented in module docstring + verifiable by code inspection.
- `rms_normalize` produces decoded audio within ±0.5 dB of target dBFS on a deterministic fixture.
- `silence_trim` removes ≥ leading silence preserving 50 ms pad; symmetric for trailing silence.
- `denoise=true` without `[denoise]` extra logs WARN, no-ops, and `X-Postprocess-Applied` excludes `denoise`.
- `X-Postprocess-Applied` absent when no step runs; lists applied steps otherwise.
- Buffer is request-scoped (verified via code inspection — no module-level state).
- Covered by UAT-PP-01, UAT-PP-02, UAT-PP-03, UAT-PP-04, UAT-PP-05, UAT-PP-07.

---

#### S-033: Format extension (wav24/flac + per-provider capability)
**Type:** Technical
**Status:** Not started
**Depends on:** S-028
**Parallel group:** H
**Refs:** FR-FMT-01..07, NFR-FMT-01..03, NFR-PT-06, BR-15
**Description:** Extend `SynthesizeRequest.response_format` from `Literal["wav"]` to `Literal["wav", "wav24", "flac"]`. Add `supported_response_formats: set[Literal["wav","wav24","flac"]]` capability to each `TTSProviderStrategy` (mlx_audio, voxtral, vllm_omni — measured day-one, not assumed). At request time: if active provider doesn't support the resolved `response_format`, return `400 validation_error.format_unsupported` with the supported set in the message. At startup: validate every preset-pinned `response_format` is in the auto-selected provider's `supported_response_formats`; otherwise `config_error.preset_provider_invalid`. Format conversion lives in the service layer (`synthesize_service.py`), runs AFTER post-processing AND AFTER provider chunk assembly, using `soundfile` (existing dep) for `wav24` (`subtype="PCM_24"`) and `flac`. Response `Content-Type` matches the resolved format: `audio/wav` for `wav`/`wav24`, `audio/flac` for `flac`. The `quality` preset's default `response_format` is `flac` (FR-FMT-05); fast/balanced stay at `wav`.
**Acceptance criteria:**
- Each provider declares a non-empty `supported_response_formats` set (mypy-strict-required).
- `wav` (16-bit) still passes existing tests (no regression).
- `wav24` produces decodable 24-bit PCM via `soundfile.read(...)` on a supporting provider.
- `flac` produces decodable lossless audio whose decoded samples equal the wav reference within tolerance.
- Unsupported format on active provider returns 400 `validation_error.format_unsupported` listing the supported set.
- Preset pinning an unsupported format at startup ⇒ process exits with `config_error.preset_provider_invalid`.
- `Content-Type` correct per format.
- Covered by UAT-FMT-01..06.

---

### Group I — Composition (depend on H)

#### S-032: Quality-preset streaming downgrade
**Type:** Technical
**Status:** Not started
**Depends on:** S-031
**Parallel group:** I
**Refs:** FR-PP-07, NFR-PP-03, BR-13
**Description:** In `services/synthesize_service.py::synthesize_core`, when the resolved `EffectiveSynthesisConfig` has `preset.name == "quality"` AND the request's `stream=true`, silently downgrade to buffered mode: bypass the trailer/streaming branch, run the full post-processing pipeline from S-031, and return a non-streaming response. Set response header `X-Stream-Downgraded: quality-postproc` to make the downgrade observable (NOT an error; documented behavior). No streaming trailers emitted on the downgrade path.
**Acceptance criteria:**
- `preset=quality` + `stream=true` request returns a buffered (non-chunked-transfer) response.
- `X-Stream-Downgraded: quality-postproc` is set on the response.
- `X-Postprocess-Applied` is populated (per S-031).
- No streaming trailers emitted on the downgrade path.
- Covered by UAT-PP-06.

---

#### S-034: Observability log + cycle-2 response headers
**Type:** Technical
**Status:** Not started
**Depends on:** S-028, S-031, S-033
**Parallel group:** I
**Refs:** NFR-OP-06
**Description:** Wire the per-synthesis INFO log line that captures: `request_id`, `resolved_preset`, `ignored_knobs` (comma-separated, possibly empty), `postprocess_applied` (comma-separated, possibly empty), `response_format`, `stream_downgraded` (boolean). Log line is payload-free per NFR-PV-02 (no `input` text, no audio bytes). Emitted at INFO level so operators can `grep resolved_preset=quality` for triage. Ensure header emission is centralized in `synthesize_service.py` so both rich and OpenAI paths funnel through one writer (OpenAI path then strips `X-Preset-*` etc. per cycle-1 S-017's `_RICH_ONLY_HEADERS`).
**Acceptance criteria:**
- One INFO-level log line per synthesis request carrying the six required fields.
- Log line is payload-free.
- `X-Preset-Effective`, `X-Preset-Ignored-Knobs`, `X-Postprocess-Applied` headers populated on rich path.
- OpenAI path continues to strip rich-only headers (cycle-1 contract preserved).
- Covered by UAT-PR-16 + indirectly by UAT-PR-04, UAT-PR-05, UAT-PP-05.

---

### Group J — Cycle-2 close-out (terminal — depend on all production code)

#### S-035: S-018 byte-identity regression gate + tamper assertion
**Type:** Technical
**Status:** Not started
**Depends on:** S-027, S-028, S-029, S-030, S-031, S-032, S-033, S-034 (all cycle-2 code complete)
**Parallel group:** J
**Refs:** NFR-PT-05, NFR-OP-07, UAT-PR-17, RISK-PR-5
**Description:** Codify the S-018 byte-identity invariant as a cycle-2 regression gate. Run the existing `tests/test_openai_adapter_parity.py` (the S-018 paired UAT, untouched since cycle 1) against post-cycle-2 master and assert: (a) all paired tests pass — rich(`preset=balanced`, no overrides) body bytes sha256-equal OpenAI-path body bytes for the same effective request; (b) the test file itself is byte-identical to its cycle-1 form (`git diff master tests/test_openai_adapter_parity.py` empty). Adds a new tamper-detection test (`tests/test_cycle2_byte_identity_gate.py` or extension to `test_docs_inventory.py`) that asserts `tests/test_openai_adapter_parity.py` SHA256 is stable. No new code in `src/`; this is a gate, not a feature.
**Acceptance criteria:**
- `uv run pytest tests/test_openai_adapter_parity.py -v` passes byte-identically.
- `git diff master tests/test_openai_adapter_parity.py` is empty.
- Tamper-detection test exists and pins the sha256 of `tests/test_openai_adapter_parity.py`.
- Covered by UAT-PR-17.

---

#### S-036: Cycle-2 documentation refresh
**Type:** Technical
**Status:** Not started
**Depends on:** S-027, S-028, S-029, S-030, S-031, S-032, S-033, S-034 (docs reflect master)
**Parallel group:** J
**Refs:** FR-DC convention (cycle-1 S-019 pattern), all cycle-2 NFRs that warrant operator documentation
**Description:** Refresh README, `docs/diagrams/`, and `docs/openapi/openapi.yaml` for cycle 2. README: new "Presets" section (3 built-ins + how to add custom + table of preset defaults), "Post-processing" section (3 steps + denoise extra), "Response formats" section (wav/wav24/flac + per-provider capability matrix), updated env-var inventory (`TTS_DEFAULT_PRESET`, `TTS_SILENCE_TRIM_THRESHOLD_DB`, etc. from S-027/S-031), new error codes table (`validation_error.preset_unknown`, `validation_error.format_unsupported`, `config_error.presets_invalid`, `config_error.preset_provider_invalid`, `config_error.presets_unsafe_permissions`), updated header inventory (X-Preset-Effective / X-Preset-Ignored-Knobs / X-Postprocess-Applied / X-Stream-Downgraded), per-preset TTFB targets per NFR-PR-01 (with "measured on M1 Max; YMMV" disclaimer). Diagrams: new sequence diagram `preset-resolution.md` (request → resolver → EffectiveSynthesisConfig → downstream), new sequence diagram `quality-postproc.md` (postproc pipeline order + stream downgrade), updated `synthesize-rich.md` to show postproc + format conversion steps. OpenAPI: `preset` field added as open-string with built-ins as examples; `response_format` enum extended; new error responses for the 5 new codes; new headers documented. Extends `tests/test_docs_inventory.py` to assert every new env var, every new error code, every new header appears in README.
**Acceptance criteria:**
- README has the new sections per the description; every new env var documented; every new error code in the taxonomy table; every new header listed.
- New + updated diagrams committed under `docs/diagrams/sequence/`.
- OpenAPI spec includes `preset` field + extended `response_format` enum + new error responses + new headers.
- `tests/test_docs_inventory.py` extended; passes.
- Covered by extending cycle-1 UAT-DC-01..03 + cycle-2 UAT-PR-10 + manual doc review.

---

## Cycle-2 functional-area → story coverage

| SRS area | Covered by |
|---|---|
| 4.14 Audio-generation presets | S-027 (foundation), S-028 (resolution), S-029 (hot-reload), S-030 (custom isolation), S-034 (headers/logs) |
| 4.15 Audio post-processing | S-031 (module + pipeline), S-032 (quality stream downgrade) |
| 4.16 Response format extension | S-033 |
| Documentation | S-036 |
| Byte-identity gate | S-035 |

All 3 cycle-2 functional areas covered; 17 cycle-2 NFRs all hooked to at least one story; 30 cycle-2 UAT cases all traced to a story.

## Cycle-2 Highlights

- **Critical path** (cycle-2): S-027 → S-028 → {S-031 | S-033} → S-034 → S-035/S-036 (≈5 stories serial; everything else parallels at Groups G/H/I).
- **Foundation gate**: S-027 must land first — everything else reads `app.state.preset_registry`.
- **Most consequential single stories**:
  - **S-028** (resolution + EffectiveSynthesisConfig) — single resolution site; centralization is what keeps NFR-PT-05 (S-018 byte-identity) holding.
  - **S-035** (byte-identity gate) — the load-bearing regression gate of the whole cycle.
- **Highest-risk stories mapped to risks**:
  - S-029 → RISK-3 (watchfiles in Docker — same mitigation as cycle-1 S-011)
  - S-031 → RISK-PR-4 (postproc overhead exceeds 200ms/s denoise budget on low-spec hosts)
  - S-033 → RISK-PR-2 (per-provider format capability declared by inspection vs measurement)
  - S-035 → RISK-PR-5 (S-018 byte-identity breaks subtly under preset resolution drift)
- **All cycle-2 OQs resolved** (see SRS §10). 10 PO decisions D1-D10 + 10 BA/TW round resolutions all traced through.

## Cycle-2 Next step

Run the `sprint-planner` skill against the cycle-2 stories. A natural first cycle-2 sprint is Group F + Group G in full (4 stories: S-027 alone, then S-028 + S-029 in parallel). A natural second sprint covers Group H (S-030 + S-031 + S-033 in parallel). A natural third sprint closes with Groups I + J (S-032, S-034, S-035, S-036).