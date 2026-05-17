# llm-tts-api — User Acceptance Test Cases

**Status:** Draft
**Date:** 2026-05-17
**Companion to:** `analyst-frs.md`

ID convention: `UAT-<area>-NN` where `<area>` matches the FR area (HW, EP, OA, VC, VM, CC, CA, HL, OB, CF, ER, QG, DC).

Each test case includes: Preconditions, Steps, Expected, FR trace, Type (Happy / Negative / Boundary / Recovery).

Test environment assumptions: Apple Silicon dev box for primary path; CUDA host for FR-HW CUDA-path tests; CPU-only container for fallback tests. Unit-level CUDA/CPU tests use monkeypatched torch availability (mirrors `llm-image-api/tests/test_device.py`).

---

## UAT-HW — Hardware & Provider Auto-Detection

### UAT-HW-01 (Happy) — MPS auto-selected on Apple Silicon
**Preconditions:** Apple Silicon host; `TTS_DEVICE` and `TTS_PROVIDER` unset.
**Steps:** Start the service. Read `GET /health`.
**Expected:** `device="mps"`, `dtype="float16"`, `provider` is the first MPS-capable provider in registry (e.g. `mlx_audio`). Source `auto`.
**Trace:** FR-HW-01, FR-HW-03, FR-HW-04.

### UAT-HW-02 (Happy) — Env override forces device
**Preconditions:** Apple Silicon host; `TTS_DEVICE=cpu`.
**Steps:** Start the service.
**Expected:** Startup either succeeds with `device="cpu"` (if a CPU-capable provider is registered) OR fails per UAT-HW-04 (none registered).
**Trace:** FR-HW-02, FR-HW-05.

### UAT-HW-03 (Happy, monkeypatched) — CUDA detection
**Preconditions:** Unit test with `torch.cuda.is_available()` monkeypatched to `True`, MPS to `False`.
**Steps:** Invoke `detect_device()`.
**Expected:** Returns `cuda`; dtype `float16`.
**Trace:** FR-HW-01.

### UAT-HW-04 (Negative) — No viable CPU provider → startup fails
**Preconditions:** Host where only CUDA-only and MPS-only providers are registered; `TTS_DEVICE=cpu`.
**Steps:** Start the service.
**Expected:** Process exits non-zero. Log contains `provider_error.no_viable_provider` and lists each rejected provider with its reason.
**Trace:** FR-HW-05.

### UAT-HW-05 (Negative) — Incompatible explicit provider on detected device
**Preconditions:** Apple Silicon host; `TTS_PROVIDER=vllm_omni`.
**Steps:** Start the service.
**Expected:** Startup fails with `validation_error` style log; process exits non-zero.
**Trace:** FR-HW-06.

### UAT-HW-06 (Boundary) — `TTS_DTYPE=bfloat16` env override
**Preconditions:** Apple Silicon host; `TTS_DTYPE=bfloat16`.
**Steps:** Start; check `GET /health`.
**Expected:** `dtype="bfloat16"`, source `env`.
**Trace:** FR-HW-03.

---

## UAT-EP — Rich Endpoint /v1/tts/synthesize

### UAT-EP-01 (Happy) — Basic synthesis with map voice
**Preconditions:** Service warm; voice `alloy` in map.
**Steps:** `POST /v1/tts/synthesize` body `{ "input": "Ciao mondo.", "voice": "alloy" }`.
**Expected:** `200`, `Content-Type: audio/wav`, body is parseable WAV. Headers include `X-Request-ID`, `X-Provider`, `X-Model`, `X-Device`, `X-Dtype`, `X-Chunks≥1`, `X-Total-Duration-Ms>0`, `X-Voice-Source=map`, `X-Voice-Id=alloy`.
**Trace:** FR-EP-01, FR-EP-02, FR-EP-04, FR-VC-01.

### UAT-EP-02 (Happy) — Streaming response
**Preconditions:** Same as UAT-EP-01.
**Steps:** `POST /v1/tts/synthesize` with `"stream": true` on a multi-sentence input.
**Expected:** `200`, `Transfer-Encoding: chunked`. Bytes arrive incrementally (first byte well before total duration). Headers FR-EP-04 set at response start; `X-Chunks`/`X-Total-Duration-Ms` either as trailers or omitted.
**Trace:** FR-EP-05.

