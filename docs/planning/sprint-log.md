# Sprint Log — llm-tts-api improvement cycle

Tracks sprints planned and their disposition.

| Sprint | Title | Stories | Status | Planned | Started | Completed |
|---|---|---|---|---|---|---|
| 1 | Foundation — observability, lifespan, hardware-aware bootstrap | S-001, S-002, S-003, S-004, S-005 | DONE | 2026-05-17 | 2026-05-17 | 2026-05-17 |
| 2 | Core services — async concurrency, model cache, error taxonomy, lifecycle | S-006, S-007, S-008, S-009, S-010, S-012 | DONE | 2026-05-17 | 2026-05-17 | 2026-05-18 |
| 3 | Voice store — repositories, optional backends, CRUD, seed ingestion | S-022, S-023, S-024, S-025, S-011 | PLANNED | 2026-05-18 | — | — |

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
