# Sprint 2 — Core services: async concurrency, model cache, error taxonomy, lifecycle

**Status:** IN PROGRESS (Step 1 DONE; Step 2 = S-010 PLANNED)
**Planned:** 2026-05-17
**Stories:** S-006, S-007, S-008, S-009, S-012 (Step 1 — parallel) + S-010 (Step 2 — depends on S-007)
**Cycle:** llm-tts-api improvement cycle, Sprint 2 of N
**Source docs:** `docs/specs/software-spec.md`, `docs/specs/analyst-frs.md`, `docs/specs/writer-nfr.md`, `docs/planning/journal.md`

---

## Objective

Build the runtime backbone the rich endpoint will sit on:

- **Provider auto-selection from the DeviceProfile** so the service picks MLX-audio on Apple Silicon, vLLM-Omni on CUDA hosts, with explicit-override + CPU fallback failures (FR-HW-04..07).
- **Async-correct concurrency** that retires the blocking `threading.Semaphore` from the synthesis path (FR-CC-01..04, NFR-PF-02).
- **LRU model cache** (S-008) with safe-before-evict validation (FR-CA-01..04).
- **Typed error taxonomy + envelope** (FR-ER-01..04) sitting on top of S-004's request-id seam.
- **Full env-var inventory** (FR-CF-01..03) covering the surface S-006..S-010 introduce.
- **Real `/health` ↔ `/ready` split + graceful drain** (FR-HL-01..04) reading the semaphore slots S-007 publishes.

By end of sprint, the service has every infrastructure piece Sprint 3's voice store and Sprint 4's rich endpoint will need.

## Provability

Sprint 2 proves itself when:

- Auto-selection picks MPS provider on Apple Silicon and fails startup with a typed error on CPU-only when no CPU-capable provider is registered.
- A long synthesis runs in flight while `/health` p95 stays ≤50 ms (NFR-PF-02 / UAT-CC-02).
- Excess concurrent requests beyond the queue cap return `429 capacity_error.queue_full`.
- Model cache swaps `m1 → m2 → m1` produce 3 loads; an invalid model_id does NOT evict the current entry.
- Every error response carries the typed envelope `{error: {type, code, message, param?, request_id}}` and an `X-Error-Code` header.
- `/ready` returns 503 during warmup or drain, 200 otherwise; SIGTERM drains in-flight then exits.

## Constraints carried from SRS / NFR

- **No new external services**. Single-process, LAN-only deploy.
- **Async refactor is in-place** (S-007 / RISK-2). Existing `SpeechSynthesizer` is refactored, not rewritten.
- **`mypy --strict` clean** must hold throughout. Coverage gate floor stays at 83 (S-001 ratchet).

---

## Execution Order

```
┌─── Step 1 (5 parallel tasks) ──────────────────────────┐
│  S-006 — Provider capability + auto-selection           │
│  S-007 — Async concurrency model    ← producer for S-010│
│  S-008 — LRU model cache                                │
│  S-009 — Typed error taxonomy + envelope                │
│  S-012 — Configuration inventory + env validation       │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─── Step 2 (1 task — depends on S-007's app.state slots) ┐
│  S-010 — Health/Ready split + graceful drain            │
└─────────────────────────────────────────────────────────┘
```

**Service-boundary enforcement**: S-007 publishes `app.state.queue_semaphore` and `app.state.concurrency_semaphore`. S-010 reads `queue_depth` and `concurrent_active` from those slots in the `/health` response body. So S-007 is a producer; S-010 is the consumer. They MUST land in separate execution steps. The coordinator assembles S-007's impl notes (including its Service Interface section) before spawning S-010.

---

## Stories & Atomic Tasks

### S-006 — Provider capability + auto-selection

**Type:** Technical
**Status:** DONE
**Depends on:** S-005 (DONE)
**Refs:** FR-HW-04..07, BR-2, BR-6, BR-7, A-1, RISK-1, UAT-HW-04..05
**Why selected:** unblocks the rich endpoint's provider resolution; also retires `TTS_PROVIDER` as a default in favor of override.

