# Sprint 1 — Code Review Findings

Append-only review document. Each section is one task/story review round.

---

## S-003 / Lifespan + app.state singletons — Review #1

**Verdict:** **APPROVED** (zero must-fix; three should-fix recommendations below).
**Reviewed:** 2026-05-17
**Reviewer:** code-reviewer skill (5 parallel agents, confidence scoring per [references/confidence-scoring.md]).
**Scope:** files modified by S-003 per `sprint-impl-1.md` §S-003.

### Method

Five parallel review agents dispatched per rubric (spec compliance, architecture compliance, code quality, test coverage, simplification). All findings scored on Evidence (0–40) + Impact (0–30) + Actionability (0–30); threshold 75; borderline scores (73–76) re-evaluated once and discarded if still borderline.

### Discarded findings worth recording

For traceability, the following were considered and deliberately not flagged:

| Source | Finding | Why discarded |
|---|---|---|
| Agent 1 | `model_cache`, `queue_semaphore`, `concurrency_semaphore` slots not populated | These slots are S-007 (semaphores) and S-008 (model cache) work; impl notes explicitly defer them. The AC text was forward-looking; S-003 delivers the *seam* (lifespan + `app.state` pattern) not the slots themselves. |
| Agent 2 | `/ready` should expose `app.state.ready` boolean and drain detection | FR-HL-04 (graceful drain) is S-010's deliverable. Current `/ready` distinguishes "tts_service present" vs "absent" which is functionally adequate until S-010 lands. Borderline (75) re-evaluated → discard. |
| Agent 2 | Lifespan lacks `try/finally yield` shutdown body | Same as above — S-010 owns the drain body. S-003 ships the structural seam (lifespan exists); future stories fill it. Borderline → discard. |
| Agent 3 | `.env` overrides existing env vars via `os.environ[key] = value` | Pre-existing code in `_load_env_file`; S-003 did not modify this helper. Flag as a separate cleanup ticket, not a S-003 regression. |
| Agent 3 | `_load_env_file` quote-stripping is asymmetric | Pre-existing; same rationale as above. |
| Agent 4 | `_load_env_file` / `_load_default_env_files` untested | Pre-existing helpers; coverage gap predates S-003. |
| Agent 4 | No concurrent-request test of singletons | Forward-looking — relevant once S-007 introduces real concurrency. |
| Agent 5 | All four simplification suggestions | Each scored <70; not clear net wins given the impl notes' deliberate forward-extension design (e.g. `AppDependencies` dataclass exists to host S-007/S-008 fields). |

### Should-fix findings (non-blocking; queue as follow-ups)

#### SF-1 — Module-level `app = create_app()` mutates env at import time

- **File:** `src/llm_tts_api/main.py:120-121` (the `_load_default_env_files()` + `app = create_app()` block).
- **Category:** Code Quality — test isolation.
- **Finding:** Importing `llm_tts_api.main` (e.g. during pytest collection or any tooling import) triggers `_load_default_env_files()` which calls `os.environ[key] = value` unconditionally for every key in `.env` / `.env.local`. This can silently clobber values set by `monkeypatch.setenv(...)` or CI env vars *if* a test imports the module after monkeypatching.
- **Expected:** Env-file loading should fire only when the service is actually being run (via uvicorn / the `run()` CLI entry), not at library-import time.
- **Suggested action:** Move `_load_default_env_files()` into `run()` (and keep `app = create_app()` at module scope only for uvicorn's import-time discovery, which doesn't need the env-file load — uvicorn will read env from the actual process env).
- **Confidence score:** Evidence 35, Impact 18, Actionability 25 → **78**.

#### SF-2 — Five of six `get_*` getters in `dependencies.py` have no direct unit tests

