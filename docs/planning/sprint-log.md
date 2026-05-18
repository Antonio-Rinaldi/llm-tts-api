# Sprint Log — llm-tts-api improvement cycle

Tracks sprints planned and their disposition.

| Sprint | Title | Stories | Status | Planned | Started | Completed |
|---|---|---|---|---|---|---|
| 1 | Foundation — observability, lifespan, hardware-aware bootstrap | S-001, S-002, S-003, S-004, S-005 | DONE | 2026-05-17 | 2026-05-17 | 2026-05-17 |
| 2 | Core services — async concurrency, model cache, error taxonomy, lifecycle | S-006, S-007, S-008, S-009, S-010, S-012 | DONE | 2026-05-17 | 2026-05-17 | 2026-05-18 |
| 3 | Voice store — repositories, optional backends, CRUD, seed ingestion | S-022, S-023, S-024, S-025, S-011 | DONE | 2026-05-18 | 2026-05-18 | 2026-05-18 |
| 4 | Rich endpoint surface — synthesize + streaming + cancellation | S-013, S-015, S-016 | DONE | 2026-05-18 | 2026-05-18 | 2026-05-18 |
| 5 | OpenAI adapter + byte-identity equivalence | S-017, S-018 | DONE | 2026-05-18 | 2026-05-18 | 2026-05-19 |
| 6 | Cycle close-out — docs, container, perf validation, dedup | S-019, S-020, S-021, S-026 | PLANNED | 2026-05-19 | — | — |

## Sprint 6 — summary

**Objective:** close the cycle by proving the implementation against its own contract from the outside in (docs, container, perf) and shipping a polished, dedup'd codebase.

**Composition:** 4 stories, 2 execution steps. Step 1: S-019 (docs refresh — README + diagrams + OpenAPI, quality-matched to llm-image-api/docs layout) + S-020 (Dockerfile default + CUDA variant + CI smoke) + S-021 (perf re-baseline vs S-002, ≤+10% regression budget) in parallel. Step 2: S-026 (behavior-preserving code-duplication refactor — ≥3% net LOC reduction, gated by S-018 byte-identity UAT) alone.

**Provability:** all Sprint-1 quality gates green; CI builds + smokes both Docker variants; perf numbers within +10% of S-002 baseline; post-refactor LOC down ≥3% with S-018 paired UAT unchanged.

**Risks:** S-002 baseline numbers may still be `_pending_` — S-021 T1 captures Sprint-1 numbers first so the regression check is internally consistent; CI docker daemon availability; S-026 LOC reduction may be modest — inventory phase records candidates first; S-026 byte-identity gate prevents silent regression.

**Detail:** `docs/planning/sprints/sprint-6.md`.

## Sprint 5 — summary

**Objective:** reduce `POST /v1/audio/speech` to a thin OpenAI-shaped translator over the Sprint-4 rich endpoint and prove the translation is byte-faithful on a warm model. Collapses the dual synthesis pipeline (BR-9) and unblocks Sprint 6 polish.

**Composition:** 2 stories, 2 execution steps (strictly serial). Step 1: S-017 alone (adapter — producer of the translation contract). Step 2: S-018 alone (paired UAT — consumer of that contract). Service-boundary rule forces the split.

**Provability:** OpenAI request/response shape unchanged (UAT-OA-01..04); handler stays ≤30 LOC of translation with no `SpeechSynthesizer` bypass; paired sha256 byte-identity holds for at least one warm provider/model (UAT-OA-05) with documented RISK-8 relaxation fallback.

**Risks:** RISK-8 (provider non-determinism) — relaxation path baked into S-018 T3; OpenAI-contract leakage of rich-endpoint headers — explicit T3 strip + test in S-017; adapter LOC creep — AST/grep gate.

**Detail:** `docs/planning/sprints/sprint-5.md`.

## Sprint 4 — summary

**Objective:** ship `POST /v1/tts/synthesize` — the rich endpoint that consumes everything Sprints 1–3 built (voice store, provider auto-selection, concurrency, model cache, error envelope, config). Streaming responses with headers/trailers and client-disconnect cancellation round out the surface so Sprint 5's OpenAI adapter has a stable layer to translate to.

**Composition:** 3 stories, 2 execution steps. Step 1: S-013 alone (foundation). Step 2: S-015 + S-016 in parallel (both extend S-013, touch different concerns — response-side streaming vs request-side cancellation).