**Acceptance criteria** (carried verbatim from sprint-1 journal):
- All three current providers declare their `supports_devices` set.
- With env unset on Apple Silicon, `/health` reports the auto-selected provider.
- With `TTS_PROVIDER=vllm_omni` on Apple Silicon → startup fails with a clear error (UAT-HW-05).
- With `TTS_DEVICE=cpu` and no CPU-viable provider → startup fails with `provider_error.no_viable_provider` listing rejected providers (UAT-HW-04).
- Fallback to a hardcoded device→provider table is acceptable if RISK-1 materializes.

**Atomic tasks:**

| Task | Purpose |
|---|---|
| S-006.T1 | Extend `TTSProviderStrategy` Protocol with `supports_devices: set[Device]` (default empty for unknown providers). |
| S-006.T2 | Annotate `MLXAudioTTSProvider`, `VoxtralTTSProvider`, `VllmOmniTTSProvider` with their actual supports_devices sets. |
| S-006.T3 | Auto-selection logic in `dependencies.build_default_dependencies` (or new helper): pick provider from `DeviceProfile.device` consulting the registry; preserve `TTS_PROVIDER` env override semantics. |
| S-006.T4 | Startup-failure path: typed error `provider_error.no_viable_provider` listing each rejected provider + reason. |
| S-006.T5 | `/health` reports `provider` field with auto-vs-override source. |
| S-006.T6 | Tests covering UAT-HW-04 (no viable CPU provider → exit) and UAT-HW-05 (incompatible env override → exit). |

---

### S-007 — Async-correct concurrency model

**Type:** Technical
**Status:** DONE
**Depends on:** S-003 (DONE)
**Refs:** FR-CC-01..04, NFR-PF-02, NFR-PF-04, NFR-SC-01..03, RISK-2
**Why selected:** unblocks NFR-PF-02 verification and is the producer for S-010's `/health` queue-depth signals.

**Acceptance criteria:**
- `/health` responds in ≤50 ms p95 during in-flight synthesis (UAT-CC-02, NFR-PF-02).
- 4 parallel requests with `TTS_MAX_CONCURRENT_REQUESTS=2` complete in ~2× single-request wall-clock (UAT-CC-01).
- Excess requests beyond admission cap return `429 capacity_error.queue_full` (UAT-CC-03).
- No use of `threading.Semaphore` remains in the synthesis path.

**Atomic tasks:**

| Task | Purpose |
|---|---|
| S-007.T1 | Add `asyncio.Semaphore` for concurrency ceiling on `app.state.concurrency_semaphore`; expose via lifespan (replaces threading.Semaphore in `SpeechSynthesizer`). |
| S-007.T2 | Add admission queue semaphore on `app.state.queue_semaphore` bounded by `TTS_MAX_QUEUE_DEPTH` (default 8). |
| S-007.T3 | Per-(provider, model) `asyncio.Lock` for engine serialization (lazily created; held in a `dict[(str, str), Lock]` on app.state). |
| S-007.T4 | Wrap sync provider calls with `anyio.to_thread.run_sync` (audit `MLXAudioTTSProvider.synthesize`, `VoxtralTTSProvider.synthesize`, `VllmOmniTTSProvider.synthesize`). |
| S-007.T5 | Service-Interface doc in impl notes: the names/types of `app.state.queue_semaphore` and `app.state.concurrency_semaphore` slots (S-010 consumes). |
| S-007.T6 | Tests: UAT-CC-01 (parallel throughput), UAT-CC-02 (event-loop responsiveness), UAT-CC-03 (queue-full). |

---

### S-008 — LRU model cache

**Type:** Technical
**Status:** DONE
**Depends on:** S-003 (DONE)
**Refs:** FR-CA-01..04, NFR-SC-04, BR-3
**Why selected:** the rich endpoint will accept model overrides; cache is required to avoid reload on every switch.