- **File:** `src/llm_tts_api/dependencies.py:89-116` and `tests/`.
- **Category:** Test Coverage.
- **Finding:** `get_settings`, `get_model_registry`, `get_tts_provider_registry`, `get_stt_service`, `get_device_profile` have zero direct tests. Only `get_tts_service` is covered (via `tests/test_conftest.py`). A bug like swapping `request.app.state.settings` for `request.app.state.app_settings` in any of the five other getters would not be caught — coverage line-counts mask this because the getters are one-liners that are reached indirectly through routers.
- **Expected:** A parametrized identity test asserting each getter returns the matching `app.state` slot.
- **Suggested action:** Add `tests/test_dependencies_getters.py` with one test per getter declaring a `/__test/dep_<name>` route via `Depends(get_X)` and asserting `response.json()["same_instance"] is True`. Same pattern as the existing `tests/test_conftest.py::test_client_fixture_overrides_get_tts_service_via_direct_import`.
- **Confidence score:** Evidence 35, Impact 18, Actionability 25 → **78**.

#### SF-3 — `test_bypass_env_skips_construction` assertion is loose

- **File:** `tests/test_startup_preload.py:108` (last line of the test).
- **Category:** Test Coverage — weak assertion.
- **Finding:** `assert not hasattr(app.state, "tts_service") or app.state.tts_service is None` — the `or` clause silently accepts a populated slot whose value is `None`, which is the exact failure mode bypass mode is supposed to prevent. Either branch passes the test.
- **Expected:** Strict assertion: bypass mode leaves the slot **absent**, not populated with `None`.
- **Suggested action:** Replace the line with `assert not hasattr(app.state, "tts_service")`. Optionally also assert the other slots are absent (`settings`, `model_registry`, `provider_registry`, `stt_service`, `device_profile`).
- **Confidence score:** Evidence 40, Impact 15, Actionability 30 → **85** (lowered to should-fix because it's a test-quality issue, not a correctness/security issue per the rubric's must-fix gate).

### Notable strengths (not findings — recorded for context)

- `mypy --strict` clean across 36 source files (rare on a refactor of this scope).
- Coverage went UP (83.64% → 85.55%) despite the refactor — the new test paths in `test_startup_preload.py` more than offset the removed code.
- The TestClient lifespan-firing quirk was caught and documented inline in `test_audio_speech.py`.
- `AppDependencies` bundle gives S-007/S-008 a clearly-named extension seam.
- Conftest's `_stub_app_state` correctly populates every slot a router reads — no silent AttributeError on routes that consume `Depends(get_model_registry)` etc.

### Recommended disposition

Mark S-003 **DONE** (zero must-fix). Open three follow-up tickets for the should-fix items above; pick them up alongside S-007/S-008 work (SF-1 is independent; SF-2 + SF-3 fit naturally during S-007 when more getters get added).

---

## S-004 / Request-ID middleware + structured logging baseline — Review #1

**Verdict:** **APPROVED** (zero must-fix; six should-fix recommendations below).
**Reviewed:** 2026-05-17
**Reviewer:** code-reviewer skill (5 parallel agents, confidence scoring per [references/confidence-scoring.md]).
**Scope:** files modified or created by S-004 per `sprint-impl-1.md` §S-004.

### Method

Five parallel review agents dispatched per rubric. ~35 findings collected; scored on Evidence (0–40) + Impact (0–30) + Actionability (0–30); threshold 75; borderline scores re-evaluated once. Reference codebase `llm-image-api` confirmed to have NO equivalent middleware or structured logging — llm-tts-api is leading the reference on observability. Architecturally clean.

### Discarded findings worth recording