### UAT-EP-03 (Negative) — `extra="forbid"` rejects unknown fields
**Steps:** `POST /v1/tts/synthesize` body `{ "input": "x", "voice": "alloy", "made_up_field": 1 }`.
**Expected:** `422`, envelope with `type="validation_error"`, `param="made_up_field"`.
**Trace:** FR-EP-02, FR-QG-02, FR-ER-02.

### UAT-EP-04 (Boundary) — Input length at limit
**Preconditions:** `TTS_MAX_INPUT_CHARS=256`.
**Steps:** POST with input of exactly 256 chars; then 257 chars.
**Expected:** 256 → `200`. 257 → `400` with `validation_error.input_too_long`.
**Trace:** FR-EP-02, FR-ER-02.

### UAT-EP-05 (Negative) — Voice missing
**Steps:** POST without `voice`.
**Expected:** `400` with `validation_error.voice_required`.
**Trace:** FR-EP-03.

### UAT-EP-06 (Negative) — Voice not in store
**Steps:** POST with `voice="does-not-exist"`.
**Expected:** `404` with `voice_error.voice_not_found`.
**Trace:** FR-EP-03, FR-VS-10.

### UAT-EP-07 (Happy) — Per-request overrides
**Steps:** POST with `normalize_db=-18`, `max_sentences_per_chunk=1`, `temperature=0.7`.
**Expected:** `200`; audio differs measurably from default (e.g. different chunk count in `X-Chunks`).
**Trace:** FR-EP-02.

---

## UAT-OA — OpenAI Adapter /v1/audio/speech

### UAT-OA-01 (Happy) — OpenAI-shaped request still works
**Steps:** POST `/v1/audio/speech` `{ "model": "qwen3-tts", "input": "Hi", "voice": "alloy", "response_format": "wav" }`.
**Expected:** `200`, audio body. Behavior matches a `/v1/tts/synthesize` call with equivalent fields.
**Trace:** FR-OA-01.

### UAT-OA-02 (Happy) — SDK streaming
**Preconditions:** Python `openai` SDK installed.
**Steps:** `with client.audio.speech.with_streaming_response.create(...)` against the local service.
**Expected:** Byte stream is consumed successfully and matches a non-streamed call's audio (byte-identical or perceptually equivalent depending on encoder determinism).
**Trace:** FR-OA-03.

### UAT-OA-03 (Code-review check) — No duplicated business logic
**Steps:** Static check: handler for `/v1/audio/speech` calls into the rich-endpoint service path; grep for direct `SpeechSynthesizer.synthesize(...)` calls outside the rich service.
**Expected:** No bypass call sites; handler is < ~30 LOC of translation.
**Trace:** FR-OA-02.

### UAT-OA-04 (Happy) — `/v1/models` lists matching catalog
**Steps:** Compare `/v1/models` list to the providers/models the rich endpoint accepts.
**Expected:** Exact match (or `/v1/models` is a strict subset with a documented reason).
**Trace:** FR-OA-04.

---

## UAT-VS — Voice CRUD & Pluggable Storage

### UAT-VS-01 (Happy) — Create voice via multipart upload
**Preconditions:** Service warm; default `fs_json` + `fs` backends.
**Steps:** `POST /v1/tts/voices` multipart with `audio` part (valid 2-sec WAV) and `metadata` JSON `{ "id": "myvoice", "transcript": "...", "language": "Italian", "consent_acknowledged": true }`.
**Expected:** `201` with the created record (no path/URI fields). `GET /v1/tts/voices` lists it.
**Trace:** FR-VS-04, FR-VS-05, FR-VS-06.

### UAT-VS-02 (Negative) — Consent missing
**Steps:** POST as UAT-VS-01 but with `consent_acknowledged=false` or absent.
**Expected:** `400` `validation_error.consent_required`.
**Trace:** FR-VS-05, NFR-CP-01.

### UAT-VS-03 (Negative) — Duplicate id
**Steps:** Create `myvoice` (UAT-VS-01). POST again with same id.
**Expected:** `409` `validation_error.voice_id_exists`.
**Trace:** FR-VS-05.

### UAT-VS-04 (Negative) — Oversized audio
**Steps:** POST with audio part > `TTS_REFAUDIO_MAX_BYTES`.
**Expected:** `400` `validation_error.ref_audio_invalid` (size).
**Trace:** FR-VS-05, NFR-SE-01.

