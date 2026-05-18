# Sprint 4 — Rich endpoint surface: synthesize + streaming + cancellation

**Status:** DONE
**Planned:** 2026-05-18
**Stories:** S-013 (Step 1) → S-015 + S-016 (Step 2 parallel)
**Cycle:** llm-tts-api improvement cycle, Sprint 4 of N
**Source docs:** `docs/specs/software-spec.md`, `docs/specs/analyst-frs.md`, `docs/specs/writer-nfr.md`, `docs/planning/journal.md`

---

## Objective

Ship the new `POST /v1/tts/synthesize` endpoint — the source of truth for TTS synthesis. It consumes everything Sprints 1–3 built:

- voice store (S-022 Protocols + S-025 CRUD + S-011 seed ingestion)
- provider auto-selection (S-006) + concurrency model (S-007) + LRU model cache (S-008)
- typed error envelope (S-009) + full env-config (S-012)

Streaming responses with header/trailer metadata (S-015) and client-disconnect cancellation (S-016) round out the surface so Sprint 5's OpenAI adapter has a stable layer to translate to.

By end of sprint, the service exposes the rich endpoint at full capability — Pydantic-validated request bodies, chunked WAV streaming, queue-admission failures returning 429, client disconnects stopping work at the next chunk boundary, and metadata flowing back via X-* headers (set at response start) and trailers (set at end-of-stream when the client advertises `TE: trailers`).

## Provability

Sprint 4 proves itself when:

- Map-voice synthesis returns 200 with the full header set: `X-Request-ID`, `X-Provider`, `X-Model`, `X-Device`, `X-Dtype`, `X-Voice-Source` (∈ `{seed, crud}`), `X-Voice-Id`, `X-Chunks`, `X-Total-Duration-Ms`. Voice resolved from `app.state.voice_metadata_repo` + `app.state.voice_blob_repo`.
- Unknown field → 422 with `validation_error` + `param`. Missing `voice` → 400 `validation_error.voice_required`. Unknown `voice` id → 404 `voice_error.voice_not_found`.
- Input at `TTS_MAX_INPUT_CHARS` succeeds; over → 400 `validation_error.input_too_long`.
- Per-request overrides (`normalize_db`, `max_sentences_per_chunk`, `temperature`, `top_p`) take effect.
- Streaming (`stream=true`) yields first audio byte well before total duration; trailers include `X-Chunks` + `X-Total-Duration-Ms` when supported.
- Client drops connection mid-synthesis → semaphore released within one chunk boundary; temp files cleaned.
- All CI gates green: ruff, mypy --strict, pytest --cov-fail-under=83, pip-audit.

## Constraints carried from SRS / NFR

- **Pydantic `extra="forbid"`** on the request model (NFR-MT-04).
- **No `ref_audio` inline field** — voices resolved by id only, via the voice store (post-OQ-3).
- **Header inventory canonical** per SRS §5 Resolution C-2. S-015 emits headers at response start; trailers only when client advertises `TE: trailers` AND uvicorn supports them.
- **Event loop must stay responsive** under streaming (NFR-PF-02; `anyio.to_thread.run_sync` for sync provider calls).

---

## Execution Order

```
┌── Step 1 ─────────────────────────────────────────────────┐
│  S-013 — POST /v1/tts/synthesize (full capability surface)│
└────────────────────────────────────────────────────────────┘
                            ↓
┌── Step 2 (2 parallel; both extend S-013) ─────────────────┐
│  S-015 — Chunked streaming + X-* headers + trailers       │
│  S-016 — Client-disconnect → cancel-at-chunk-boundary     │
└────────────────────────────────────────────────────────────┘
```

**Service-boundary enforcement**: S-013 publishes the synthesis endpoint shape (request model, header contract, handler skeleton). S-015 + S-016 extend the endpoint. They MUST be in a later step than S-013 because they consume its endpoint shape. They CAN be parallel because they touch different concerns: S-015 = response-side (chunked transfer, headers/trailers, body framing); S-016 = request-side (disconnect detection, semaphore release, in-flight cancellation). Step-2 merge conflicts will be additive on the shared router file (same pattern as Sprint 3 Step 2).

---

## Stories & Atomic Tasks

### S-013 — Rich endpoint `POST /v1/tts/synthesize`

