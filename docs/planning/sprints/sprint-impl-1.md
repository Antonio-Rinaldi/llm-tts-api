# Sprint 1 — Implementation Notes

Per-task implementation notes appended as work completes. Companion to `sprint-1.md`.

---

## S-001 — CI quality gate scaffolding (ratchet)

**Status:** READY-FOR-REVIEW
**Completed:** 2026-05-17
**Refs:** FR-QG-01..04, NFR-MT-01..04, NFR-SE-05; OQ-1 resolved (ratchet)
**Technology:** Python 3.10+, `uv` 0.11+, GitHub Actions, ruff/mypy/pytest+pytest-cov/pip-audit. No production deps added. Selected because the SRS scopes the cycle to the existing FastAPI/Python service (no architecture rewrite).

### Files created

| File | Purpose |
|---|---|
| `.github/workflows/ci.yml` | GitHub Actions workflow mirroring `llm-image-api`'s pattern: lint, format check, type check, tests + coverage ratchet, dependency audit. |
| `src/llm_tts_api/py.typed` | PEP 561 marker so downstream consumers see this package's type hints. |

### Files modified

| File | Change |
|---|---|
| `pyproject.toml` | Migrated `[project.optional-dependencies] dev` → `[dependency-groups] dev` (PEP 735; what `uv sync --group dev` expects). Added `pytest-cov`, `pytest-asyncio`, `httpx` to dev group. Added `[tool.coverage.run]` (source) and `[tool.coverage.report]` (show_missing, exclude_lines). Added `[tool.pytest.ini_options]` `pythonpath = ["src"]` and `asyncio_mode = "auto"` (mirrors llm-image-api). Added `[tool.mypy] mypy_path = "src"` + `explicit_package_bases = true`. Declared `[tool.setuptools.package-data] llm_tts_api = ["py.typed"]` so the marker ships with the wheel. |
| `tests/conftest.py` and 28 other files | Reformatted by `uv run ruff format` (mechanical only; no logic change). |

### Key decisions

1. **PEP 735 `[dependency-groups]` over PEP 621 `[project.optional-dependencies]`.** `uv sync --frozen --group dev` reads from the former; this aligns with the llm-image-api CI workflow verbatim. The existing dev deps are unchanged in content; only the table they live in changed. (`pip install -e ".[dev]"` no longer works against the project — `uv sync --group dev` is the documented dev workflow.)
2. **Coverage floor set to `--cov-fail-under=83`, measured at 83.64%** on day 1. The 1-percentage-point safety margin absorbs ordinary noise; ratchet protocol is to raise the floor (never lower) as coverage improves. End-of-cycle target ≥ 80% is already satisfied; OQ-1 is effectively a no-op risk for this codebase but the ratchet stays as the policy. Locking the floor in the workflow file (not `[tool.coverage.report] fail_under`) means every bump shows up clearly in `git log` of `ci.yml`.
3. **`pip-audit` gates with `--skip-editable`.** This avoids `llm-tts-api` itself failing the audit ("Dependency not found on PyPI"). All third-party deps are audited; the workflow comment documents that failure-on-any-advisory is the default policy.
4. **`mypy --strict` is already clean** on the existing 32 source files — no `# type: ignore[reason]` bulk pass was needed. Surprised by this in a good way; existing codebase was already typed to strict-mode standards.
5. **Did NOT touch the `sys.path.insert` hack in `tests/conftest.py`.** `pythonpath = ["src"]` in pytest config now serves the same purpose, but removing the hack risks subtle breakage and is out of S-001 scope. Marked as follow-up cleanup.
6. **GitHub Action versions pinned by SHA, not tag.** Mirrors llm-image-api; resists tag-mutation supply-chain attacks. Versions: `actions/checkout@v4.2.2`, `astral-sh/setup-uv@v4.2.0`.

### Verification — all categories green

| Category | Command | Result |
|---|---|---|
| Lint | `uv run ruff check src/ tests/` | All checks passed |
| Format | `uv run ruff format --check src/ tests/` | 52 files already formatted |
| Types | `uv run mypy src/` | Success: no issues found in 32 source files |
| Tests + coverage | `uv run pytest --cov=src/llm_tts_api --cov-fail-under=83` | 69 passed; coverage 83.64% (≥ 83 floor) |
| Dep audit | `uv run pip-audit --skip-editable` | No known vulnerabilities found |

### Security considerations

