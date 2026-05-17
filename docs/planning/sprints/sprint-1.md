# Sprint 1 — Foundation: observability, lifespan, hardware-aware bootstrap

**Status:** PLANNED
**Planned:** 2026-05-17
**Stories:** S-001, S-002, S-003, S-004, S-005 (Journal Group A — all parallel-safe, zero inter-dependency)
**Cycle:** llm-tts-api improvement cycle, Sprint 1 of N
**Source docs:** `docs/specs/software-spec.md`, `docs/specs/analyst-frs.md`, `docs/specs/writer-nfr.md`, `docs/planning/journal.md`

---

## Objective

Establish the engineering foundation every subsequent sprint will stand on:

- A working **CI quality gate** with ruff, strict mypy, pytest-cov (ratchet), and pip-audit.
- A recorded **performance baseline** that NFR-PF-01 will be compared against.
- A **FastAPI lifespan + `app.state` refactor** that retires module-level singletons.
- **Request-correlated structured logging** with `X-Request-ID` propagation.
- A **hardware-detection module** mirroring the llm-image-api pattern (MPS → CUDA → CPU + dtype) with env overrides.

By end of sprint, the codebase is observably refactored without changing user-facing behavior. The next sprint can build the rich endpoint, the voice store, and the async concurrency model on top.

## Provability

The sprint proves itself when:
- CI is green on a representative PR exercising all four gates.
- `docs/perf/baseline.md` exists with reproducible numbers.
- `app.state` carries the singletons; no module-level `@lru_cache` survives in the synthesis path.
- Every request log line carries `request_id`; opt-in JSON format works.
- `detect_device()` returns correct values on Apple Silicon, with monkeypatched-torch tests proving CUDA + CPU branches.

## Constraints carried from SRS / NFR

- **No user-facing behavior change** — existing `/v1/audio/speech` still works identically.
- **No performance regression** — S-002 captures the baseline against current code; S-021 (later sprint) re-runs against the refactored path.
- **No new external services** — confirmed by sprint scope (foundation only).
- **MLX-audio remains primary** — device detection adds no provider work this sprint; that's S-006.

---

## Execution Order

All five stories run **fully in parallel** — zero inter-dependencies.

```
┌─────── Parallel step 1 (all five in flight) ───────┐
│  S-001  S-002  S-003  S-004  S-005                 │
└─────────────────────────────────────────────────────┘
```

**Service boundary check:** none of these stories cross a service boundary with another sprint story. S-003 exposes `app.state` slots as the future foundation for S-004's middleware, but S-004's middleware only reads its own request-id contextvar (it does not consume any `app.state` slot owned by S-003). Both can land in either order without contract churn.

---

## Stories & Atomic Tasks

### S-001 — CI quality gate scaffolding (ratchet)

**Type:** Technical
**Status:** DONE
**Depends on:** none
**Refs:** FR-QG-01..04, NFR-MT-01..04, NFR-SE-05
**Why selected:** every subsequent PR must be type-checked, lint-gated, coverage-tracked, and dependency-audited from this point on.

**Acceptance criteria:**
- CI workflow exists and runs on PRs and main.
- `ruff check` and `ruff format --check` pass on `src/` and `tests/`.
- `mypy --strict src/` passes (zero errors).
- Coverage threshold set to current measured level on day 1; ratchet upward per PR; end-of-cycle target ≥ 80%.
- `pip-audit` runs and fails on configured severity threshold.
- `py.typed` marker shipped with the package.

**Atomic tasks:**

| Task | Purpose | Parallel-safe within story | Refs |
|---|---|---|---|
| S-001.T1 | Author `.github/workflows/ci.yml` (or equivalent) with jobs for lint/format, types, tests, audit | yes | FR-QG-01 |
| S-001.T2 | Ruff configuration in `pyproject.toml` (rule selection E,F,I,UP,B,SIM; line-length; target-version) + apply auto-fixes | yes | NFR-MT-01 |
| S-001.T3 | `mypy --strict` configuration in `pyproject.toml`; fix or `# type: ignore[reason]` outstanding errors | yes | FR-QG-03, NFR-MT-03 |
| S-001.T4 | Measure baseline coverage; set `--cov-fail-under=<current>`; document ratchet protocol in README/contributing | sequential after T1 | NFR-MT-02 |
| S-001.T5 | Add `pip-audit` step with severity threshold; document the threshold | yes | NFR-SE-05 |
| S-001.T6 | Add `py.typed` marker file and include it in package data | yes | NFR-MT-03 |

**Testing & verification:** the CI run on the first PR through the new workflow is itself the test. Smoke check: introduce a deliberate ruff/mypy/coverage violation, confirm CI fails; revert, confirm CI passes.

---

### S-002 — Baseline performance capture

**Type:** Technical
**Status:** DONE (scaffolding) / BLOCKED-ON-USER (final measurement row)
**Depends on:** none
**Refs:** NFR-PF-01, A-7
**Why selected:** the baseline MUST be captured against the current (pre-refactor) code, or NFR-PF-01 acceptance becomes unverifiable in Sprint 5+.