**Type:** User
**Status:** DONE
**Depends on:** S-006, S-007, S-008, S-009, S-011, S-012, S-025 (all DONE)
**Refs:** FR-EP-01..04, NFR-MT-04, BR-1..4, BR-9, SRS §5 header inventory
**Why selected:** Foundation for all of Step 2 + Sprint 5 (OpenAI adapter, byte-identity).

**Acceptance criteria** (from journal):
- Map-voice synthesis returns 200 with full header set including `X-Voice-Source=crud` (UAT-EP-01, UAT-VS-11).
- Unknown field is rejected with 422 + `param` (UAT-EP-03).
- Input at limit succeeds; over limit returns `validation_error.input_too_long` (UAT-EP-04).
- Missing `voice` → `validation_error.voice_required` (UAT-EP-05).
- Unknown `voice` id → `voice_error.voice_not_found` (UAT-EP-06).
- Per-request overrides (`normalize_db`, chunking) take effect (UAT-EP-07).

**Atomic tasks:**

| Task | Purpose |
|---|---|
| S-013.T1 | Pydantic request model in `src/llm_tts_api/schemas/synthesis.py` with `ConfigDict(extra="forbid")`. Fields: `input`, `voice` (required), `provider`, `model`, `response_format` (wav MUST), `stream` (bool, default false — actual streaming behavior wired in S-015), `normalize_db`, `max_sentences_per_chunk`, `language`, `number_lang`, `temperature`, `top_p`. |
| S-013.T2 | Router `src/llm_tts_api/routers/synthesize.py` mounted at `/v1/tts`. POST `/synthesize` handler resolves voice via `Depends(get_voice_metadata_repo)` + `Depends(get_voice_blob_repo)`. |
| S-013.T3 | Voice resolution: lookup voice record by id; raise `voice_error.voice_not_found` (404) on miss. Pull audio bytes from blob repo; write to ephemeral `tempfile.NamedTemporaryFile` per request; deleted in `finally`. |
| S-013.T4 | Provider/model resolution: explicit `provider` override → registry lookup; else `app.state.provider_selection.provider_name`. Model: explicit `model` → check allow-list; else provider default. |
| S-013.T5 | Synthesis flow: acquire queue admission semaphore (non-blocking; overflow → 429 `capacity_error.queue_full`); acquire concurrency semaphore; acquire per-(provider, model) lock; dispatch sync provider call via `anyio.to_thread.run_sync`; release in `finally`. |
| S-013.T6 | Response: buffered WAV bytes when `stream=false`. Headers at response start: `X-Request-ID`, `X-Provider`, `X-Model`, `X-Device`, `X-Dtype`, `X-Voice-Source`, `X-Voice-Id`, `X-Chunks`, `X-Total-Duration-Ms`. `Content-Type: audio/wav`. |
| S-013.T7 | Per-request overrides: `normalize_db` / `max_sentences_per_chunk` / `temperature` / `top_p` flow into the chunking + normalization pipeline. |
| S-013.T8 | Input validation: enforce `TTS_MAX_INPUT_CHARS`; reject empty / whitespace input; consistent `validation_error.input_too_long` envelope. |
| S-013.T9 | Wire router into `main.py:create_app`. Verify the existing OpenAI-shaped `/v1/audio/speech` continues to work (no regression to existing 24 tests). |
| S-013.T10 | Tests: UAT-EP-01..07 + the voice-resolution + queue-full + temp-file-cleanup cases. Pin the response header inventory (failing the test on any drift). |

---

### S-015 — Streaming response with headers/trailers

**Type:** User
**Status:** DONE
**Depends on:** S-013 (Step 1)
**Refs:** FR-EP-05, SRS §5 Resolution G-3 (trailer fallback)
**Why selected:** Streaming path completes the rich-endpoint contract and is required for the OpenAI SDK streaming compatibility S-017 will rely on.

**Acceptance criteria:**
- `stream=true` returns chunked transfer encoding; first audio byte arrives before total duration / 2 (UAT-EP-02, NFR-PF-03).
- Headers from FR-EP-04 (X-Request-ID, X-Provider, X-Model, X-Device, X-Dtype, X-Voice-Source, X-Voice-Id) set at response start.
- `X-Chunks` + `X-Total-Duration-Ms` emitted as **trailers** when client advertises `TE: trailers` AND uvicorn supports them; otherwise omitted (never faked, never block the stream waiting for finality).
- Streaming MUST NOT block the event loop.

**Atomic tasks:**

