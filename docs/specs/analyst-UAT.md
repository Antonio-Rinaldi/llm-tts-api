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

## UAT-PR — Audio-Generation Presets (*cycle 2*)

### UAT-PR-01 (Happy path) — Default preset applies when client omits `preset`
**Preconditions:** `config/presets.json` shipped with default `balanced` preset.
**Steps:** `POST /v1/tts/synthesize` with `{"input":"Hello world.","voice":"alloy"}` (no `preset`).
**Expected:** 200 OK; response header `X-Preset-Effective: balanced(...)` present; audio body non-empty.
**Trace:** FR-PR-05, FR-PR-06, FR-PR-08.

### UAT-PR-02 (Happy path) — Named preset applies its defaults
**Steps:** Same request body with `"preset":"quality"` and `"stream":false`.
**Expected:** 200 OK; `X-Preset-Effective: quality(...)` lists the quality preset's resolved fields; `Content-Type: audio/flac` (per FR-FMT-05); body is FLAC bytes.
**Trace:** FR-PR-03, FR-PR-06, FR-PR-08, FR-FMT-05.

### UAT-PR-03 (Negative) — Unknown preset returns 400 preset_unknown
**Steps:** Request with `"preset":"cinematic"` and no such preset configured.
**Expected:** 400 `validation_error.preset_unknown`; error message lists the available preset names.
**Trace:** FR-PR-07.

### UAT-PR-04 (Conflict) — Explicit field overrides preset pin; warning logged
**Preconditions:** `quality` preset pins `provider="voxtral"` in `config/presets.json`.
**Steps:** Request `{"input":"...","voice":"alloy","preset":"quality","provider":"mlx_audio"}`.
**Expected:** 200 OK; `X-Preset-Effective` shows `provider=mlx_audio`; log line at WARN level with request_id and the override reason.
**Trace:** FR-PR-06, FR-PR-08.

### UAT-PR-05 (Soft-ignore) — Provider can't honor a preset knob; ignored knobs surfaced
**Preconditions:** Preset's `defaults.temperature=0.5`; the active provider's `synthesize_chunks` does not accept temperature.
**Steps:** Request with `preset` whose defaults include unsupported knob.
**Expected:** 200 OK; response header `X-Preset-Ignored-Knobs: temperature`; request succeeds.
**Trace:** FR-PR-09.

### UAT-PR-06 (Compat) — `/v1/audio/speech` always uses server default preset
**Preconditions:** `TTS_DEFAULT_PRESET=balanced`.
**Steps:** OpenAI-shaped `POST /v1/audio/speech` request (no `preset` field allowed in body).
**Expected:** 200 OK; the response body bytes are byte-identical to a `POST /v1/tts/synthesize` request with the equivalent input AND `preset="balanced"` (extends the S-018 paired UAT). Headers on the OpenAI path still strip `X-Preset-Effective` etc. per S-017 contract.
**Trace:** FR-PR-10.

### UAT-PR-07 (Negative) — Extra `preset` field on OpenAI request is rejected
**Steps:** `POST /v1/audio/speech` with `{"model":"...","input":"...","voice":"alloy","preset":"fast"}`.
**Expected:** 422 schema validation error (`SpeechRequest` has `extra="forbid"`); preserves OpenAI byte-identity.
**Trace:** FR-PR-10.

### UAT-PR-08 (Hot-reload) — presets.json change picked up within poll interval
**Preconditions:** Service running with `config/presets.json` loaded; in-flight synthesis NOT active.
**Steps:** Edit `presets.json` to add a new `cinematic` preset; wait ≤ 2 s; issue request with `"preset":"cinematic"`.
**Expected:** 200 OK; preset resolves; no service restart required.
**Trace:** FR-PR-11.

### UAT-PR-09 (Snapshot) — In-flight request unaffected by mid-flight reload
**Preconditions:** Long-running quality-preset request in flight.
**Steps:** Edit presets.json to remove the `quality` preset while the request is mid-flight.
**Expected:** In-flight request completes successfully using the snapshot taken at request-start. Subsequent requests with `preset="quality"` return 400 `preset_unknown`.
**Trace:** FR-PR-11.