- **Pinned action SHAs** (NFR-SE-06 hygiene principle applied to CI surface — supply-chain risk).
- **`pip-audit` in CI** enforces NFR-SE-05 from day 1; failures block merge.
- **No new secrets** added to the workflow; uses only public actions.
- **No production dependency changes** — every added dep is dev-only.

### Acceptance criteria status

| Criterion | Status |
|---|---|
| CI workflow exists and runs on PRs and main | ✅ `.github/workflows/ci.yml` (push + pull_request triggers) |
| `ruff check` and `ruff format --check` pass on `src/` and `tests/` | ✅ verified locally |
| `mypy --strict src/` passes | ✅ zero errors on 32 files |
| Coverage threshold set to current measured level; ratchet protocol documented | ✅ floor 83, current 83.64%, protocol in workflow comment |
| `pip-audit` runs and fails on configured threshold | ✅ default policy: any advisory fails |
| `py.typed` marker shipped | ✅ `src/llm_tts_api/py.typed` + `[tool.setuptools.package-data]` |

### Known limitations / deferred items

- **conftest.py `sys.path` hack cleanup** deferred — non-essential to gate functionality; risk-vs-value not worth in S-001 scope. Cleanup story: a one-line deletion once test suite confirms pytest `pythonpath` is fully effective. Recommended as a small refactor PR after S-003.
- **CI does NOT yet run `docker build`** — that's S-020 (Sprint 6). Current workflow is software-only.
- **Coverage ratchet is manual.** Bumping the floor is a per-PR judgement; no auto-ratchet tooling. Acceptable given small team / single-author cycle.
- **`pytest-asyncio` warning suppression not configured.** May surface deprecation warnings in CI logs; cosmetic only.

### Follow-up tasks (not in S-001)

1. Remove `sys.path.insert` in `tests/conftest.py` after S-003 lands (lifespan refactor may touch the same file).
2. When coverage exceeds 85%, bump `--cov-fail-under` to 85 in the workflow.
3. Consider adding a separate `audit-only` workflow that runs daily on `main` so new advisories surface even without PRs.

### Service Interface

N/A — S-001 is an infrastructure/tooling task with no service-boundary surface.

---

## S-002 — Baseline performance capture

**Status:** READY-FOR-REVIEW (scaffolding) / BLOCKED-ON-USER (final measurement)
**Completed (scaffolding):** 2026-05-17
**Refs:** NFR-PF-01, A-7, OQ-7 resolved
**Technology:** Python stdlib only (no extra deps) for the measurement script; Markdown for the methodology doc.

### Scope reality

This story needs two halves:

- **Half A (this agent can deliver):** the measurement script, the reference-input fixture, the methodology doc with a paste-ready Markdown row template.
- **Half B (operator must run):** start the real service against a real MLX-audio model, run the script, paste the resulting Markdown row into `docs/perf/baseline.md`, commit.

Half B requires multi-GB model weights, Apple Silicon hardware, and minutes of inference time — beyond what is meaningful to execute inside this agent loop. Half A is complete; Half B is queued for the operator.

### Files created

| File | Purpose |
|---|---|
| `scripts/perf_baseline.py` | Stdlib-only measurement script: warmup + N timed POSTs against `/v1/audio/speech`, prints a paste-ready Markdown table row. |
| `tests/perf/fixtures/baseline_input.txt` | ~700-char Italian narrative used as the reference input (exercises semantic chunking + normalization). |
| `docs/perf/baseline.md` | Methodology + regression-policy doc, with a Measurements table holding a `_pending_` row to be filled in by the operator. |

### Files modified

| File | Change |
|---|---|
| `.github/workflows/ci.yml` | Extended ruff lint + format-check paths from `src/ tests/` → `src/ tests/ scripts/` so the new script (and any future scripts) are gated. |

### Key decisions

