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
| Total stories | 24 |
| User stories | 9 |
| Technical stories | 15 |
| Parallel groups | 5 (A–E) |
| Critical path length | 8 stories (A → B → B' → C → D → E) |
| Functional areas covered | 13/13 (SRS §4.1–4.13) |

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

## Next step

Run the `sprint-planner` skill against this journal to break it into sprint increments. A natural first sprint is Group A in full (foundation, no deps); a natural second sprint is Group B; etc. — but the sprint planner is the right tool to finalize that.