### UAT-PR-10 (Custom preset NOT in OpenAPI) — Operator preset works but isn't enumerated
**Preconditions:** `config/presets.json` has a custom `cinematic` preset.
**Steps:** (a) Request with `preset="cinematic"`. (b) `GET /v1/models`. (c) Read `docs/openapi/openapi.yaml`.
**Expected:** (a) 200 OK. (b) `/v1/models` response does NOT include preset names anywhere. (c) OpenAPI's `preset` field type is `string`; the spec MAY mention `fast/balanced/quality` as informational examples; `cinematic` is NOT mentioned anywhere in the YAML.
**Trace:** FR-PR-12.

### UAT-PR-11 (Startup-fail) — Invalid presets.json fails fast
**Preconditions:** `config/presets.json` has `presets.fast.defaults.temperature = "not-a-number"`.
**Steps:** Start the service.
**Expected:** Process exits non-zero; stderr includes `config_error.presets_invalid` with path `presets.fast.defaults.temperature`. No HTTP socket bound.
**Trace:** FR-PR-02.

### UAT-PR-12 (Startup-fail) — Preset pins invalid (provider, model)
**Preconditions:** `config/presets.json` has `presets.quality.defaults.model = "nonexistent-model"` not in any provider's allow-list.
**Steps:** Start the service.
**Expected:** Process exits non-zero with `config_error.preset_provider_invalid`. Error message names the offending preset + the unknown model.
**Trace:** FR-PR-13.

### UAT-PR-13 (Startup-fail) — TTS_DEFAULT_PRESET names an unknown preset
**Preconditions:** `TTS_DEFAULT_PRESET=bogus` env var set; not in `config/presets.json`.
**Steps:** Start the service.
**Expected:** Process exits non-zero with `config_error.presets_invalid` (or a dedicated `config_error.default_preset_unknown` — analyst leaves error-code naming to implementation). Message names the offending env var.
**Trace:** FR-PR-05.

### UAT-PR-14 (Startup-fail) — presets.json is world-writable
**Preconditions:** `config/presets.json` exists with mode `0666` (world-writable) OR ownership differs from the service user.
**Steps:** Start the service.
**Expected:** Process exits non-zero with `config_error.presets_unsafe_permissions`. Log line names the offending mode bits / owner uid mismatch.
**Trace:** NFR-SE-09.

### UAT-PR-15 (Reload) — Invalid presets.json at runtime is rejected; service stays on prior config
**Preconditions:** Service running with valid `presets.json` loaded; `quality` preset present.
**Steps:** Write an invalid `presets.json` (e.g. malformed JSON, or `presets.fast.defaults.temperature="oops"`). Wait ≤ 2 s. Issue request with `preset="quality"`.
**Expected:** Reload notification fires; reloader validates new file; validation fails; in-memory registry NOT swapped; WARN log line records the rejection. The follow-up request succeeds against the prior `quality` preset. No service restart, no 5xx.
**Trace:** NFR-SE-10.

### UAT-PR-16 (Observability) — INFO log carries resolved preset state
**Steps:** Issue a synthesis request with `preset="quality"` against an `app_log_format=json` deployment. Capture the INFO log line for the request.
**Expected:** Log line contains JSON fields: `request_id`, `resolved_preset="quality"`, `ignored_knobs` (string, possibly empty), `postprocess_applied` (string, comma-separated steps), `response_format="flac"`, `stream_downgraded` (boolean). Log line is payload-free (NO `input` text, NO audio bytes).
**Trace:** NFR-OP-06.

### UAT-PR-17 (Regression) — S-018 byte-identity paired UAT passes byte-identically across cycle 2
**Preconditions:** Cycle-2 master state; default `config/presets.json` shipped; `TTS_DEFAULT_PRESET=balanced`.
**Steps:** Run `uv run pytest tests/test_openai_adapter_parity.py -v` (the existing S-018 paired UAT — NOT modified in cycle 2).
**Expected:** All paired tests pass. sha256 of rich(`preset=balanced`, no overrides) body byte-equals sha256 of OpenAI-path body for the same effective request. The test file itself is byte-identical to its merged cycle-1 form (`git diff master tests/test_openai_adapter_parity.py` empty).
**Trace:** NFR-PT-05.