### UAT-VS-05 (Negative) — Corrupt audio (magic bytes mismatch)
**Steps:** POST with `Content-Type: audio/wav` but body is random bytes.
**Expected:** `400` `validation_error.ref_audio_invalid` (decode/magic-bytes).
**Trace:** FR-VS-05, NFR-SE-02.

### UAT-VS-06 (Negative) — id path traversal attempt
**Steps:** POST with `id="../etc/passwd"`.
**Expected:** `400` `validation_error` (id pattern); no filesystem side effects.
**Trace:** FR-VS-04, FR-VS-11, NFR-SE-03.

### UAT-VS-07 (Happy) — Get metadata (no audio body, ever)
**Steps:** `GET /v1/tts/voices/myvoice`.
**Expected:** `200` with full metadata JSON; no path/URI fields; no audio body. `Content-Type: application/json`.
**Trace:** FR-VS-07.

### UAT-VS-08 (Happy) — Get audio via dedicated endpoint
**Steps:** `GET /v1/tts/voices/myvoice/audio`.
**Expected:** `200` with `Content-Type: audio/wav`, body is the stored blob. Headers include `X-Voice-Id`, `X-Voice-Source`, `X-Content-Sha256`.
**Trace:** FR-VS-07b.

### UAT-VS-08b (Negative) — Audio endpoint when blob missing
**Preconditions:** Metadata exists for `myvoice` but the blob has been deleted out-of-band.
**Steps:** `GET /v1/tts/voices/myvoice/audio`.
**Expected:** `404` `voice_error.voice_blob_missing`.
**Trace:** FR-VS-07b.

### UAT-VS-09 (Happy) — Update metadata and replace audio atomically
**Steps:** `PUT /v1/tts/voices/myvoice` multipart with new metadata and new audio.
**Expected:** `200`. Subsequent synthesis uses the new blob. No partial-write state if the blob put fails mid-way.
**Trace:** FR-VS-08.

### UAT-VS-10 (Happy) — Delete removes metadata + blob
**Steps:** `DELETE /v1/tts/voices/myvoice`. Verify `GET /v1/tts/voices/myvoice` → 404. Verify backend storage no longer contains the blob.
**Trace:** FR-VS-09.

### UAT-VS-11 (Happy) — Synthesis resolves CRUD-created voice
**Steps:** Create `myvoice` (UAT-VS-01). `POST /v1/tts/synthesize` `{"input": "Ciao", "voice": "myvoice"}`.
**Expected:** `200` with audio body. Headers include `X-Voice-Id=myvoice`, `X-Voice-Source=crud`.
**Trace:** FR-VS-10, FR-VS-12, FR-EP-04.

### UAT-VS-12 (Backend-swap) — Optional backend selected without extra installed
**Preconditions:** Base install (no `[postgres]` extra); `TTS_VOICE_METADATA_BACKEND=postgres`.
**Steps:** Start the service.
**Expected:** Startup fails with `config_error.missing_extra` naming the missing extra.
**Trace:** FR-VS-01, NFR-ST-02.

---

## UAT-VM — Voice Seed Ingestion

### UAT-VM-01 (Happy) — Seed ingestion on startup populates empty store
**Preconditions:** Empty voice store; `voice_map.json` references three existing wav files.
**Steps:** Start the service.
**Expected:** `GET /ready` returns `200` post-warmup; `GET /v1/tts/voices` lists three voices, all with `source="seed"`.
**Trace:** FR-VM-01.

### UAT-VM-02 (Idempotent) — Restart with existing store leaves CRUD voices untouched
**Preconditions:** Store contains `myvoice` (`source=crud`) and `alloy` (`source=seed`). `voice_map.json` defines `alloy` and `new_seed`.
**Steps:** Restart the service.
**Expected:** Store still has `myvoice` unchanged, `alloy` unchanged, and now `new_seed` (`source=seed`). No errors.
**Trace:** FR-VM-01.

### UAT-VM-03 (Happy) — File change re-ingests within 2s
**Preconditions:** Service running.
**Steps:** Append a new valid entry to `voice_map.json`; save.
**Expected:** Within 2 s, `GET /v1/tts/voices` lists the new voice with `source="seed"`.
**Trace:** FR-VM-02, FR-VM-03, NFR-OP-05.