| Source | Finding | Why discarded |
|---|---|---|
| Agent 1 | JSON output flattens `extras` rather than nesting under an `extras` key | UAT-OB-03 doesn't require the nested form; flat keys match Datadog/ELK convention. Agent itself recommended accepting as-is. |
| Agent 1 | NFR-PV-02 producer-side enforcement carries no formatter-level mask | NFR-PV-02 enumerates a positive allow-list; generic redaction at the formatter cannot synthesize that allow-list. Producer-side discipline is the only viable mechanism — covered in impl notes Decision 5. |
| Agent 1 | WebSocket scope correlation gap | WS scopes are S-007 / realtime; explicitly out-of-scope for S-004. |
| Agent 3 | `JsonFormatter`'s `default=str` may leak sensitive `__str__` text | Speculative; depends on producers attaching secret-bearing objects to `extra`. Producer-side discipline applies. |
| Agent 3 | `RequestIdFilter` overrides producer-supplied `request_id` extra | This is the documented design — single source of truth = contextvar. Impl notes call it out implicitly. |
| Agent 3 | `setup_logging` strips pytest's caplog handler | Existing test (`test_log_record_carries_request_id`) works around this by attaching the filter directly to `caplog.handler`. Latent footgun but not a regression today. |
| Agent 4 | 7 of the 11 missing-test items (newline escaping, exc_info=False, reserved-attribute negative, APP_LOG_FORMAT case variants, etc.) | All scored 38–65 — below threshold individually; would clutter without proportional value. |
| Agent 5 | Cross-file duplication of `RequestIdFilter` tests | Each test is small; explicit per-module coverage is reasonable. |

### Should-fix findings (non-blocking; queue as follow-ups)

#### SF-4 — Add test that exception handler sees the populated request-id contextvar

- **File:** `tests/test_observability_request_id.py` (new test) / `src/llm_tts_api/main.py:83-89` (registration order).
- **Category:** Test Coverage — forward-compatibility for S-009.
- **Finding:** S-009 (error taxonomy) will read `current_request_id()` inside `openai_exception_handler` to set `error.request_id` on the envelope. The chain works today (Starlette mounts `ExceptionMiddleware` *inside* the user middleware stack, so the contextvar is still set when the handler runs), but no test pins this contract. A future middleware insertion that reorders the stack could regress silently.
- **Suggested action:** Add a test in `test_observability_request_id.py` that registers a route which `raise`s an `OpenAIHTTPException`, asserts the response has `X-Request-ID`, and asserts the JSON error body's `request_id` (once S-009 lands) or a captured log line carries the same id.
- **Confidence score:** Evidence 35, Impact 15, Actionability 25 → **75**.

#### SF-5 — `_wrap_send` duplicate-check is case-sensitive

- **File:** `src/llm_tts_api/observability/request_id.py:96`.
- **Category:** Code Quality — defense in depth.
- **Finding:** `any(name == header_bytes[0] for name, _ in headers)` compares against the literal lowercase bytes `b"x-request-id"`. ASGI spec requires lowercase header names from compliant servers, so this works on Starlette/uvicorn today. A non-conformant ASGI server or unusual inner middleware passing mixed-case bytes would slip past the check and yield duplicate headers.
- **Suggested action:** `already_set = any(name.lower() == header_bytes[0] for name, _ in headers)` — one-char fix.
- **Confidence score:** Evidence 35, Impact 15, Actionability 30 → **80**.

#### SF-6 — Validate inbound `X-Request-ID` against a safe charset

- **File:** `src/llm_tts_api/observability/request_id.py:75-82`.
- **Category:** Security — defense in depth against log injection.
- **Finding:** Inbound `X-Request-ID` is `.strip()`ed and echoed verbatim into the response AND into every log line via `[%(request_id)s]` in the text format. A client-supplied value containing tab / vertical tab / other non-CRLF control characters slips past Starlette's CRLF-only header sanitization and produces forged log lines. NFR §1 caveats threat model to "accidental misuse on LAN" — but the fix is one regex check.
- **Suggested action:** After `.strip()`, validate against `[A-Za-z0-9._\-]{1,128}`; on mismatch, `break` and mint a fresh UUID.
- **Confidence score:** Evidence 30, Impact 20, Actionability 25 → **75**.

#### SF-7 — JSON timestamp loses timezone offset (`%z` empty string on naive `time.struct_time`)