### UAT-PR-18 (Happy + override) — Preset pinning `voice` + `language` is honored; explicit fields still win
**Preconditions:** A custom preset in `config/presets.json` with `defaults.voice="alloy"`, `defaults.language="en"`, `defaults.number_lang="en"` (added per triage T-3 / FR-PR-03 amendment).
**Steps:**
(a) `POST /v1/tts/synthesize` with `{"input":"Hello world.","preset":"<custom>"}` (no `voice`, no `language`, no `number_lang`).
(b) `POST /v1/tts/synthesize` with `{"input":"Hello world.","preset":"<custom>","language":"it"}` (explicit `language` override; preset's `voice` + `number_lang` still applied).
**Expected:**
(a) 200 OK; `X-Preset-Effective` shows `voice=alloy, language=en, number_lang=en`; synthesis uses voice `alloy` and English pronunciation.
(b) 200 OK; `X-Preset-Effective` shows `voice=alloy, language=it (override), number_lang=en`; synthesis uses voice `alloy`, Italian pronunciation, English number expansion. WARN log records the `language` override per BR-10.
**Trace:** FR-PR-03 (amended), FR-PR-06, FR-PR-08, BR-10.

---

## UAT-PP — Audio Post-Processing (*cycle 2*)

### UAT-PP-01 (Happy path) — RMS normalize applied for quality preset
**Preconditions:** `quality` preset has `defaults.postprocess.rms_normalize=true` and `defaults.normalize_db=-16.0`.
**Steps:** Request with `preset="quality"`.
**Expected:** Response header `X-Postprocess-Applied: rms_normalize` (or `silence_trim,rms_normalize` if trim also enabled); decoded audio's measured RMS ≈ -16 dBFS within ±0.5 dB tolerance.
**Trace:** FR-PP-01, FR-PP-03, FR-PP-06.

### UAT-PP-02 (Happy path) — Silence trim removes leading silence
**Preconditions:** Provider output has ~1s leading silence on chosen voice.
**Steps:** Request with `preset="quality"` (postprocess.silence_trim=true).
**Expected:** Output audio's leading silence ≤ 100ms (50ms pad + ~50ms tolerance). `X-Postprocess-Applied` lists `silence_trim`.
**Trace:** FR-PP-04, FR-PP-06.

### UAT-PP-03 (Ordering) — Pipeline order is denoise → trim → normalize
**Steps:** Code-level test or DEBUG log assertion: enable all three postproc flags; capture intermediate signal at each pipeline boundary; assert order in module comments and execution.
**Expected:** Module docstring and code show the documented ordering; intermediate signals verify it.
**Trace:** FR-PP-02.

### UAT-PP-04 (Optional extra) — denoise=true without [denoise] extra logs WARN and no-ops
**Preconditions:** Service installed WITHOUT the `[denoise]` extra; preset has `postprocess.denoise=true`.
**Steps:** Request that triggers the preset.
**Expected:** 200 OK; output audio not denoised; log line at WARN level mentioning the missing extra. `X-Postprocess-Applied` does NOT list `denoise`.
**Trace:** FR-PP-05.

### UAT-PP-05 (Header) — X-Postprocess-Applied absent when no postproc ran
**Preconditions:** `fast` preset with all postproc flags false.
**Steps:** Request with `preset="fast"`.
**Expected:** 200 OK; `X-Postprocess-Applied` header is NOT set on the response.
**Trace:** FR-PP-06.

### UAT-PP-06 (Stream downgrade) — quality + stream=true → buffered with X-Stream-Downgraded
**Steps:** `POST /v1/tts/synthesize` with `{"preset":"quality","stream":true,"input":"...","voice":"alloy"}`.
**Expected:** 200 OK; response is buffered (no chunked transfer encoding, no trailers); response header `X-Stream-Downgraded: quality-postproc` set; full post-processing applied; `X-Postprocess-Applied` populated.
**Trace:** FR-PP-07.

### UAT-PP-07 (Insertion point) — Postproc runs after assembly, before format encoding
**Steps:** Code-review assertion in `services/synthesize_service.py`: `postprocess_audio(...)` is called AFTER chunk assembly + BEFORE `soundfile`-based format conversion.
**Expected:** Inspection of the function flow confirms ordering. Per-step LOG/test instrumentation may pin this; pure structural assertion otherwise.
**Trace:** FR-PP-08.

---

## UAT-FMT — Response Format Extension (*cycle 2*)

### UAT-FMT-01 (Happy path) — wav (16-bit) still works (regression)
**Steps:** Request with `response_format="wav"` (explicit) or with the `fast`/`balanced` preset (default `wav`).
**Expected:** 200 OK; `Content-Type: audio/wav`; body is 16-bit PCM WAV decodable by `wave` stdlib.
**Trace:** FR-FMT-01, FR-FMT-05, FR-FMT-07.

### UAT-FMT-02 (Happy path) — wav24 (24-bit) works on a supporting provider
**Preconditions:** Active provider declares `wav24 ∈ supported_response_formats`.
**Steps:** Request with `response_format="wav24"`.
**Expected:** 200 OK; `Content-Type: audio/wav`; body decodes as 24-bit PCM WAV via `soundfile.read(...).dtype == 'int32' or 'float64'` (depending on soundfile coercion).
**Trace:** FR-FMT-01, FR-FMT-06, FR-FMT-07.

### UAT-FMT-03 (Happy path) — flac works; default for quality preset
**Preconditions:** Active provider declares `flac ∈ supported_response_formats`.
**Steps:** Request with `preset="quality"` (default `response_format=flac` per FR-FMT-05).
**Expected:** 200 OK; `Content-Type: audio/flac`; body decodes via `soundfile.read(...)` and matches the equivalent WAV decode within sample-tolerance (lossless invariant).
**Trace:** FR-FMT-05, FR-FMT-06, FR-FMT-07.

### UAT-FMT-04 (Negative) — Unsupported format on active provider returns 400
**Preconditions:** Active provider declares `supported_response_formats = {"wav"}` only.
**Steps:** Request with `response_format="flac"`.
**Expected:** 400 `validation_error.format_unsupported`; message lists the supported set (`"Provider 'X' supports only: wav. Requested: flac"`).
**Trace:** FR-FMT-02, FR-FMT-03.

### UAT-FMT-05 (Startup-fail) — Preset pins flac, auto-selected provider doesn't support
**Preconditions:** `config/presets.json` quality preset has `defaults.response_format=flac`; on the deployed device, auto-selection picks a provider whose `supported_response_formats` excludes flac.
**Steps:** Start the service.
**Expected:** Process exits non-zero with `config_error.preset_provider_invalid` (per FR-FMT-04). Error message identifies the preset and the format mismatch.
**Trace:** FR-FMT-04.

### UAT-FMT-06 (Capability declaration) — Each provider exposes supported_response_formats
**Steps:** Code-level / introspection check: each `TTSProviderStrategy` subclass exposes a non-empty `supported_response_formats: set[Literal["wav","wav24","flac"]]` attribute or method.
**Expected:** mlx_audio, voxtral, vllm_omni providers each declare a measured (non-assumed) set. The day-one matrix is recorded in `docs/specs/software-spec.md` cycle-2 section.
**Trace:** FR-FMT-02.

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
| FR-PR-01..13 (*cycle 2*) | UAT-PR-01 … UAT-PR-13 |
| FR-PR-03 amendment (T-3, 2026-05-19) | UAT-PR-18 |
| NFR-SE-09 (*cycle 2*) | UAT-PR-14 |
| NFR-SE-10 (*cycle 2*) | UAT-PR-15 |
| NFR-OP-06 (*cycle 2*) | UAT-PR-16 |
| NFR-PT-05 (*cycle 2*) | UAT-PR-17 |
| FR-PP-01..08 (*cycle 2*) | UAT-PP-01 … UAT-PP-07 |
| FR-FMT-01..07 (*cycle 2*) | UAT-FMT-01 … UAT-FMT-06 |

### Coverage gaps explicitly noted
- **FR-OB-04** (Prometheus `/metrics`) — out of scope; no UAT.
- **FR-VM-05 pagination** (OQ-2) — no UAT until shape is decided.
- **FR-PR-09 multi-knob soft-ignore exhaustive matrix** — UAT-PR-05 covers the principle with one knob; an exhaustive provider-vs-knob matrix is left to the technical-writer's NFR-PT testing approach.

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
11. **(*cycle 2*) Preset config validation**: UAT-PR-11..13 (startup-fail tier).
12. **(*cycle 2*) Preset resolution**: UAT-PR-01..10 (runtime resolution + headers + hot-reload).
13. **(*cycle 2*) Format extension**: UAT-FMT-01..06 (per-provider capability + 400 on mismatch).
14. **(*cycle 2*) Post-processing**: UAT-PP-01..07 (per-step verification + stream downgrade).