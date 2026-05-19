# Sprint 6: Cycle close-out — docs, container, perf validation, dedup

> Source: docs/planning/journal.md
> SRS: docs/specs/software-spec.md
> FRS: docs/specs/analyst-frs.md
> NFR: docs/specs/writer-nfr.md
> UAT: docs/specs/analyst-UAT.md
> Author: Sprint Planner (AI-assisted)
> Date: 2026-05-19
> Status: DONE
> Version: 1.0

## 1. Sprint Objective

Close the llm-tts-api improvement cycle by **proving the implementation against its own contract from the outside in** and shipping a polished codebase. Three parallel close-out tracks land first — **S-019** (README + diagrams + OpenAPI refreshed against current master, quality-matched to the sibling `llm-image-api/docs/` layout), **S-020** (two-variant Dockerfile + CUDA build with CI smoke-tests), and **S-021** (perf re-baseline against S-002's numbers under +10% regression budget). Then **S-026** (behavior-preserving code-duplication refactor) runs as the cycle-end terminal step, gated by S-018's byte-identity UAT and all other Sprint-5 invariants.

## 2. Value Statement

After Sprint 5, the code is correct and tested but the *outer surface* (docs, container, perf claim) and the *internal cleanliness* (accidental duplication accumulated across 5 sprints) have not been touched since Sprint 1's foundation. This is the last sprint of the cycle — anything not landed here ships rough. The parallel close-out (S-019/S-020/S-021) gives the external view; S-026 leaves the internal codebase smaller than it found it without changing observable behavior. Together they make the cycle's output a complete, defensible deliverable rather than a "works on my machine" milestone.

## 3. Sprint Summary

| Metric | Value |
|--------|-------|
| Stories | 4 |
| User stories | 0 |
| Technical stories | 4 (S-019, S-020, S-021, S-026) |
| Total tasks | 19 |
| Parallel tracks | 3 (Step 1: S-019 ∥ S-020 ∥ S-021) → 1 (Step 2: S-026 alone) |

## 4. Execution Order

| Step | Stories | Can start after |
|------|---------|----------------|
| 1 | S-019, S-020, S-021 | Immediately (no intra-sprint deps; all upstream deps are DONE) |
| 2 | S-026 | Step 1 complete — S-026 explicitly depends on S-019 (docs reflect master), S-020 (Dockerfile reflects master), S-021 (perf claim recorded) so the refactor lands on a quiescent surface |

Service-boundary check: S-019 (docs), S-020 (container), S-021 (perf test) modify disjoint file sets — no producer/consumer pairs in Step 1. S-026 is the consumer of all three in Step 2 because dedup decisions must respect the docs (no docs drift), the container manifest (no startup regression), and the perf budget (no hot-path regression).

## 5. Stories

### S-019: Documentation refresh
- **Status:** DONE
- **Type:** Technical
- **Parallel with:** S-020, S-021 (Step 1)
- **Depends on (intra-sprint):** None (all upstream deps DONE)
- **Refs:** FR-DC-01..03, NFR-MT-06, NFR-CP-01, NFR-PV-04, UAT-DC-01..03, UAT-CF-04
- **Architecture:** SRS §4.13 documentation, §5 sizing recommendations (resolution C-1), §6 handler topology
- **Quality bar:** `/Volumes/Coding/Projects/Applications/epub/llm-image-api/docs/` — particularly the `README.md` depth, `docs/diagrams/{class,sequence}/*.md` Mermaid style + per-component split, and `docs/openapi/openapi.yaml` shape. Mirror that structure; don't copy content.

#### Tasks

| # | Task | Purpose | Parallel | Status | Refs |
|---|------|---------|----------|--------|------|
| 1 | README sections refresh | Update top-level `README.md` to current Sprint-5 state. Required sections: Hardware Auto-Detection (S-005 rules), full env-var inventory (S-012 — including TTS_VOICE_* and TTS_REFAUDIO_MAX_BYTES from Sprint 3), Rich endpoint (`POST /v1/tts/synthesize`) examples with full header inventory, Voice-CRUD endpoints under `/v1/tts/voices/*` with consent attestation note, seed-ingestion mechanism + `voice_map.json` legacy contract, storage-backend selection matrix (FS default vs `[postgres]`/`[s3]` extras), Error taxonomy table sourced from `src/llm_tts_api/errors.py`, **Voice biometric notice** verbatim per NFR-CP-01/NFR-PV-04, Sizing recommendations resolving SRS §5 C-1, link to `docs/perf/baseline.md` Performance section. | Yes (independent surface) | DONE | UAT-DC-01, UAT-CF-04, NFR-CP-01, NFR-PV-04 |
| 2 | Class diagrams refresh | Refresh `docs/diagrams/class/*.md` to current code. Add a new `voice-store.md` (FR-VS-01..12 producer/consumer Protocols + FS/Postgres/S3 backends). Update `overview.md` (router topology: `synthesize.py` + `audio.py` both delegate to `services/synthesize_service.py::synthesize_core`). Update `config-and-schemas.md` for new env vars + `SynthesizeRequest`. Update `providers.md` for the auto-selection capability table (S-006). All Mermaid syntax; match the per-component split llm-image-api uses (no monolithic mega-diagram). | Yes (independent files) | DONE | FR-DC-02 |
| 3 | Sequence diagrams refresh | Update + add `docs/diagrams/sequence/*.md` to current code. Refresh: `startup.md` (lifespan + warmup + ready toggle from S-003/S-010), `health-and-ready.md` (S-010 lock-free `/health`, drain semantics), `create-speech.md` (now a thin translator over `synthesize_core` per S-017). Add new: `synthesize-rich.md` (POST /v1/tts/synthesize buffered AND streamed paths from S-013/S-015), `voice-crud.md` (POST/GET/PUT/DELETE under /v1/tts/voices/* from S-025), `voice-seed-ingestion.md` (S-011 idempotent ingest + watchfiles reload). | Yes (independent files) | DONE | UAT-DC-02 |
| 4 | OpenAPI spec refresh | Refresh `docs/openapi/openapi.yaml` (or split by surface like llm-image-api's pattern). Must cover `/v1/tts/synthesize`, `/v1/tts/voices/*` (POST/GET list/GET one/GET audio/PUT/DELETE), `/v1/audio/speech` (OpenAI shape, unchanged from upstream), `/v1/models`, `/health`, `/ready`. Schema definitions: `SynthesizeRequest`, `VoiceRecord`, `Error` envelope (matches `errors.py` taxonomy). Validate with a syntax check (`openapi-spec-validator` or equivalent stdlib YAML lint). | Yes (independent file) | DONE | UAT-DC-03 |
| 5 | Cross-reference smoke test | Single new test (`tests/test_docs_inventory.py`) that programmatically asserts: (a) every env var name in `Settings.__post_init__` appears in `README.md` (UAT-CF-04), (b) every error-taxonomy `(type, code)` pair from `errors.py` appears in `README.md`, (c) every router prefix mounted by `create_app` has a matching OpenAPI `paths:` entry. Lightweight — runs in the standard unit suite. | No (depends on T1+T4) | DONE | UAT-DC-01, UAT-CF-04 |

#### Acceptance Criteria
- README contains all required sections (UAT-DC-01); every new env var is documented (UAT-CF-04).
- Sequence diagrams reflect the new lifespan/singleton flow + new rich endpoint + voice CRUD + seed ingestion (UAT-DC-02).
- OpenAPI spec exercises all three external surfaces (rich, voices, OpenAI adapter) + `/v1/models` + lifecycle endpoints (UAT-DC-03).
- README has the biometric notice section verbatim per NFR-CP-01.
- `tests/test_docs_inventory.py` passes (env vars + errors + router prefixes all reflected in README/OpenAPI).

#### Testing & Verification
Standard gates (ruff, ruff format, mypy --strict, pytest, pip-audit) plus the new docs-inventory test. Manual visual check on README rendering. OpenAPI YAML validated via a lightweight syntax check that fails CI if shape is broken.

---

### S-020: Dockerfile + CI docker build update
- **Status:** DONE
- **Type:** Technical
- **Parallel with:** S-019, S-021 (Step 1)
- **Depends on (intra-sprint):** None
- **Refs:** NFR-OP-02, FR-QG-04, UAT-QG-05, OQ-5 (two variants: default + CUDA)
- **Architecture:** SRS §10 (deployment + sizing), §4.8 lifecycle (SIGTERM drain from S-010)

#### Tasks

| # | Task | Purpose | Parallel | Status | Refs |
|---|------|---------|----------|--------|------|
| 1 | Default `Dockerfile` refresh | Multi-stage build: builder isolates compiler toolchain; final image is slim Python 3.13, runs as non-root. Pinned base-image digest (no floating tags). Configuration by env vars (S-012 inventory) + volumes for `TTS_VOICE_STORE_DIR` and `TTS_VOICE_MAP_FILE`. `EXPOSE` the service port; `HEALTHCHECK` calls `/health`. Entrypoint launches uvicorn under tini (or `--use-colors=false` discipline so logs format cleanly per S-004). SIGTERM drain per S-010 — `TTS_SHUTDOWN_DRAIN_SECONDS` honored. | Yes (independent file) | DONE | NFR-OP-02, FR-QG-04 |
| 2 | `Dockerfile.cuda` variant | CUDA-enabled base image (`nvidia/cuda:*-cudnn-runtime` family), pinned digest. CUDA torch wheel installed in builder stage; final image slim. Same env-var + volume + healthcheck + drain contract as the default. Default device override: `TTS_DEVICE=cuda` at runtime. Reject startup with `provider_error.no_viable_provider` if no CUDA-supporting provider is available (S-006). | Yes (independent file) | DONE | NFR-OP-02, OQ-5 |
| 3 | CI build + smoke job for both variants | Add a `.github/workflows/docker.yml` (or extend existing CI). Build both images on PR + push. Smoke per image: start container, wait for `/health` returns 200 within 60 s, then SIGTERM and assert drain exits 0 within `TTS_SHUTDOWN_DRAIN_SECONDS`. Tag as `:ci` for the build, do not push to a registry. | No (consumes T1+T2) | DONE | UAT-QG-05, FR-QG-04 |
| 4 | `.dockerignore` + image-size hygiene | New `.dockerignore` excludes `.git/`, `.worktrees/`, `tests/`, `docs/planning/`, `var/`, `.pending/`, etc. Aim for final image size under a reasonable threshold (record in PR). | Yes (independent file) | DONE | NFR-OP-02 |

#### Acceptance Criteria
- `docker build -t llm-tts-api:ci .` and `docker build -f Dockerfile.cuda -t llm-tts-api:ci-cuda .` both succeed in CI (UAT-QG-05).
- Each built image starts and `/health` returns 200 within 60 s.
- Each image runs as non-root; final stage contains no compiler toolchain.
- Voice map + voice store readable from a mounted volume; container restart picks up changes.
- SIGTERM triggers graceful drain (S-010) — container exits 0 within `TTS_SHUTDOWN_DRAIN_SECONDS`.

#### Testing & Verification
CI builds both images and runs the smoke script (start, hit `/health`, SIGTERM, assert exit code). Local verification optional — CI is the gate.

---

### S-021: Performance validation against baseline
- **Status:** DONE
- **Type:** Technical
- **Parallel with:** S-019, S-020 (Step 1)
- **Depends on (intra-sprint):** None
- **Refs:** NFR-PF-01..04, RISK-2, UAT-CC-01..02
- **Architecture:** SRS §5 G-1 (RISK-8 relaxation already pinned in `docs/perf/baseline.md` from S-018)

#### Tasks

| # | Task | Purpose | Parallel | Status | Refs |
|---|------|---------|----------|--------|------|
| 1 | Re-run baseline scenario on rich endpoint | Use the same input (`tests/perf/fixtures/baseline_input.txt` from S-002), same voice `alloy`, same provider/model. Drive `POST /v1/tts/synthesize` via `scripts/perf_baseline.py` (already in repo). Record p50, p95, p99 latency and the commit SHA + date. | Yes (independent run) | DONE | NFR-PF-01 |
| 2 | Re-run baseline scenario on OpenAI adapter | Same fixture, same voice, same model, but drive `POST /v1/audio/speech` (the S-017 thin translator path). Record the same metrics. The OpenAI path's wall-clock should match the rich path within noise — they share `synthesize_core`. | Yes (independent run) | DONE | NFR-PF-01, NFR-PT-03 |
| 3 | `/health` responsiveness under load (NFR-PF-02) | Background a single synthesis request, in parallel hammer `/health` 100× and record p95 latency. Asserts ≤50 ms p95 per NFR-PF-02 / UAT-CC-02. | No (sequential with T1/T2 worker contention) | DONE | NFR-PF-02, UAT-CC-02 |
| 4 | Concurrent throughput check | Spawn 4 parallel synthesis requests with `TTS_MAX_CONCURRENT_REQUESTS=2`. Assert total wall-clock is within ±20% of `2 × single-request time` (UAT-CC-01). | No (sequential) | DONE | UAT-CC-01 |
| 5 | Record numbers + regression verdict | Append post-cycle p50/p95/p99 rows to `docs/perf/baseline.md` under a new "Sprint 6 post-cycle measurement" section, alongside the existing Sprint-1 baseline row. Compute regression vs Sprint-1 row; assert ≤+10% on p50 and p95. Document any methodology drift (e.g. warm-up runs, hardware). | No (consumes T1–T4) | DONE | NFR-PF-01, RISK-2 |

#### Acceptance Criteria
- Rich + OpenAI p50 and p95 within +10% of S-002 baseline numbers.
- `/health` p95 ≤50 ms during in-flight synthesis.
- Concurrent-throughput check passes within ±20%.
- Post-cycle numbers + date + commit SHA appended to `docs/perf/baseline.md`.

#### Testing & Verification
Re-uses `scripts/perf_baseline.py` (S-002) for the measurement primitive. New `tests/test_perf_regression.py` may be added as a "smoke" version with relaxed bounds for CI; the strict measurement is operator-driven and recorded in `docs/perf/baseline.md`. If S-002 baseline row is still `_pending_` (operator-action item from Sprint 1), this story FIRST captures the Sprint-1 numbers as the missing baseline, THEN adds the Sprint-6 row — the perf claim is internally consistent rather than blocked.

---

### S-026: Code-duplication refactor (cycle-end cleanup)
- **Status:** DONE
- **Type:** Technical
- **Parallel with:** None within this sprint
- **Depends on (intra-sprint):** S-019, S-020, S-021 (cycle-end terminal — runs LAST)
- **Refs:** NFR-MT-01..04, BR-9, NFR-PT-03
- **Architecture:** Behavior-preserving — touches no external surface; constraints are inward (smaller, more uniform internal code)

#### Tasks

| # | Task | Purpose | Parallel | Status | Refs |
|---|------|---------|----------|--------|------|
| 1 | Duplication inventory | Survey `src/llm_tts_api/` for accidental duplication. Concrete suspects from Sprints 1–5: (a) the `X-*` header inventory is constructed in both `synthesize.py` and `synthesize_service.py`; (b) error-envelope construction is repeated across routers; (c) voice-id regex + path validation is in `records.py` AND inline in `fs_blob.py`; (d) provider-allow-list extraction (`settings.tts_*_model_allowed`) is iterated in 3+ sites; (e) test fixtures `_seed_voice` and `_stub_app_state` overlap. Record findings in impl notes BEFORE touching code. | No (foundation) | DONE | NFR-MT-01 |
| 2 | Consolidate header inventory | Move the `X-*` header set to a single module-level constant in `synthesize_service.py` (or new `services/headers.py`). Both rich-endpoint and OpenAI paths consume from the same source; S-017's `_RICH_ONLY_HEADERS` becomes a derived view. Net effect: deleting one site, both still produce the same headers. | Yes (with T3/T4 — independent surface) | DONE | NFR-MT-02, NFR-PT-03 |
| 3 | Consolidate error-envelope helpers | If `errors.py` has any redundant construction logic vs router-level helpers, merge. Keep the OpenAI-compatible envelope shape byte-identical. | Yes | DONE | BR-9, NFR-MT-02 |
| 4 | Consolidate voice-id validation | Move the `[a-z0-9_-]{1,64}` regex + the path-traversal guard into one helper in `records.py` (or `voice_store/validation.py`); inline copies in repository implementations call it. | Yes | DONE | NFR-MT-02, NFR-SE-03 |
| 5 | Consolidate allow-list scattering | Single accessor (`Settings.allowed_models_for(provider)` or `ModelRegistry.allowed_for(provider)`) replaces hardcoded `settings.tts_mlx_audio_model_allowed` / `settings.tts_voxtral_model_allowed` reads. | Yes | DONE | NFR-MT-02 |
| 6 | Test-fixture dedup | If `_seed_voice` (from `test_openai_adapter.py` and elsewhere) and `_stub_app_state` (`conftest.py`) overlap, merge into `tests/fakes/`. Importable from both. | Yes | DONE | NFR-MT-02 |
| 7 | LOC + behavior gates | Measure: `tokei src/llm_tts_api/` before vs after; require ≥3% net production LOC reduction. Re-run `uv run pytest` — S-018 byte-identity test must pass UNCHANGED (no test modifications allowed there). Verify `docs/openapi/openapi.yaml` is byte-identical OR diff is purely cosmetic (with explicit per-line justification). | No (verifies T2–T6) | DONE | NFR-MT-01..04, BR-9 |

#### Acceptance Criteria
- Net production LOC reduction ≥ 3% vs Step-1-end master (measured `tokei` or `cloc` on `src/llm_tts_api/`).
- All gates green: ruff, ruff format, `mypy --strict src/`, pytest, pip-audit.
- S-018 byte-identity paired UAT passes unchanged (no edits to `tests/test_openai_adapter_parity.py`).
- No new dependencies introduced.
- No public API or response-shape changes (`docs/openapi/openapi.yaml` byte-identical OR diff is purely cosmetic — comments, ordering — with per-line justification).
- Per-consolidation rationale recorded in implementation notes (what was duplicated, where it lives now, what behavior is preserved).

#### Testing & Verification
Existing 375+ test suite is the regression gate. No new tests required (refactor doesn't change behavior); if a consolidation makes a test redundant, the test is removed only with explicit justification in the impl notes. The hard gate is "S-018 paired UAT passes byte-identically" — that test pins both endpoints' output bytes, so any internal reorganization that breaks the synthesis pipeline fails it.

---

## 6. References

- [SRS](../../specs/software-spec.md) — §4.13 docs, §5 sizing, §10 deployment, §4.8 lifecycle
- [FRS](../../specs/analyst-frs.md) — FR-DC-01..03, FR-QG-04
- [NFR](../../specs/writer-nfr.md) — NFR-MT-01..06, NFR-OP-02, NFR-PF-01..04, NFR-CP-01, NFR-PV-04
- [UAT](../../specs/analyst-UAT.md) — UAT-DC-01..03, UAT-CF-04, UAT-QG-05, UAT-CC-01..02
- [Journal](../journal.md) — Stories: S-019, S-020, S-021, S-026
- [Sibling project — quality bar for docs](../../../../llm-image-api/docs/) — README depth + diagram split + OpenAPI shape

## 7. Risks & Dependencies

| Risk/Dependency | Affected Stories | Mitigation |
|----------------|-----------------|------------|
| S-002 baseline numbers row may still be `_pending_` (operator action) | S-021 | T1 in S-021 first captures the Sprint-1 numbers, then computes regression — perf claim is internally consistent rather than blocked on prior operator work. |
| CI machine may lack docker daemon | S-020 | If CI cannot run docker, mark the docker job optional with manual operator instructions in impl notes. UAT-QG-05 then verified out-of-band on a dev host. |
| S-026 LOC reduction may be hard to hit if codebase is already lean | S-026 | T1 inventory phase records candidates BEFORE coding; if total LOC saving < 3%, the inventory is the deliverable (with explicit rationale) and the threshold is documented as "as-found" rather than failing the story. |
| S-026 may accidentally regress S-018 byte-identity | S-026 | T7 re-runs S-018 paired UAT before commit; gate is `git status` clean on `tests/test_openai_adapter_parity.py`. |
| Docs drift between S-019 (parallel) and S-026 (terminal) | S-019, S-026 | S-026's gate "OpenAPI byte-identical" prevents silent drift. README inventory test in S-019 (T5) catches new env vars from S-026 if any are introduced (they shouldn't be). |
| Sprint 5's xfailed test (TestClient streaming buffering) may interact with S-021's perf scenario | S-021 | S-021 uses `scripts/perf_baseline.py` against a real uvicorn process, not TestClient — out-of-process measurement bypasses the xfail entirely. |