### UAT-VM-04 (Recovery) — Invalid seed edit leaves store unchanged
**Preconditions:** Service running with two seeded voices.
**Steps:** Edit `voice_map.json` to point one entry at a non-existent ref_audio file.
**Expected:** Store unchanged; log contains `provider_error.voice_seed_ingest_failed`.
**Trace:** FR-VM-03.

### UAT-VM-05 (Happy) — Missing seed file is OK
**Preconditions:** `TTS_VOICE_MAP_FILE` unset.
**Steps:** Start the service.
**Expected:** `GET /ready` returns `200`; `GET /v1/tts/voices` returns empty list.
**Trace:** FR-VM-05.

---

## UAT-CC — Concurrency, Queueing & Cancellation

### UAT-CC-01 (Happy) — Concurrency bound respected
**Preconditions:** `TTS_MAX_CONCURRENT_REQUESTS=2`; provider monkeypatched with a 1s sleep.
**Steps:** Fire 4 requests in parallel.
**Expected:** Wall-clock ≈ 2s (4 reqs / 2 slots × 1s). `GET /health` during the run shows `concurrent_active=2`.
**Trace:** FR-CC-01.

### UAT-CC-02 (Happy) — Event loop stays responsive
**Preconditions:** As UAT-CC-01.
**Steps:** While synthesis is in flight, hit `GET /health` 20× over 1s.
**Expected:** All 20 `/health` responses return < 50ms each.
**Trace:** FR-CC-02.

### UAT-CC-03 (Negative) — Queue full
**Preconditions:** `TTS_MAX_CONCURRENT_REQUESTS=1`, `TTS_MAX_QUEUE_DEPTH=2`, provider sleeps 2s.
**Steps:** Fire 5 requests in parallel.
**Expected:** Up to 2 (active+queued) succeed; remaining 3 return `429` with `capacity_error.queue_full`.
**Trace:** FR-CC-03.

### UAT-CC-04 (Happy) — Client disconnect cancellation
**Preconditions:** Long synthesis (>5s).
**Steps:** Start a request, drop connection after 1s.
**Expected:** Server logs detect disconnection; no further chunks synthesized after the next chunk boundary; temp files cleaned.
**Trace:** FR-CC-05, FR-VC-04.

---

## UAT-CA — Model Cache

### UAT-CA-01 (Happy) — Single-slot LRU swap
**Preconditions:** `TTS_MODEL_CACHE_SIZE=1`; provider has models `m1`, `m2`.
**Steps:** Request with `m1`, then `m2`, then `m1`.
**Expected:** Logs show 2 loads (m1, then m2 evicts m1, then m1 evicts m2). Each request succeeds.
**Trace:** FR-CA-01, FR-CA-02.

### UAT-CA-02 (Negative) — Invalid model_id doesn't evict
**Steps:** Load `m1`. Request with `model="bogus"`.
**Expected:** `400`/`404` `validation_error.unknown_model`. Subsequent request with `m1` does NOT reload (cache preserved).
**Trace:** FR-CA-03.

### UAT-CA-03 (Happy) — Preload populates cache
**Preconditions:** `TTS_PRELOAD_MODELS="mlx_audio:qwen3-tts"`.
**Steps:** Start the service.
**Expected:** Warmup loads the model; `GET /health` shows it in `model_loaded`; first synthesis incurs no load latency.
**Trace:** FR-CA-04.

---

## UAT-HL — Health, Readiness & Lifecycle

### UAT-HL-01 (Happy) — Health always 200
**Steps:** Hit `/health` during startup warmup, during a synthesis, and during shutdown drain.
**Expected:** Always `200` with structured body; `device`, `dtype`, `provider`, `queue_depth`, `concurrent_active` present.
**Trace:** FR-HL-01.

### UAT-HL-02 (Happy) — Ready gates on warmup
**Steps:** Configure preload; hit `/ready` during warmup, then post-warmup.
**Expected:** During warmup → `503` with `{ ready: false, reason: "warming_up" }`. Post-warmup → `200`.
**Trace:** FR-HL-02.

### UAT-HL-03 (Happy) — Graceful shutdown drain
**Preconditions:** Long synthesis in flight.
**Steps:** Send SIGTERM.
**Expected:** New requests admit → `503 capacity_error.service_unavailable`. In-flight request completes within `TTS_SHUTDOWN_DRAIN_SECONDS`. Process exits 0.
**Trace:** FR-HL-04.