- **File:** `src/llm_tts_api/app_logging.py:81`.
- **Category:** Code Quality — observable JSON shape bug.
- **Finding:** `self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z")` uses `logging.Formatter.formatTime`, which under the hood calls `time.localtime(record.created)` — yielding a `time.struct_time` with no tzinfo. `strftime("%z")` returns `""` on most platforms when fed a naive struct, so the `ts` field renders as e.g. `"2026-05-17T17:35:55"` (no offset). Log aggregators expecting full ISO-8601 may misparse.
- **Suggested action:** Override `formatTime`:
  ```python
  def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
      return datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
  ```
  Drop the `datefmt` argument at the call site since `formatTime` now ignores it.
- **Confidence score:** Evidence 40, Impact 18, Actionability 25 → **83**.

#### SF-8 — `_resolve_request_id` `UnicodeDecodeError` branch is dead with `latin-1`

- **File:** `src/llm_tts_api/observability/request_id.py:73-80`.
- **Category:** Code Quality — dead code.
- **Finding:** `value.decode("latin-1")` cannot raise `UnicodeDecodeError` because every byte 0–255 maps to a valid latin-1 codepoint. The `try/except` is unreachable, the comment promising defensive behavior is misleading, and the branch is not covered.
- **Suggested action:** Either delete the `try/except` outright (the simpler fix) OR switch encoding to `utf-8` (matches HTTP/2 expectation; then the branch becomes reachable and merits a test). Recommend the first option for minimal change; combine with SF-6's regex check which would catch garbage anyway.
- **Confidence score:** Evidence 40, Impact 10, Actionability 25 → **75**.

#### SF-9 — Replace serial "concurrent" test with a real concurrency test

- **File:** `tests/test_observability_request_id.py:65-77` (`test_concurrent_requests_get_distinct_ids`).
- **Category:** Test Coverage.
- **Finding:** The test fires three sequential `client.get(...)` calls inside one synchronous `TestClient`. No two requests overlap, so the contextvar's main correctness claim (isolation across concurrent async tasks) is not validated. A bug that leaked the var across requests would still pass.
- **Suggested action:** Add a true-concurrency test using `httpx.AsyncClient(transport=httpx.ASGITransport(app=app))` + `asyncio.gather` against a route that does `await asyncio.sleep(0.05)` before reading the id. Fire 10 requests with distinct inbound ids; assert each saw its own id back. Keep the existing serial test as a smoke check.
- **Confidence score:** Evidence 35, Impact 15, Actionability 25 → **75**.

### Notable strengths (not findings)

- llm-image-api has NO observability package — llm-tts-api is **leading** the reference codebase here. Pattern is clean enough to backport.
- Pure-ASGI middleware over `BaseHTTPMiddleware` correctly anticipates S-015 streaming work.
- `_RESERVED` set already includes `taskName` (Python 3.12+ attribute) — future-conscious.
- Filter + format-key pattern (rather than `LoggerAdapter`) correctly captures uvicorn / FastAPI internal log records.
- Idempotent `setup_logging` (handler swap on re-invocation) avoids the duplicate-log-line bug that catches many implementations.
- All three UATs (UAT-OB-01..03) explicitly exercised, plus extras.

### Recommended disposition

Mark S-004 **DONE** (zero must-fix). Six should-fix items can land in any order — none block S-005 or Sprint 2. Suggested grouping: SF-5 + SF-6 + SF-8 are all one-region-of-code changes in `request_id.py` and can be a single small PR. SF-7 is a one-method override in `app_logging.py`. SF-4 + SF-9 are test additions.

---

## S-005 / Hardware detection module — Review #1

**Verdict:** **APPROVED** (zero must-fix; four should-fix recommendations).
**Reviewed:** 2026-05-17
**Reviewer:** code-reviewer skill (5 parallel agents).
**Scope:** files created by S-005 per `sprint-impl-1.md` §S-005.

### Method

Five parallel review agents dispatched per rubric. ~35 findings collected; scored on E (0–40) + I (0–30) + A (0–30); threshold 75; borderlines re-evaluated. Significant deduplication across agents (torch-error-handling raised by both Agent 3 and Agent 4; `_VALID_DEVICES` duplication raised by Agents 3 and 5; `getattr` defensiveness raised by Agents 2 and 5).

### Discarded findings worth recording