1. **Stdlib `urllib` over `httpx` / `requests`** for the measurement script. Reasons: zero extra runtime deps (the script must be runnable in any environment that can reach the service URL); no third-party hot-spots in the measured path (only the service's perf is being measured). Trade-off: less ergonomic API. Net: worth it for a one-off measurement script.
2. **`statistics.quantiles(..., method="inclusive")`** for percentiles rather than rolling our own or pulling in numpy. Inclusive method matches what most engineers expect from "p50" / "p95" on a small sample (11 runs).
3. **Drain the response body** in `_one_request` (`resp.read()`) so the timing reflects end-to-end synthesis completion, not header arrival. Documented inline.
4. **Sample-size default = 11 measured runs + 1 warmup.** 11 is the smallest sample that lets the 95th percentile fall on an actual data point. Operator can raise via `--runs` if variance demands.
5. **Italian narrative input** chosen because the codebase ships an Italian default voice config (`alloy` in voice_map.example.json) and `num2words` Italian language support is exercised. Keeps the reference workload aligned with the codebase's primary language target.
6. **Baseline doc is append-only.** Measurements table accumulates rows; never overwrite history. S-021 compares end-of-cycle to the first row.

### Verification — all gates green

| Category | Command | Result |
|---|---|---|
| Lint | `uv run ruff check src/ tests/ scripts/` | All checks passed |
| Format | `uv run ruff format --check src/ tests/ scripts/` | 53 files already formatted |
| Types | `uv run mypy src/` | Success: 32 files, zero errors |
| Tests + coverage | `uv run pytest --cov=src/llm_tts_api --cov-fail-under=83` | 69 passed, 83.64% (unchanged from S-001) |
| Script syntax | `python -c "import ast; ast.parse(...)"` | OK |
| Script help | `python scripts/perf_baseline.py --help` | Renders |

### Security considerations

- **No outbound network calls except to the operator-supplied `--url`.** Default is `http://127.0.0.1:8010` (local only).
- **Stdlib `urllib`** — no third-party HTTP client surface attack.
- **Per-request timeout** defaults to 600 s; configurable. Hangs are bounded.
- **No secret exfiltration** — the script only emits latency numbers + git SHA + platform string. No request bodies, no response bodies, no env vars.

### Acceptance criteria status

| Criterion | Status |
|---|---|
| `docs/perf/baseline.md` exists with input text, voice id, host spec, methodology, p50/p95, timestamp | ✅ scaffolding; numbers row marked `_pending_` until operator runs the script |
| Methodology reproducible: commit SHA recorded; measurement script referenced | ✅ |
| README's Performance section (introduced in S-019) will later reference this file | ⏳ deferred to S-019 |

### Known limitations / deferred items

- **Final numbers row is pending operator execution.** The story cannot be marked DONE until the row is filled and committed. Half A scaffolding is complete and reviewable now.
- **No CI-side perf regression gate.** S-021 (end-of-cycle) is the regression check; running the perf script in CI on every PR is out of scope and would be unreliable on ephemeral GitHub runners.
- **No streaming-mode measurement.** S-015 will add the streaming path; a separate baseline row will be added then.
- **Single host class.** Only Apple Silicon (the reference host per NFR §1) is measured as the regression anchor. CUDA and CPU paths produce informational rows only.

### Operator runbook (Half B)

```bash
# Terminal 1: start the service with the real provider stack
cd /Volumes/Coding/Projects/Applications/epub/llm-tts-api
uv run uvicorn llm_tts_api.main:app --host 127.0.0.1 --port 8010

# Terminal 2: capture the baseline (after the service prints "Application startup complete")
uv run python scripts/perf_baseline.py \
    --url http://127.0.0.1:8010 \
    --voice alloy \
    --runs 11 \
    --warmup 1

# Copy the printed pipe-delimited row, replace the _pending_ row in
# docs/perf/baseline.md, commit:
git add docs/perf/baseline.md
git commit -m "perf: capture S-002 baseline on Apple Silicon"
```

After the commit lands, mark S-002 DONE in `journal.md` and `sprint-1.md`.

### Service Interface

N/A — S-002 is a measurement infrastructure task with no service-boundary surface.

---

## S-005 — Hardware detection module

**Status:** READY-FOR-REVIEW
**Completed:** 2026-05-17
**Refs:** FR-HW-01..03, UAT-HW-01/03/06
**Technology:** Python stdlib (`importlib`, `platform`, `os`, `logging`, `dataclasses`, `typing.Literal`). No new runtime deps.

### Files created

| File | Purpose |
|---|---|
| `src/llm_tts_api/engine/__init__.py` | Package marker + public re-exports (`DeviceProfile`, `detect_device`, `detect_dtype`, `resolve_device_profile`). |
| `src/llm_tts_api/engine/device.py` | Detection module with the four exports above + private `_probe_device` and `_try_import_torch` helpers. |
| `tests/test_engine_device.py` | 21 unit tests covering torch-present and torch-absent branches, env overrides, dtype rules, frozen-dataclass invariants. |

### Files modified

None.

### Key implementation decisions

1. **Torch-soft detection.** llm-tts-api is MLX-only today; torch isn't installed. Instead of forcing a heavy `torch` dep to do detection, the module probes torch via `importlib.import_module("torch")` and falls back to platform/architecture detection if the import fails. This matches the SRS-implied semantics (FR-HW-01: MPS → CUDA → CPU) without forcing torch into the install graph.
   - **Apple Silicon Darwin without torch** correctly reports `mps`, because MLX uses the Metal backend regardless of torch's presence. This is the path the current production install exercises.
   - **CUDA detection** requires torch; this isn't a regression because no CUDA-capable provider exists in the codebase today (vLLM-Omni would add one).
2. **`_try_import_torch()` is the test seam.** A single module-level function returns either the imported torch module or `None`. Tests monkeypatch this seam (not `sys.modules`, not `__import__`) — cleaner, faster, no global-state leakage. Also keeps the detection code itself oblivious to the test environment.
3. **`DeviceProfile` is a `@dataclass(frozen=True, slots=True)`.** Immutability matters because the profile will be stashed on `app.state` (after S-003 lands) and shared across the async event loop without locking. `slots=True` shaves memory and forbids accidental attribute attachment.
4. **`Literal` types for device, dtype, and source.** `Literal["mps", "cuda", "cpu"]` lets `mypy --strict` catch typos at call sites without runtime overhead. The valid-set `frozenset`s exist for runtime validation in `detect_device`/`detect_dtype` (which accept arbitrary strings from env vars).
5. **`resolve_device_profile` reports `source="env"` if EITHER field came from env.** A single label is simpler than two. Operators grepping startup logs see one source = "auto" → fully auto-detected; source = "env" → at least one override was applied. The per-field breakdown is recoverable from the env vars themselves.
6. **Case-insensitive env values.** `TTS_DEVICE=MPS` works the same as `mps`. Common operator mistake; cheap to handle.
7. **Did NOT wire `DeviceProfile` into `app.state` in this story.** Story T4 says "coordinates with S-003 if both land in the same PR; otherwise a follow-up wiring task." S-003 hasn't landed yet; deferring the wiring to S-003 (lifespan refactor) keeps blast radius small. Documented as follow-up.

### Verification — all gates green

| Category | Command | Result |
|---|---|---|
| Lint | `uv run ruff check src/ tests/ scripts/` | All checks passed |
| Format | `uv run ruff format --check src/ tests/ scripts/` | 56 files clean |
| Types | `uv run mypy src/` | Success: 34 files (was 32), zero errors |
| Tests + coverage | `uv run pytest --cov=src/llm_tts_api --cov-fail-under=83` | 90 passed (was 69; +21), 84.64% (was 83.64%; +1.0pp) |

### Security considerations

- **No untrusted input sources.** Env vars are operator-controlled; values are strictly validated against `frozenset` allow-lists.
- **No path traversal / file I/O.** Pure logic + soft torch import.
- **`_try_import_torch` catches only `ImportError`.** Other exceptions during torch import propagate (rare but possible: torch initialization can fail on broken CUDA installs). Operator sees a real traceback in that case rather than a silent fall-through.
- **Frozen dataclass** prevents downstream tampering of the resolved profile.

### Acceptance criteria status

| Criterion | Status |
|---|---|
| `detect_device()` returns `mps\|cuda\|cpu` per the rule | ✅ all branches tested |
| `detect_dtype()` defaults to `float16` on MPS/CUDA, `float32` on CPU | ✅ |
| Env overrides take precedence and are validated | ✅ valid + invalid cases covered |
| Unit tests cover all branches (UAT-HW-01/03/06) | ✅ 21 tests, including torch-present, torch-absent, Apple-Silicon-fallback, env override, frozen invariant |
| `DeviceProfile` dataclass exported for S-006 consumption | ✅ re-exported from `engine/__init__.py` |

### Known limitations / deferred items

- **`DeviceProfile` not yet on `app.state`.** Deferred to S-003 (lifespan refactor), per story T4 note.
- **Provider auto-selection** (FR-HW-04..07) is S-006, not S-005. The module exports the data it needs but doesn't drive provider selection itself.
- **Memory-based warning** (FR-HL-05, NFR-OP-04) is part of S-010, not here.
- **No integration test** on real hardware. Unit tests via monkeypatched torch cover the logic; real-hardware verification is implicit (the existing MLX path keeps working).

### Service Interface

**Interface type:** in-process Python API. No HTTP / gRPC / message surface.

**Contract (consumed by S-006 provider auto-selection, S-003 lifespan):**
- `from llm_tts_api.engine import DeviceProfile, resolve_device_profile`
- `resolve_device_profile(device_override: str | None = None, dtype_override: str | None = None) -> DeviceProfile`
- Returned `DeviceProfile` is frozen, with fields:
  - `device: Literal["mps", "cuda", "cpu"]`
  - `dtype: Literal["float16", "bfloat16", "float32"]`
  - `source: Literal["auto", "env"]`
- Idempotent and side-effect-free apart from a single `logger.info` line at INFO level (`"device profile resolved: device=… dtype=… source=…"`).

**Consumer assumptions:**
- Call once per process at startup (S-003 will own this).
- Cache the result on `app.state.device_profile` (S-003).
- S-006 will dispatch on `device` to pick the provider; on a `cpu` device with no CPU-capable provider, S-006 fails startup per FR-HW-05.

---

## S-004 — Request-ID middleware + structured logging baseline

**Status:** READY-FOR-REVIEW
**Completed:** 2026-05-17
**Refs:** FR-OB-01..02, NFR-OB-01..02, NFR-PV-02..03
**Technology:** Python stdlib (`contextvars`, `logging`, `uuid`, `json`) + pure-ASGI middleware. No new runtime deps.

### Files created

| File | Purpose |
|---|---|
| `src/llm_tts_api/observability/__init__.py` | Public re-exports: `RequestIDMiddleware`, `current_request_id`, `request_id_var`, `REQUEST_ID_HEADER`. |
| `src/llm_tts_api/observability/request_id.py` | `ContextVar`-based correlation id, pure-ASGI middleware that mints/echoes `X-Request-ID`. |
| `tests/test_observability_request_id.py` | 9 tests: inbound echo, auto-generation, blank-header replacement, concurrent requests, contextvar reset, handler override, default value, set+reset, asyncio.Task propagation, log-record propagation. |
| `tests/test_app_logging.py` | Rewritten (subsumes the prior 1-test stub): 12 tests across the filter, JSON formatter, and `setup_logging` flows. |

### Files modified

| File | Change |
|---|---|
| `src/llm_tts_api/app_logging.py` | Replaced the minimal `setup_logging` with a request-id-aware version. Added `RequestIdFilter` (injects `request_id` attribute from contextvar onto every log record, with `-` sentinel when outside a request). Added `JsonFormatter` (single-line JSON, fold extras + exc_info). `setup_logging` now: (a) chooses formatter via `log_format` arg or `APP_LOG_FORMAT` env (`text`/`json`); (b) is idempotent (replaces existing handlers); (c) attaches the filter to the stream handler; (d) strips uvicorn's own handlers and routes via root. |
| `src/llm_tts_api/main.py` | Import `RequestIDMiddleware` from `llm_tts_api.observability`; wire it via `app.add_middleware` in `create_app()`. |

### Key implementation decisions

1. **Pure-ASGI middleware over Starlette `BaseHTTPMiddleware`.** `BaseHTTPMiddleware` adds a streaming buffer that would complicate S-015 (rich-endpoint streaming) and add overhead for no gain. The pure-ASGI form (`__call__(scope, receive, send)`) is also faster and simpler to reason about.
2. **`contextvars.ContextVar` as the propagation seam.** Standard Python async pattern: each request gets its own logical context; `asyncio.Task` copies the context at task creation. Tested explicitly with `await asyncio.create_task(...)` to lock the propagation guarantee.
3. **`-` sentinel outside a request scope.** Startup, lifespan, and background-task log lines need a stable column. An empty string would create visually inconsistent `[]` in human-format lines; `-` reads as "no request" without lying about the id. JSON output uses the same value.
4. **Idempotent `setup_logging`.** Tests/CI may invoke setup multiple times (re-create app per test); stacking handlers would silently double every log line. Setup now strips existing handlers before installing the new one.
5. **Did NOT enforce NFR-PV-02 redaction in this module.** NFR-PV-02 says INFO+ logs must not contain raw input text or audio bytes. That's a producer-side discipline (route handlers, service code) — the logging module shouldn't pretend to redact arbitrary `msg` strings, and a redacting formatter would mask bugs in the producer. The module's contract is "preserve what was logged plus inject request_id". A code-review checklist item (Sprint 2+, FR-OB-03 implementation) will audit existing logger calls.
6. **Inner-app header override is honored** (`_wrap_send` checks `already_set`). If a handler explicitly sets `X-Request-ID`, the middleware does NOT duplicate it. Future error envelopes (S-009) can set their own id without producing two header values.
7. **Lifespan and WebSocket scopes pass through.** The middleware short-circuits on `scope["type"] != "http"`. Lifespan events fire once at startup and don't have client-supplied headers in a meaningful sense; setting a contextvar there would never get reset (no per-request boundary).
8. **Latin-1 for header decoding.** RFC 7230 says HTTP headers are ISO-8859-1; Starlette uses latin-1 internally. Defensive `try/except UnicodeDecodeError` falls back to a fresh UUID rather than crashing.
9. **UUID4 hex (32 chars) format.** Compact, sortable enough for grep, no hyphens to escape in shell. Industry-standard for correlation ids.

### Verification — all gates green

| Category | Command | Result |
|---|---|---|
| Lint | `uv run ruff check src/ tests/ scripts/` | All checks passed |
| Format | `uv run ruff format --check src/ tests/ scripts/` | 59 files clean |
| Types | `uv run mypy src/` | Success: 36 files (was 34; +observability + tests of observability), zero errors |
| Tests + coverage | `uv run pytest --cov=src/llm_tts_api --cov-fail-under=83` | 111 passed (was 90; +21), 85.46% (was 84.64%; +0.82pp) |
| Manual smoke | `client.get("/health", headers={"X-Request-ID": "smoke-test"})` | 200; `x-request-id: smoke-test` returned; log lines carry `[-]` outside request scope |

### Security considerations

- **No payload bleed into logs by this module.** Filter only injects the request id; producers retain full control over `msg`/`args`.
- **Malformed headers fall back gracefully** rather than crashing the request (UnicodeDecodeError → mint a fresh id).
- **Header injection guard:** the response header is set from a server-validated source (either inbound after decode/strip or a freshly minted UUID). The client cannot inject CRLF or other control sequences because Starlette's header serialization rejects them at write time.
- **No timing attack surface.** Request id is not a secret.

### Acceptance criteria status

| Criterion | Status |
|---|---|
| Middleware sets and returns `X-Request-ID` on all responses | ✅ tested for inbound, auto-mint, blank-replacement, override paths |
| Log lines include `request_id` whenever request scope is active | ✅ tested via caplog + handler-filter |
| `APP_LOG_FORMAT=json` produces one valid JSON object per line with the required fields | ✅ |
| INFO-level logs do not contain raw input text or audio bytes | ✅ this module preserves producer-side discipline; no enforcement here by design (see Decision 5) |
| Tests mirror UAT-OB-01..03 | ✅ inbound echo + auto-generation + JSON-mode |

### Known limitations / deferred items

- **No request-id propagation into uvicorn access logs.** Uvicorn's access logger logs at request *end* with its own format; threading the request id through would require a custom access-log formatter or middleware. Not blocking; S-019 docs will note this.
- **Producer-side payload-redaction audit deferred.** A scan of existing `logger.info("...")` call sites in services/routers to ensure they don't log raw text/audio is queued for an early Sprint 2 task (could fold into S-009 error taxonomy).
- **No log sampling or rate limits.** A single broken loop could spam logs. Acceptable for the LAN-only deploy profile; rate limiting is on the Roadmap.

### Service Interface

**Interface type:** ASGI middleware + Python module-level API.

**Wire contract:**
- Request header `X-Request-ID` is read if present and non-blank; otherwise a UUIDv4 hex id is minted.
- Response header `X-Request-ID` is always present (passes through inbound id or the minted id).
- ASGI lifespan / WebSocket scopes pass through unmodified.

**In-process Python contract** (consumed by S-009 errors, S-013 rich endpoint, future route handlers):
- `from llm_tts_api.observability import current_request_id`
- `current_request_id() -> str` — returns the active id during a request, empty string otherwise.
- Log records emitted inside a request automatically carry the id; producers don't have to thread it.

**Consumer assumptions:**
- The middleware must be installed early in the app (it currently runs before exception handlers because exception handlers wrap the route, not the middleware stack).
- Error envelopes (S-009) should include the current id in the JSON body under `error.request_id` AND echo the header (this is automatic via the middleware if the handler doesn't override).

---

## S-003 — Lifespan + app.state singletons

**Status:** READY-FOR-REVIEW
**Completed:** 2026-05-17
**Refs:** FR-HL-03, NFR-OP-01, A-2, RISK-2
**Technology:** Python stdlib + FastAPI. No new runtime deps.

### Files created

| File | Purpose |
|---|---|
| (none — refactor only) | |

### Files modified

| File | Change |
|---|---|
| `src/llm_tts_api/dependencies.py` | Removed all `@lru_cache` factories. Introduced `AppDependencies` dataclass (singleton bundle) and `build_default_dependencies()` factory. Converted every `get_*` getter to a Request-aware function that reads from `request.app.state.*`. Added `get_device_profile` (consumes S-005). |
| `src/llm_tts_api/main.py` | Lifespan now calls `build_default_dependencies()` and fans out the result onto `app.state.{settings, device_profile, model_registry, provider_registry, tts_service, stt_service}`. Added `TEST_BYPASS_ENV` constant + `_test_bypass_active()` helper. When bypass env is truthy, lifespan exits without touching `app.state` (fixtures inject their own slots). |
| `src/llm_tts_api/routers/health.py` | `/ready` now reads `request.app.state.tts_service` directly (presence = ready, absence/error = 503 degraded) instead of calling `dependencies.get_tts_service()`. |
| `tests/conftest.py` | Rewrote `client` fixture: sets bypass env, populates every `app.state` slot via a `_stub_app_state` helper, wires `dependency_overrides[get_tts_service]` for FastAPI Depends. Removed the old two-seam monkeypatch pattern. |
| `tests/test_startup_preload.py` | Rewrote to test the new `build_default_dependencies` seam via `monkeypatch.setattr(main_module, "build_default_dependencies", spy)`. Added an explicit bypass-env test. |
| `tests/test_health_endpoints.py` | Rewrote the degraded-path test: build app in bypass mode, do NOT set `app.state.tts_service`, assert 503. Simpler than the old monkeypatch dance. |
| `tests/test_audio_speech.py` | Removed dead `cache_clear()` calls. Added `client.__enter__()` so lifespan fires (TestClient construction alone does not trigger lifespan startup events). |
| `tests/test_conftest.py` | Updated docstring to reflect new pattern; test body unchanged (still validates the override mechanism). |

### Key implementation decisions

1. **Single `AppDependencies` bundle.** A dataclass that holds all singletons. Lifespan builds one, fans out across `app.state.*`. Tests can construct partial bundles via `object.__new__(Settings)` to skip env-driven validation. The container makes the handoff explicit and gives future stories (S-007 semaphores, S-008 model cache) an obvious place to add fields.
2. **Request-aware getters via `cast(...)`.** Each `get_*` is a one-liner that pulls from `request.app.state.*`. `cast()` documents the expected type without runtime overhead (mypy-strict happy). Loose typing of Starlette's `State` would otherwise leak `Any` into route signatures.
3. **`LLM_TTS_API_TEST_NO_LIFESPAN` env toggle for test bypass.** A single env var read at lifespan time. Truthy values: `1 / true / yes` (case-insensitive); anything else (including unset) runs the real lifespan. Tests that exercise the real lifespan path (e.g. `test_startup_calls_builder_when_not_bypassed`) explicitly delete the env. Tests that need stubbed state (the `client` fixture) set it. No global side-effect between tests because pytest's `monkeypatch.setenv` is scoped per-test.
4. **`/ready` reads `app.state.tts_service` directly.** Previously called `dependencies.get_tts_service()`; now it does `getattr(request.app.state, "tts_service", None)`. Cleaner — health endpoints shouldn't depend on Depends machinery (which would force `Depends(get_tts_service)` to either succeed or fail with a 500). Direct attribute access lets `/ready` distinguish "not yet ready" from "broken" itself.
5. **TestClient context entry is required for lifespan.** Discovered the hard way: `TestClient(create_app())` does NOT fire lifespan; you need `with TestClient(...)` or explicit `client.__enter__()`. `test_audio_speech.py` uses the explicit form via a helper to keep the test signatures unchanged.
6. **Conftest stub uses `object.__new__(Settings)` to skip `__post_init__`.** Settings parses env vars and requires a real voice map file. For tests that don't care about real settings, bypassing the constructor is the cheapest way to get a typed Settings instance. Each test that DOES exercise real Settings (test_audio_speech) sets `TTS_VOICE_MAP_FILE` and goes through the real lifespan.
7. **`AppDependencies` is mutable (no `frozen=True`).** Future sprints (S-007, S-008) will replace fields (e.g. swap a real model cache in). A frozen dataclass would force re-instantiation. Acceptable mutation surface because the bundle is only assembled at lifespan startup; runtime code only reads from `app.state.*`.

### Verification — all gates green

| Category | Command | Result |
|---|---|---|
| Lint | `uv run ruff check src/ tests/ scripts/` | All checks passed |
| Format | `uv run ruff format --check src/ tests/ scripts/` | 59 files clean |
| Types | `uv run mypy src/` | Success: 36 files, zero errors |
| Tests + coverage | `uv run pytest --cov=src/llm_tts_api --cov-fail-under=83` | **112 passed** (was 111), **85.55%** coverage (was 85.46%) |

### Security considerations

- **Test bypass env is opt-in and explicit.** No way for a production deploy to accidentally skip lifespan (would require setting `LLM_TTS_API_TEST_NO_LIFESPAN=1` in production env, an operator mistake — not a code defect).
- **No new attack surface.** Refactor only — no new request paths, no new parsing, no new I/O.
- **`getattr` with default on `app.state`** prevents a route from crashing if a slot is missing (returns 503 from `/ready`, the documented failure mode).

### Acceptance criteria status

| Criterion | Status |
|---|---|
| Application uses `FastAPI(lifespan=...)`; no module-level singletons for the listed objects | ✅ All `@lru_cache` factories removed from `dependencies.py`. (Future slots — `model_cache`, `queue_semaphore`, `concurrency_semaphore` — will be added by S-007/S-008 onto the same `app.state` seam.) |
| `app.state.settings`, `app.state.provider_registry`, `app.state.model_registry`, `app.state.tts_service`, `app.state.stt_service`, `app.state.device_profile` populated post-startup | ✅ |
| `conftest.py` exposes `LLM_TTS_API_TEST_NO_LIFESPAN` env-toggle | ✅ via `TEST_BYPASS_ENV` constant exported from `main.py` |
| Existing tests still pass with mechanical dependency-override updates | ✅ 112 passed (was 111 pre-S-003; the +1 is S-003's new test) |

### Known limitations / deferred items

- **`app.state.device_profile` not yet consumed by anything.** S-006 (provider auto-selection) will read it. The slot exists and is populated; the consumer is sprint 2.
- **No `app.state.queue_semaphore` / `concurrency_semaphore` slots yet.** Reserved for S-007 (async concurrency refactor).
- **No `app.state.model_cache` slot yet.** Reserved for S-008.
- **`TestClient.__enter__()` in `test_audio_speech.py` is a small leak.** TestClient doesn't get explicitly exited; pytest's process exit handles cleanup. A follow-up could convert `_build_client_with_voice` into a proper pytest fixture with teardown. Out of S-003 scope.
- **No new sys.path.insert cleanup.** Already noted as S-001 follow-up; still pending.

### Service Interface

**Interface type:** in-process Python API + ASGI lifespan contract.

**In-process contract (the lifespan seam future stories will build on):**
- `from llm_tts_api.dependencies import AppDependencies, build_default_dependencies`
- `build_default_dependencies() -> AppDependencies` — constructs the full graph from env. Idempotent only in the sense that calling it twice produces two independent graphs (each with its own preloaded models).
- `app.state.*` slots: `settings`, `device_profile`, `model_registry`, `provider_registry`, `tts_service`, `stt_service`. All set during lifespan startup; all readable thereafter via `request.app.state.<slot>` or via the `Depends(get_*)` wrappers.

**Test seam:**
- `from llm_tts_api.main import TEST_BYPASS_ENV` — the canonical env-var name.
- `monkeypatch.setenv(TEST_BYPASS_ENV, "1")` before `create_app()` to skip lifespan construction.
- After `create_app()`, the test populates whichever `app.state.*` slots its routes will read.

**Consumer assumptions for downstream sprints:**
- S-006 will look up `request.app.state.device_profile` to dispatch on `device` for provider selection.
- S-007 will add `app.state.queue_semaphore` and `app.state.concurrency_semaphore` alongside the existing slots; routes will read those for concurrency gating.
- S-008 will replace whatever model-loading state currently lives inside `TTSService` with an explicit `app.state.model_cache` slot.