### UAT-HL-04 (Recovery) — Drain timeout forces exit
**Preconditions:** `TTS_SHUTDOWN_DRAIN_SECONDS=2`; provider sleeps 30s.
**Steps:** Start synthesis; send SIGTERM.
**Expected:** Process exits within ~2s; exit code documents forced-shutdown path.
**Trace:** FR-HL-04.

### UAT-HL-05 (Boundary) — Low-memory warning at startup
**Preconditions:** `TTS_MIN_FREE_MEMORY_GB=1024` (artificially huge).
**Steps:** Start the service.
**Expected:** Startup succeeds; a single `WARNING` log line is emitted naming the threshold and current free memory.
**Trace:** FR-HL-05.

---

## UAT-OB — Observability

### UAT-OB-01 (Happy) — X-Request-ID round-trips and is logged
**Steps:** Send a request with `X-Request-ID: abc-123`.
**Expected:** Response header `X-Request-ID: abc-123`. Log lines for the request carry `request_id=abc-123`.
**Trace:** FR-OB-01.

### UAT-OB-02 (Happy) — Request ID auto-generated when absent
**Steps:** Send a request without `X-Request-ID`.
**Expected:** Response carries an auto-generated `X-Request-ID` (UUID-ish). Same value in logs.
**Trace:** FR-OB-01.

### UAT-OB-03 (Happy) — JSON logs when opt-in
**Preconditions:** `APP_LOG_FORMAT=json`.
**Steps:** Start service; send a request.
**Expected:** Each log line is valid JSON with `ts`, `level`, `logger`, `message`, `request_id`.
**Trace:** FR-OB-02.

### UAT-OB-04 (Happy) — Error responses carry X-Error-Code
**Steps:** Send a request that triggers `validation_error.input_too_long`.
**Expected:** Response headers include `X-Request-ID` and `X-Error-Code: input_too_long`.
**Trace:** FR-OB-03, FR-ER-03.

---

## UAT-CF — Configuration

### UAT-CF-01 (Negative) — Invalid env value fails startup
**Preconditions:** `TTS_MAX_CONCURRENT_REQUESTS=-3`.
**Steps:** Start the service.
**Expected:** Startup fails with a clear validation error citing the env var.
**Trace:** FR-CF-01.

### UAT-CF-02 (Happy) — Default-disabled inference timeout
**Preconditions:** `TTS_INFERENCE_TIMEOUT_SECONDS` unset; provider sleeps 60s.
**Steps:** Send a synthesis request.
**Expected:** Request completes after ~60s; no timeout error.
**Trace:** FR-CF-03.

### UAT-CF-03 (Negative) — Inference timeout enforced when set
**Preconditions:** `TTS_INFERENCE_TIMEOUT_SECONDS=2`; provider sleeps 30s.
**Steps:** Send a synthesis request.
**Expected:** Response within ~2s; `504` with `capacity_error.timeout`.
**Trace:** FR-CF-03.

### UAT-CF-04 (Code-review check) — Every new env var documented
**Steps:** Cross-check `FR-CF-02` env-var inventory against README sections.
**Expected:** Every name appears in README with default + meaning.
**Trace:** FR-CF-02, FR-DC-01.

---

## UAT-ER — Error Envelope

### UAT-ER-01 (Happy) — Envelope shape consistent
**Steps:** Trigger one error per category (validation, voice, provider, capacity).
**Expected:** All responses share the envelope `{ "error": { "type", "code", "message", "param"?, "request_id" } }`.
**Trace:** FR-ER-01, FR-ER-02.

### UAT-ER-02 (Recovery) — Unexpected errors don't leak internals
**Preconditions:** Provider raises a `RuntimeError("/Users/foo/secret/path.bin")`.
**Steps:** Send a synthesis request.
**Expected:** `500` with `internal_error.unexpected_error`; message is generic (no path, no traceback). Full traceback present in server logs.
**Trace:** FR-ER-04.

---

## UAT-QG — Quality Gates

### UAT-QG-01 (CI check) — Linting clean
**Steps:** Run `ruff check src/ tests/` and `ruff format --check src/ tests/`.
**Expected:** Both exit 0.
**Trace:** FR-QG-01.

### UAT-QG-02 (CI check) — Strict mypy clean
**Steps:** Run `mypy --strict src/`.
**Expected:** Exit 0; zero errors.
**Trace:** FR-QG-01, FR-QG-03.