| Source | Finding | Why discarded |
|---|---|---|
| Agent 1 | Platform-fallback to "mps" diverges from FR-HW-01's torch-probe-only reading | Defensible SRS-driven adaptation: MLX uses Metal regardless of torch presence. Documented explicitly in impl notes Decision 1. |
| Agent 1 | T4 (wiring DeviceProfile into app.state) not done by S-005 | Wired by S-003's lifespan refactor. Acknowledged in impl notes. |
| Agent 2 | `detect_device(override)` parameter divergent from reference's no-arg form | Override parameter intentionally improves testability without env mutation. Impl notes Decision 2. |
| Agent 2 | `is_mps`/`is_cuda` helpers missing | Speculative; not used by any current consumer. |
| Agent 2 | `IMAGE_SERVER_DEVICE` vs `TTS_DEVICE` naming | `TTS_DEVICE` is pinned by SRS FR-HW-02. |
| Agent 3 | `# type: ignore[return-value]` could be `cast(Device, ...)` | Stylistic; both work; mypy strict is happy. |
| Agent 3 | `_VALID_DEVICES` / `Literal` duplication | Drift risk small; `get_args(Literal)` exists as future cleanup. |
| Agent 3 | Defensive `getattr` chain in `_probe_device` | Tests monkeypatch full torch shapes; the defense covers partial-attribute monkeypatch edge cases that mostly only matter under hostile mocking. Score 60-65; below threshold. |
| Agent 4 | Empty-string / whitespace / aarch64-Linux / dtype-case test gaps | Multiple individual gaps, each score 50–60. Collectively meaningful but no single one merits action. |
| Agent 5 | Double env parsing in `resolve_device_profile` | DRY violation but doesn't affect correctness; impl notes' Decision 5 documents the per-field source-label intent. |

### Should-fix findings (non-blocking; queue as follow-ups)

#### SF-10 — Empty / whitespace env value should be treated as `auto`, not as a hard error

- **File:** `src/llm_tts_api/engine/device.py:64-72` (`detect_device`) and `:83-91` (`detect_dtype`).
- **Category:** Code Quality — operator footgun.
- **Finding:** `TTS_DEVICE=""` or `TTS_DEVICE="   "` (a common "defined-but-unset" state from shell wrappers like `export TTS_DEVICE=$DEVICE` when `$DEVICE` is empty) currently raises `ValueError("TTS_DEVICE='' is not a valid device...")` and crashes startup. Convention is to treat empty as unset.
- **Suggested action:** After `raw = raw.strip().lower()`, add `if not raw: raw = "auto"`. Apply to both `detect_device` and `detect_dtype`.
- **Confidence score:** Evidence 35, Impact 15, Actionability 30 → **80**.

#### SF-11 — `_try_import_torch` only catches `ImportError`; broken torch install crashes startup

- **File:** `src/llm_tts_api/engine/device.py:167-176`.
- **Category:** Code Quality — error-handling gap.
- **Finding:** Module-docstring promises "torch-soft" detection. The current `try: ... except ImportError` catches the most common failure (torch not installed) but not the second-most-common (torch installed but its `__init__` raises — broken CUDA driver: `RuntimeError`; missing shared lib: `OSError`; etc.). These propagate up the call chain and crash startup, even though the MLX-only path would still work. Direct contradiction to the "soft" framing.
- **Suggested action:** Broaden to `except Exception as exc:` with a `logger.warning("torch import failed: %s; falling back to platform detection", exc)` so the broken install is still visible. (Also raised by Agent 4 F2.)
- **Confidence score:** Evidence 35, Impact 18, Actionability 25 → **78**.

#### SF-12 — `resolve_device_profile` log emits inline-format string, not structured extras