**Provability:** map-voice synthesis + full header inventory; streaming first-byte < half-duration; client-disconnect releases semaphores at next chunk boundary; existing `/v1/audio/speech` regression-free.

**Risks:** S-013 size vs engineer session budget; Step-2 router-file conflicts; streaming×cancellation interaction; OpenAI endpoint regression during voice-resolution reshape.

**Detail:** `docs/planning/sprints/sprint-4.md`.

## Sprint 3 — summary

**Objective:** land the OQ-3-derived voice store end-to-end — repository Protocols + FS defaults, optional Postgres/S3 backends, CRUD endpoints under `/v1/tts/voices/*`, and idempotent seed ingestion from `voice_map.json`.

**Composition:** 5 stories, 3 execution steps. Step 1: S-022 alone (foundation). Step 2: S-023 + S-024 + S-025 in parallel (all consume S-022's Protocols). Step 3: S-011 alone (consumer of S-022 + S-025).

**Provability:** `POST /v1/tts/voices` end-to-end CRUD on the FS default; `pip install .[postgres]` / `.[s3]` enables alternates; selecting an alternate without the extra fails startup with `config_error.missing_extra`; `voice_map.json` ingestion idempotent across restarts.

**Risks:** engineer-commit gotcha (Sprint 2 Step 1 pattern) — mitigated by SKILL.md Step 2.5 boilerplate; Step-2 conflicts on shared `dependencies.py`; `watchfiles` in Docker (RISK-3).

**Detail:** `docs/planning/sprints/sprint-3.md`.

## Sprint 2 — summary

**Objective:** build the runtime backbone the rich endpoint sits on — auto-selecting provider, async-correct concurrency, LRU model cache, typed error envelope, env-config inventory, and real `/health`/`/ready` lifecycle.

**Composition:** 6 stories (all Technical), 2 execution steps. Step 1 = 5 parallel stories (S-006, S-007, S-008, S-009, S-012). Step 2 = S-010 alone (consumer of S-007's semaphore slots).

**Provability:** event-loop stays responsive under in-flight synthesis (NFR-PF-02), excess concurrency returns `429 capacity_error.queue_full`, model-cache swap behavior, typed error envelope on every error response, `/ready` reflects warmup + drain accurately.

**Risks:** RISK-2 (S-007 async refactor scope vs no-rewrite), RISK-1 (S-006 provider capability declaration).

**Detail:** `docs/planning/sprints/sprint-2.md`.

## Sprint 1 — summary

**Objective:** establish the engineering foundation every subsequent sprint stands on — CI gate, perf baseline, FastAPI lifespan + `app.state`, request-id + structured logging, hardware detection.

**Composition:** 5 stories (all Technical), all from Journal Group A, all parallel-safe.

**Provability:** CI green on the new gates, `docs/perf/baseline.md` committed, no module-level singletons survive, request_id appears in logs and response headers, device detection unit-tested across MPS/CUDA/CPU branches.

**Risks:** mypy-strict reveals more debt than expected (mitigated by one-time bulk ignores); lifespan refactor breaks tests in subtle ways (mitigated by per-PR test-fixture coupling).

**Detail:** `docs/planning/sprints/sprint-1.md`.

## Roadmap (provisional — subject to revision per sprint outcome)

| Sprint | Theme | Likely stories |
|---|---|---|
| 2 | Core services (concurrency, errors, lifecycle, config) | S-006, S-007, S-008, S-009, S-010, S-012 |
| 3 | Voice store (repositories + CRUD + seed ingestion) | S-022, S-023, S-024, S-025, S-011 |
| 4 | Rich endpoint surface | S-013, S-015, S-016 |
| 5 | OpenAI adapter + equivalence | S-017, S-018 |
| 6 | Polish — docs, Dockerfile (default + CUDA), perf validation | S-019, S-020, S-021 |

Notes on the roadmap:
- Sprint 2 is on the edge of "right-sized" (6 stories); may be split if S-007 (async refactor, RISK-2) proves invasive.
- Sprint 3 is the new biggest cluster post-OQ-3 (voice CRUD pulled into cycle); S-023 and S-024 (optional backends) are split-points if scope tightens.
- Sprint 5 is small (2 stories) by design — paired byte-identity testing (S-018) deserves focus.