**Acceptance criteria:**
- `docs/perf/baseline.md` exists with the input text, voice id, host spec, methodology, p50 and p95 latency, and timestamp.
- Methodology is reproducible: commit SHA recorded; measurement script or REST fixture referenced.
- README's Performance section (introduced in S-019) will later reference this file.

**Atomic tasks:**

| Task | Purpose | Parallel-safe within story | Refs |
|---|---|---|---|
| S-002.T1 | Write a measurement script (e.g. `scripts/perf_baseline.py`) that POSTs to `/v1/audio/speech` N times and computes p50/p95 | yes | NFR-PF-01 |
| S-002.T2 | Define the reference input fixture (e.g. `tests/perf/fixtures/baseline_input.txt`, ~500 chars Italian) | yes | NFR-PF-01 |
| S-002.T3 | Run on the reference Apple Silicon host; write `docs/perf/baseline.md` with numbers, methodology, commit SHA | sequential after T1+T2 | A-7 |

**Testing & verification:** the script is rerunnable. A second run on the same commit should produce numbers within noise (~±5%).

---

### S-003 — Lifespan + `app.state` singletons

**Type:** Technical
**Status:** DONE
**Depends on:** none
**Refs:** FR-HL-03, NFR-OP-01, A-2, RISK-2
**Why selected:** every Group B and C story depends on this. Refactoring bootstrap now (before async, before voice store, before rich endpoint) minimizes downstream churn.

**Acceptance criteria:**
- Application uses `FastAPI(lifespan=...)`; no module-level singletons remain for: `Settings`, `provider_registry`, `model_cache`, `queue_semaphore`, `concurrency_semaphore`, `request_id_context`.
- `app.state.settings`, `app.state.provider_registry`, `app.state.model_cache`, `app.state.queue_semaphore`, `app.state.concurrency_semaphore` are populated post-startup.
- `conftest.py` exposes a `LLM_TTS_API_TEST_NO_LIFESPAN` env-toggle that bypasses real startup for unit tests.
- Existing tests still pass with at most mechanical dependency-override updates.

**Atomic tasks:**

| Task | Purpose | Parallel-safe within story | Refs |
|---|---|---|---|
| S-003.T1 | Introduce `lifespan` async context manager in `main.py` (or new `src/llm_tts_api/lifespan.py`); leave a single TODO for each singleton slot | yes | FR-HL-03 |
| S-003.T2 | Move `Settings` construction into lifespan; expose via `app.state.settings`; remove module-level singleton | sequential after T1 | FR-HL-03 |
| S-003.T3 | Move `TTSProviderRegistry` and any model-cache scaffolding into lifespan; expose via `app.state.*` | sequential after T1 | FR-HL-03, prep for S-006/S-008 |
| S-003.T4 | Retire module-level `@lru_cache` factories that hold singletons across tests; convert to FastAPI `Depends` reading from `app.state` | sequential after T2+T3 | NFR-OP-01 |
| S-003.T5 | `conftest.py`: add `LLM_TTS_API_TEST_NO_LIFESPAN` env toggle + dependency-override pattern for unit tests | sequential after T1 | RISK-2 mitigation |
| S-003.T6 | Update existing failing tests to use the new override pattern; verify nothing else regresses | sequential after T5 | — |

**Testing & verification:** existing `pytest -q` suite passes. Add a new test asserting `app.state.settings is not None` post-startup. Add a test that two TestClient sessions don't share singletons via the `LLM_TTS_API_TEST_NO_LIFESPAN` toggle.

---

### S-004 — Request-ID middleware + structured logging baseline

**Type:** Technical
**Status:** DONE
**Depends on:** none
**Refs:** FR-OB-01..02, NFR-OB-01..02, NFR-PV-02..03
**Why selected:** S-009 (error taxonomy, next sprint) needs `X-Request-ID` available in error envelopes; everything else benefits from correlated logs.

**Acceptance criteria:**
- ASGI middleware sets and returns `X-Request-ID` on all responses (uses inbound header if present; otherwise UUID).
- Log lines include `request_id` whenever the request scope is active.
- `APP_LOG_FORMAT=json` produces one valid JSON object per log line with fields: `ts`, `level`, `logger`, `request_id`, `message`, extras.
- INFO-level logs do not contain raw input text or audio bytes (NFR-PV-02).
- Covered by tests mirroring UAT-OB-01..03.

**Atomic tasks:**

| Task | Purpose | Parallel-safe within story | Refs |
|---|---|---|---|
| S-004.T1 | Add `request_id` `contextvars.ContextVar` and ASGI middleware in e.g. `src/llm_tts_api/observability/request_id.py` | yes | FR-OB-01 |
| S-004.T2 | Reconfigure logging via `app_logging.py`: structured format with `request_id`; opt-in JSON via `APP_LOG_FORMAT=json` | yes | FR-OB-02, NFR-OB-02 |
| S-004.T3 | Audit existing logger calls in synthesis path to ensure no raw text/audio payload appears in INFO+ (NFR-PV-02) | yes | NFR-PV-02 |
| S-004.T4 | Tests: round-trip header (inbound → returned), auto-generation when absent, JSON-mode line shape, INFO-redaction check | sequential after T1+T2 | UAT-OB-01..03 |