- **File:** `src/llm_tts_api/engine/device.py:128-133`.
- **Category:** Code Quality — observability consistency.
- **Finding:** S-004 introduced structured logging (`extra={...}` payloads + JSON format). This call uses `logger.info("device profile resolved: device=%s dtype=%s source=%s", ...)`. With `APP_LOG_FORMAT=json`, the fields are baked into the message string instead of being top-level JSON keys — operators grepping `device=mps` will find this line, but operators querying `device:"mps"` in their aggregator won't.
- **Suggested action:** Replace with `logger.info("device profile resolved", extra={"device": profile.device, "dtype": profile.dtype, "source": profile.source})`.
- **Confidence score:** Evidence 35, Impact 15, Actionability 30 → **80**.

#### SF-13 — `_try_import_torch` smoke test asserts nothing meaningful

- **File:** `tests/test_engine_device.py:156-167` (`TestTryImportTorch::test_returns_none_or_module`).
- **Category:** Test Coverage — weak assertion.
- **Finding:** The test accepts either `None` or any object with a `backends` attribute. In the current MLX-only environment it always returns `None`, so the `hasattr` branch is never exercised. A regression that made `_try_import_torch` always return `None` (defeating the whole soft-import design) would still pass this test.
- **Suggested action:** Replace with two deterministic tests:
  - `test_returns_module_when_importable` — `monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(backends=..., cuda=...))` then assert `_try_import_torch() is sys.modules["torch"]`.
  - `test_returns_none_when_import_fails` — `monkeypatch.setattr(device_module.importlib, "import_module", _raise_import_error)`, assert `None`.
- **Confidence score:** Evidence 40, Impact 10, Actionability 25 → **75**.

### Notable strengths

- **Torch-soft design is a legitimate adaptation** of the reference, not a defect. llm-image-api has hard torch (uses it for diffusion); llm-tts-api is MLX-only. The platform-fallback path (Apple Silicon Darwin → `mps` without torch) correctly serves MLX consumers.
- **21 unit tests** cover all monkeypatched-torch combinations the AC requires.
- **Tests use a single seam (`_try_import_torch`) for monkeypatching** — clean and idiomatic.
- **`DeviceProfile` is `frozen=True, slots=True`** — immutable, hashable, cheap.
- **Coverage went UP** by ~1pp despite adding ~180 lines of source — the test suite covers the new module heavily.

### Recommended disposition

Mark S-005 **DONE** (zero must-fix). All four should-fix items are small. SF-10 + SF-11 + SF-12 are single-region edits in `device.py`. SF-13 is a test rewrite. Suggest grouping SF-10 + SF-11 into one PR (both touch device.py error/edge paths) and SF-12 + SF-13 into separate small commits.

---

## S-002 / Baseline performance capture — Review #1

**Verdict:** **CHANGES NEEDED** (one must-fix finding).
**Reviewed:** 2026-05-17
**Reviewer:** code-reviewer skill (single-pass, applied false-positive discipline given the small artifact surface — same approach as S-001).
**Scope:** files created/modified by S-002 per `sprint-impl-1.md` §S-002.

### Method

S-002 is a 130-line stdlib-only Python script plus a Markdown methodology doc plus a text fixture — too small a surface to merit a 5-agent dispatch. Single-pass review walking the five rubric categories transparently; only findings scoring ≥75 are recorded. Confidence-scoring against Evidence (0–40) + Impact (0–30) + Actionability (0–30).

### Must-fix findings

#### MF-1 — Hardcoded `"model": "qwen3-tts"` will fail the server's allow-list validation

- **File:** `scripts/perf_baseline.py:46-49` (inside `_one_request`).
- **Category:** Code Quality — correctness; the script does not work as shipped.
- **Finding:** The JSON body for each request hardcodes `"model": "qwen3-tts"`. The project's configured model defaults are:
  - `tts_mlx_audio_model_default = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"` (in `src/llm_tts_api/config.py:49`)
  - The speech endpoint validates `model` against the provider's allow-list (also driven from those env vars).
  - Existing tests (`tests/test_audio_speech.py:53, 67, 81`) all use the full HuggingFace-style name `"Qwen/Qwen3-TTS-12Hz-0.6B-Base"`.
  The lowercase `"qwen3-tts"` form is not in any allow-list, so every request will return `400 invalid_request_error param=model`. The script's `_one_request` only catches `URLError` — a 400 response does not raise `URLError`; instead `urlopen` raises `HTTPError` (a subclass of `URLError`, so the catch DOES fire). The script then exits 1 with `"warmup request failed: ..."` on the very first request. **The operator will never produce a baseline row** even on a perfectly healthy service.