| Task | Purpose |
|---|---|
| S-015.T1 | `StreamingResponse` (FastAPI) returning an async generator that yields per-chunk WAV bytes. Headers set at response start include everything from FR-EP-04 except the two end-of-stream fields. |
| S-015.T2 | Trailer detection: read `TE` request header; if it contains `trailers` and uvicorn supports outbound trailers, emit `X-Chunks` + `X-Total-Duration-Ms` as trailers at end-of-stream. Otherwise omit. |
| S-015.T3 | Synthesis pipeline must yield bytes chunk-by-chunk without buffering the full audio (use the existing chunker from S-013, just don't buffer the join). |
| S-015.T4 | Tests: streaming path, trailer-supported path, trailer-omitted path, first-byte-before-half-duration assertion (under a slowed-down provider). |

---

### S-016 — Client-disconnect cancellation

**Type:** Technical
**Status:** DONE
**Depends on:** S-013 (Step 1), S-007 (DONE)
**Refs:** FR-CC-05
**Why selected:** Closes the concurrency contract — semaphores must release on client disconnect to prevent queue starvation under unreliable clients.

**Acceptance criteria:**
- Client drops connection at 1 s during a >5 s synthesis; further chunks stop at the next boundary; logs note the cancellation (UAT-CC-04).
- No orphan temp files remain after disconnection.
- Concurrency + queue semaphores released within the same chunk-boundary window.

**Atomic tasks:**

| Task | Purpose |
|---|---|
| S-016.T1 | Disconnect detection: FastAPI `Request.is_disconnected()` polled at chunk boundaries inside the synthesis loop. |
| S-016.T2 | On disconnect: stop further chunk synthesis at the next boundary; raise `asyncio.CancelledError` or use a cooperative flag, depending on the chunker shape. |
| S-016.T3 | Cleanup: `finally` block releases concurrency_semaphore + queue_semaphore + per-model lock; removes temp files. (S-013 already establishes the `finally` discipline; S-016 ensures it triggers correctly on disconnect.) |
| S-016.T4 | Tests: UAT-CC-04 — long synthesis + client drop at 1s; assert no orphan temp files + semaphores back to baseline `_value`. |

---

## Sprint-wide testing & verification

- All five CI gates remain green: ruff (check + format), `mypy --strict src/`, `pytest --cov-fail-under=83`, `pip-audit`.
- Coverage hold target ≥85% (current master 85.27%).
- Manual smoke: hit the new endpoint with a CRUD-created voice; confirm headers + audio body; hit with `stream=true` + curl + monitor `--trace-time` for chunk arrival.

## Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| S-013 too big for one engineer session (rate limit / token budget) | Medium | Medium | Worktree-isolation means a fresh engineer can pick up uncommitted work. Engineer prompt explicitly says "if you stall, commit what you have and signal STALLED; coordinator can dispatch a continuation." |
| Step 2 conflicts on the router file (`routers/synthesize.py`) | Medium | Low | Both stories add hooks at different lifecycle points; merge conflicts will be additive (same playbook as Sprint 3 Step 2). |
| Streaming × cancellation runtime interaction | Medium | Medium | Coordinator runs a final integration smoke test after Step 2 merges: a streamed request that's cancelled mid-stream must release semaphores AND not corrupt the partial response. |
| OpenAI `/v1/audio/speech` regression while reshaping voice resolution | Medium | High | S-013.T9 explicitly verifies the existing 24 OpenAI-endpoint tests still pass before merge. |

## Stories NOT in this sprint

- **S-017 (OpenAI adapter)**: Sprint 5; depends on S-013 + S-015 stable.
- **S-018 (byte-identity paired UAT)**: Sprint 5; depends on S-017.
- **S-019 (docs refresh)**, **S-020 (Dockerfile + CUDA variant)**, **S-021 (perf validation)**: Sprint 6.

## Definition of Done (Sprint 4)

- All three stories' acceptance criteria met.
- All CI gates green on `master` after merge.
- `POST /v1/tts/synthesize` end-to-end exercised by UAT-EP-01..07.
- `stream=true` produces chunked output with first-byte < half-duration.
- Disconnect at 1 s of a 5 s synthesis releases semaphores within the chunk-boundary window.
- Sprint review document at `docs/planning/sprints/sprint-review-4.md`.
- Existing `/v1/audio/speech` continues to work — no regression to its tests.