**Testing & verification:** unit tests as above. Manual smoke: hit a running server, inspect headers and log lines.

---

### S-005 — Hardware detection module

**Type:** Technical
**Status:** DONE
**Depends on:** none
**Refs:** FR-HW-01..03, UAT-HW-01/03/06
**Why selected:** unblocks S-006 (provider auto-selection) for Sprint 2; mirrors llm-image-api's `engine/device.py` for parity.

**Acceptance criteria:**
- `detect_device()` returns `mps|cuda|cpu` per: MPS → CUDA → CPU.
- `detect_dtype()` returns `float16` on MPS/CUDA, `float32` on CPU by default.
- Env overrides `TTS_DEVICE` and `TTS_DTYPE` take precedence and are validated.
- `DeviceProfile` dataclass exported for S-006 consumption.
- Unit tests with monkeypatched `torch.backends.mps.is_available` and `torch.cuda.is_available` cover all three branches.

**Atomic tasks:**

| Task | Purpose | Parallel-safe within story | Refs |
|---|---|---|---|
| S-005.T1 | Add `src/llm_tts_api/engine/device.py` with `detect_device()` + `detect_dtype()` + `DeviceProfile` dataclass | yes | FR-HW-01..03 |
| S-005.T2 | Wire env overrides `TTS_DEVICE`, `TTS_DTYPE` (parse + validate in `Settings`; consumed by device module) | sequential after T1 | FR-HW-02..03 |
| S-005.T3 | Unit tests: MPS-available (monkeypatch), CUDA-available, neither, plus env-override paths | sequential after T1 | UAT-HW-01/03/06 |
| S-005.T4 | Wire `DeviceProfile` into `app.state` via lifespan (coordinates with S-003 if both land in the same PR; otherwise a follow-up wiring task) | sequential, deferred to merge | FR-HL-03 |

**Testing & verification:** unit tests above. No integration test in this sprint — provider auto-selection (which exercises this module end-to-end) is S-006.

---

## Sprint-wide testing & verification

- **CI gates must all pass** on every story's PR — this is itself the verification of S-001.
- **No regression in existing test suite** — `pytest -q` (with whatever fixtures the lifespan refactor introduces) must remain green.
- **Manual smoke**: run the service locally; confirm `/health` still returns 200; confirm a synthesis request still succeeds; confirm `X-Request-ID` is on the response; confirm log lines carry `request_id`.

## Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| S-003 (lifespan refactor) breaks existing tests in non-obvious ways | Medium | Medium | Keep T5+T6 in the same PR as T1..T4 so the test suite stays green per-PR; do not merge a partial refactor. |
| Coverage ratchet number can't be measured cleanly because tests are coupled to module-level singletons | Low | Low | T4 of S-001 runs *after* S-003's test-fixture work; if needed, ratchet starts after S-003 lands. |
| `mypy --strict` reveals more outstanding typing debt than expected on the existing codebase | Medium | Medium | Allow a one-time bulk `# type: ignore[reason]` pass on legacy modules at S-001.T3; record the debt as follow-up tasks for later sprints. |

## Stories NOT in this sprint (and rationale)

- **S-006 provider auto-selection** — depends on S-005 (this sprint); Sprint 2.
- **S-007 async concurrency refactor** — depends on S-003 (this sprint); high-risk (RISK-2); deserves dedicated attention in Sprint 2 or 3.
- **S-008 LRU model cache, S-009 error taxonomy, S-010 health/ready split, S-011 voice seed ingestion, S-012 config inventory** — all transitively depend on S-003 and/or S-022; Sprint 2 candidates.
- **S-022..S-025 voice repository + CRUD** — depend on S-003 + S-009 + S-012; Sprint 3 candidates.
- **S-013 rich endpoint** — depends on six prior stories; not before Sprint 4.
- **S-014** — RETIRED post-OQ-3.
- **S-015..S-021** — Group D / E; depend on rich endpoint and feature completion; later sprints.

## Definition of Done (Sprint 1)

- All five stories' acceptance criteria met.
- All CI gates green on `main` after merge.
- `docs/perf/baseline.md` committed and referenced from the sprint review note.
- `app.state` populated with the documented singleton set; no module-level singletons survive.
- A test run with `LLM_TTS_API_TEST_NO_LIFESPAN=1` exercises the unit-test path.
- `pytest -q` green; coverage at or above the ratchet floor.
- Hardware detection unit tests cover MPS / CUDA / CPU + env overrides.
- A sprint review note recorded at `docs/planning/sprints/sprint-review-1.md` (to be created by the code-reviewer skill at end-of-sprint).