### UAT-QG-03 (CI check) — Coverage ≥ 80%
**Steps:** Run `pytest --cov --cov-fail-under=80`.
**Expected:** Exit 0.
**Trace:** FR-QG-01.

### UAT-QG-04 (CI check) — pip-audit clean
**Steps:** Run `pip-audit` against the locked deps.
**Expected:** No high-severity advisories (policy TBD; failing build on any advisory is the safe default).
**Trace:** FR-QG-01.

### UAT-QG-05 (CI check) — Docker image builds
**Steps:** `docker build -t llm-tts-api:ci .` in CI.
**Expected:** Build succeeds; resulting image starts and `/health` returns 200.
**Trace:** FR-QG-04.

---

## UAT-DC — Documentation

### UAT-DC-01 (Doc-review check) — README has all new sections
**Expected:** README contains: Hardware Auto-Detection table; complete env-var inventory matching FR-CF-02; Rich-endpoint request/response examples; voice-CRUD endpoints under `/v1/tts/voices/*` with consent attestation; seed-ingestion mechanism; storage-backend selection matrix (defaults vs `[postgres]`/`[s3]` extras); Error taxonomy table; Roadmap pointers.
**Trace:** FR-DC-01.

### UAT-DC-02 (Doc-review check) — Diagrams refreshed
**Expected:** Sequence diagrams for startup, /v1/tts/synthesize (both buffered & streamed), voice map hot-reload exist and reflect the new lifespan/singleton model.
**Trace:** FR-DC-02.

### UAT-DC-03 (Doc-review check) — OpenAPI covers both endpoints
**Expected:** `docs/openapi/openapi.yaml` describes `/v1/tts/synthesize` (full surface) and `/v1/audio/speech` (unchanged shape).
**Trace:** FR-DC-03.

---

## 10. Traceability Matrix (FR → UAT)

| FR ID | UAT IDs |
|---|---|
| FR-HW-01 | UAT-HW-01, UAT-HW-03 |
| FR-HW-02 | UAT-HW-02, UAT-HW-06 |
| FR-HW-03 | UAT-HW-01, UAT-HW-06 |
| FR-HW-04 | UAT-HW-01 |
| FR-HW-05 | UAT-HW-04, UAT-HW-02 |
| FR-HW-06 | UAT-HW-05 |
| FR-HW-07 | (covered structurally via UAT-HW-04/05) |
| FR-EP-01..05 | UAT-EP-01 … UAT-EP-07 |
| FR-OA-01..04 | UAT-OA-01 … UAT-OA-04 |
| FR-VS-01..12 | UAT-VS-01 … UAT-VS-12 (incl. UAT-VS-08b), UAT-EP-01, UAT-EP-06 |
| FR-VM-01..05 | UAT-VM-01 … UAT-VM-05 |
| FR-CC-01..05 | UAT-CC-01 … UAT-CC-04 |
| FR-CA-01..04 | UAT-CA-01 … UAT-CA-03 |
| FR-HL-01..05 | UAT-HL-01 … UAT-HL-05 |
| FR-OB-01..03 | UAT-OB-01 … UAT-OB-04 |
| FR-CF-01..03 | UAT-CF-01 … UAT-CF-03 |
| FR-ER-01..04 | UAT-ER-01, UAT-ER-02, UAT-OB-04 |
| FR-QG-01..04 | UAT-QG-01 … UAT-QG-05 |
| FR-DC-01..03 | UAT-DC-01 … UAT-DC-03 |

### Coverage gaps explicitly noted
- **FR-OB-04** (Prometheus `/metrics`) — out of scope; no UAT.
- **FR-VM-05 pagination** (OQ-2) — no UAT until shape is decided.

---

## 11. Suggested Execution Sequence

1. **Static gates first**: UAT-QG-01..05.
2. **Unit-level detection**: UAT-HW-01..06.
3. **Startup & lifecycle**: UAT-HL-01..05, UAT-VM-01..02.
4. **Core synthesis**: UAT-EP-01..07.
5. **OpenAI parity**: UAT-OA-01..04.
6. **Voice features**: UAT-VS-01..12, UAT-VM-01..05.
7. **Concurrency**: UAT-CC-01..04.
8. **Cache**: UAT-CA-01..03.
9. **Observability & errors**: UAT-OB-01..04, UAT-ER-01..02, UAT-CF-01..03.
10. **Doc review**: UAT-DC-01..03.