- **Expected:** The model field must either (a) be an operator-supplied CLI flag with a sensible default that matches the project's typical config, or (b) be omitted entirely so the server uses its own default, or (c) be read from the same env vars the server reads (`TTS_MLX_AUDIO_MODEL_DEFAULT`, etc.).
- **Suggested action:** Add `--model` flag to the argparse setup with default `"Qwen/Qwen3-TTS-12Hz-0.6B-Base"` (matches `config.py`), thread through `_one_request`. Update the script's docstring usage example to show the flag. Verify by running against a local service before re-review.
  ```python
  p.add_argument(
      "--model",
      default="Qwen/Qwen3-TTS-12Hz-0.6B-Base",
      help="TTS model id (must be in the server's allow-list)",
  )
  # ...
  body = json.dumps({"model": args.model, "input": text, ...})
  ```
- **Confidence score:** Evidence 40, Impact 25 (correctness — script doesn't work as shipped), Actionability 30 → **95**.

### Discarded findings worth recording

| Finding | Why discarded |
|---|---|
| `_one_request` doesn't propagate an `X-Request-ID` header for log correlation | Nice-to-have for troubleshooting; the script isn't expected to thread through correlation ids itself. Operators can grep logs by timestamp. |
| No unit tests for the script | Defensible for a one-off measurement tool. Functions are simple, no business logic. |
| Default `--input` path is relative to CWD | Documented in the docstring's "Usage" example; operator runs the script from the project root. |
| `_git_sha` only catches `CalledProcessError, FileNotFoundError` | Other failures (e.g. permission errors) bubble up; harmless for a developer tool. |
| `_percentile` uses `statistics.quantiles` not numpy | Numpy would be a heavy dep for a script designed to avoid heavy deps. |

### Notable strengths

- Stdlib-only (`urllib.request` + `statistics`) — script runs in any Python env, no install step.
- `time.perf_counter` is the right clock for sub-second measurements.
- `resp.read()` drains the response body so timings reflect end-to-end synthesis, not just header arrival (call-site comment captures the intent).
- `_one_request` per-request timeout default of 600 s correctly anticipates first-warmup model-load latency for Voxtral-class models.
- Sample size default of 11 produces a legitimate p95 (one sample falls exactly at the 95th percentile by construction).
- The methodology doc has the right shape — append-only Measurements table, regression-policy section, explicit reference to NFR-PF-01.

### Recommended disposition

S-002 **remains READY-FOR-REVIEW until MF-1 is fixed**. The fix is small (one argparse flag + thread through one variable + docstring update). After the fix, re-run gates and request a re-review.

---

## S-002 / Baseline performance capture — Re-review #2

**Verdict:** **APPROVED**. MF-1 from Review #1 fixed; all gates re-verified.
**Reviewed:** 2026-05-17.

### Fix applied

- `scripts/perf_baseline.py`:
  - Added `--model` CLI flag with default `"Qwen/Qwen3-TTS-12Hz-0.6B-Base"` (matches `config.py`'s `tts_mlx_audio_model_default`).
  - `_one_request` signature extended to `(url, model, voice, text, timeout)`; threaded through.
  - Module docstring updated with `--model` in the usage example + an explicit warning that the value must be in the server's allow-list.
- `docs/perf/baseline.md`: methodology block updated with the `--model` flag.

### Verification

- `uv run python scripts/perf_baseline.py --help` shows the new `--model MODEL` flag.
- `ruff check / format`, `mypy --strict`, `pytest --cov` all green. 127 tests pass, 84.84% coverage.

### Outcome

Mark S-002 **DONE (scaffolding)** / BLOCKED-ON-USER for the final measurement row in `docs/perf/baseline.md`. The script will now correctly POST against the server's allow-list once the operator runs it.