**Acceptance criteria:**
- Sequential requests `m1 → m2 → m1` cause 3 loads with cache size 1 (UAT-CA-01).
- Request with bogus model_id does NOT evict the current entry (UAT-CA-02).
- `TTS_PRELOAD_MODELS` populates the cache during startup; first synthesis incurs no load latency (UAT-CA-03).

**Atomic tasks:**

| Task | Purpose |
|---|---|
| S-008.T1 | LRU cache class keyed by `(provider, model_id)`; size = `TTS_MODEL_CACHE_SIZE` (default 1). |
| S-008.T2 | Pre-eviction validation: model_id in provider's allow-list + file deps exist; failure leaves current entry alone. |
| S-008.T3 | Eviction calls provider `unload()` if present; else drop reference. |
| S-008.T4 | `TTS_PRELOAD_MODELS` parser ("provider:model,provider:model"); preload in lifespan, contribute to readiness gating. |
| S-008.T5 | Tests: UAT-CA-01..03 + cache-thrash regression. |

---

### S-009 — Typed error taxonomy + envelope

**Type:** Technical
**Status:** DONE
**Depends on:** S-004 (DONE)
**Refs:** FR-ER-01..04, NFR-SE-04, NFR-OB-03
**Why selected:** every other Sprint 2 story emits errors (S-006 no-viable-provider, S-007 queue-full, S-008 unknown-model); they need the envelope ready.

**Acceptance criteria:**
- All four envelope fields plus `request_id` present on every error response (UAT-ER-01).
- Unexpected exception with sensitive path text produces a generic message + traceback only in logs (UAT-ER-02).
- `X-Error-Code` matches `error.code` (UAT-OB-04).

**Atomic tasks:**

| Task | Purpose |
|---|---|
| S-009.T1 | Extend `errors.py` with category enum (`validation_error`, `voice_error`, `provider_error`, `capacity_error`, `internal_error`) and sub-code registry. |
| S-009.T2 | `OpenAIHTTPException` (or replacement) carries `(type, code, message, param?)`; FastAPI handler pulls `request_id` from `current_request_id()` (S-004 seam). |
| S-009.T3 | Generic 500 handler maps unhandled exceptions to `internal_error.unexpected_error` and logs the traceback (no payload leakage). |
| S-009.T4 | `X-Error-Code` response header set by handler. |
| S-009.T5 | Tests: UAT-ER-01 (envelope shape), UAT-ER-02 (no traceback in body), UAT-OB-04 (header parity), unhandled-exception path. |

---

### S-012 — Configuration inventory + env validation

**Type:** Technical
**Status:** DONE
**Depends on:** none (independent of Step 1 peers, but pairs naturally)
**Refs:** FR-CF-01..03, NFR-OP-03
**Why selected:** S-006/S-007/S-008 all introduce new env vars; consolidating their parsing + validation in `Settings.__post_init__` is cheaper as a single pass than per-story.

**Acceptance criteria:**
- All env vars are parsed and validated; invalid value → startup exits non-zero with named-var message (UAT-CF-01).
- Default-unset timeout: 60 s synthesis succeeds (UAT-CF-02).
- Configured timeout = 2 s: 30 s synthesis is interrupted with 504 (UAT-CF-03).
- README inventory check in S-019 finds every new var documented (UAT-CF-04 — README update deferred to S-019).

**Atomic tasks:**

| Task | Purpose |
|---|---|
| S-012.T1 | Add to `Settings`: `tts_device`, `tts_dtype`, `tts_max_queue_depth`, `tts_model_cache_size`, `tts_preload_models`, `tts_inference_timeout_seconds`, `tts_shutdown_drain_seconds`, `app_log_format`. (Voice/storage env vars deferred to Sprint 3.) |
| S-012.T2 | Validation: integers ≥ 0 where applicable; enum-style values via the same `frozenset` pattern used in `engine/device.py`. |
| S-012.T3 | `TTS_INFERENCE_TIMEOUT_SECONDS` default UNSET → disabled. Positive value enables `asyncio.wait_for` wrapper. |
| S-012.T4 | Tests: UAT-CF-01..03 (each invalid value class). |

---

### S-010 — Health/Ready split + graceful drain

**Type:** Technical
**Status:** PLANNED
**Depends on:** S-007 (Step 1; produces semaphore slots) + S-003 (DONE)
**Refs:** FR-HL-01/02/04/05, NFR-RL-01/05, NFR-OP-04
**Why selected:** completes the lifecycle FR surface; consumes S-007's slots; only Step-2 story.

**Acceptance criteria:**
- `/health` returns 200 during startup, synthesis, and drain (UAT-HL-01). Body includes `device`, `dtype`, `provider`, `model_loaded`, `queue_depth`, `concurrent_active`, `version`.
- `/ready` returns 503 during warmup, 200 post-warmup (UAT-HL-02).
- SIGTERM drains and exits 0 within drain budget (UAT-HL-03); forced exit when exceeded (UAT-HL-04).
- Low-memory warning emitted when threshold breached (UAT-HL-05).

**Atomic tasks:**

| Task | Purpose |
|---|---|
| S-010.T1 | `app.state.ready: bool` flag, set False at create_app, True at lifespan-yield, False on drain. |
| S-010.T2 | Rewrite `/health` to read `app.state.{device_profile, model_cache (S-008), queue_semaphore (S-007), concurrency_semaphore (S-007), settings}` and emit the body fields above. |
| S-010.T3 | Rewrite `/ready` to use `app.state.ready` flag (replace the S-003 `tts_service is not None` check). |
| S-010.T4 | Graceful drain: lifespan `try/yield/finally` clears the ready flag and waits up to `TTS_SHUTDOWN_DRAIN_SECONDS` for the concurrency semaphore to fully release. |
| S-010.T5 | Optional psutil low-memory warning at startup. |
| S-010.T6 | Tests: UAT-HL-01..05. |

---

## Sprint-wide testing & verification

- **All five Sprint-1 CI gates must remain green** after Sprint 2 lands: ruff check + format, mypy --strict, pytest --cov ≥ 83, pip-audit.
- **NFR-PF-01 perf baseline**: if available (operator has run S-002 script), re-run after S-007 lands and confirm ≤ +10% regression. If baseline still pending, document Sprint 2's measurements anyway.

## Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| S-007 (async refactor) is more invasive than the "no rewrite" constraint allows; residual blocking remains. | Medium | Medium | Stage refactor; benchmark NFR-PF-02 (event-loop responsiveness) before/after; accept some sync-wrap in `anyio.to_thread` if needed. RISK-2 from SRS §8. |
| S-006 (provider auto-selection) trips on a provider with poorly-declared `supports_devices`. | Medium | Low | Fallback to device→provider table if Protocol annotation fails (RISK-1 from SRS §8). |
| S-008 model cache thrashes under cache-size=1 with multiple operators using different models. | Low | Low | Default is configurable; ops doc note. |

## Stories NOT in this sprint (and rationale)

- **S-011 voice seed ingestion**: blocked on S-022 + S-025 (voice CRUD, Sprint 3).
- **S-013 rich endpoint** and downstream: depend on Sprint 3 voice CRUD.
- **S-019..S-021 polish**: end-of-cycle work.

## Definition of Done (Sprint 2)

- All six stories' acceptance criteria met.
- All CI gates green on `main` after merge.
- S-007's `app.state.{queue_semaphore, concurrency_semaphore}` slots documented in Service Interface and consumed by S-010 via `/health`.
- `TTS_*` env vars from S-006/S-007/S-008 inventoried in `Settings` and validated.
- All error responses use the S-009 envelope.
- Sprint review document at `docs/planning/sprints/sprint-review-2.md` (created by the code-reviewer skill at end-of-sprint).
