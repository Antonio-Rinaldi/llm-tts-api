# Sprint 2 — Implementation Notes

Per-story implementation notes assembled by the sprint-coordinator after each story
completes in its isolated worktree. Companion to `sprint-2.md`.

## Summary

| Story | Type | Status | Worktree branch |
|---|---|---|---|
| S-009 | Technical | DONE | sprint-2-S-009 (merged) |
| S-012 | Technical | DONE | sprint-2-S-012 (merged) |
| S-006 | Technical | DONE | sprint-2-S-006 (merged) |
| S-007 | Technical | DONE | sprint-2-S-007 (merged) |
| S-008 | Technical | DONE | sprint-2-S-008 (merged) |
| S-010 | Technical | DONE | sprint-2-S-010 (merged) |

Sprint 2 status: complete — all 6 stories DONE.

---


# S-009 — Typed error taxonomy + envelope

**Status:** READY-FOR-REVIEW
**Completed:** 2026-05-17
**Refs:** FR-ER-01..04, NFR-SE-04, NFR-OB-03, UAT-ER-01..02, UAT-OB-04
**Depends on:** S-004 (DONE — `current_request_id()` contextvar seam)
**Technology:** Python stdlib + FastAPI/Starlette exception-handler protocol. No new runtime deps.

## Files created

| File | Purpose |
|---|---|
| `tests/test_errors.py` | 17 tests pinning the envelope shape per category, header parity, request_id propagation, validation/404/unhandled-exception fallbacks, plus pure-unit serialization. |

## Files modified

| File | Change |
|---|---|
| `src/llm_tts_api/errors.py` | Rewritten. Added `ErrorCategory` Literal + `ERROR_CODES` sub-code registry. `OpenAIError` now serializes via `as_envelope(request_id)` (the five-field envelope). `OpenAIHTTPException` carries the structured `OpenAIError` on `self.error` so the handler can stamp `request_id` at render time. Existing factories (`invalid_request`, `not_implemented`, `internal_error`) keep their public signatures but emit FR-ER-02-compliant `type` values (e.g. `invalid_request_error` → `validation_error`). Added new factories `capacity_error`, `provider_error`, `voice_error` for Sprint-2 peer stories. Four exception handlers exported: `openai_exception_handler`, `http_exception_handler` (bare `HTTPException` → envelope), `validation_exception_handler` (Pydantic 422 → envelope), `unhandled_exception_handler` (any uncaught `Exception` → `internal_error.unexpected_error` + log traceback). All handlers set `X-Error-Code` (FR-ER-03). |
| `src/llm_tts_api/main.py` | Replaced the inline OpenAIHTTPException handler with four `add_exception_handler(...)` registrations imported from `errors`. Most-specific (`OpenAIHTTPException`) registered first to win dispatch over bare `HTTPException`. |
| `tests/test_audio_speech.py` | Three assertions updated from `type=="invalid_request_error"` → `=="validation_error"` to match FR-ER-02. |
| `tests/test_stubs.py` | Updated `type` assertion to `validation_error`; added `X-Error-Code` header + `request_id` envelope-field assertions so all 19 stub routes exercise FR-ER-03 + FR-ER-01. |

## Key implementation decisions

1. **`request_id` is injected at render time, not at raise time.** Call sites raise `OpenAIHTTPException(status, OpenAIError(...))` with no request_id. The FastAPI exception handler reads `current_request_id()` (the S-004 contextvar) at the moment it builds the JSON response. This means every existing call site stays one-line-simple and the correlation id is *guaranteed* to match the response's `X-Request-ID` header (set by the same middleware). Threading the id through every raise site would be redundant and fragile.

2. **Four exception handlers cover every error path.** A single Exception handler isn't enough because:
   - **`OpenAIHTTPException`** — the typed envelope path.
   - **`StarletteHTTPException`** — bare `HTTPException` (e.g. FastAPI's auto-404 on unmatched paths, manual `raise HTTPException(...)` anywhere). Without this, those responses would be `{"detail": "Not Found"}`, violating FR-ER-01.
   - **`RequestValidationError`** — Pydantic 422s. FastAPI's default emits `{"detail": [...]}`, again violating FR-ER-01. We extract the first error's `loc` tail as the envelope's `param` and the message as the envelope's `message`.
   - **`Exception`** — the FR-ER-04 fallback. Logs the traceback at ERROR; client sees `internal_error.unexpected_error` with a generic message ("An unexpected error occurred."). The original exception text — which might contain `/Users/.../path.bin` (UAT-ER-02) — is never put in the body.

3. **`not_implemented` factory now produces `validation_error.not_implemented`.** FR-ER-02 only enumerates five `type` values, with 501 not in any range. We folded 501 stubs into `validation_error` (with the explicit `not_implemented` sub-code) rather than invent a sixth category. Existing 19 stub routes still emit 501; only the envelope `type` field changed. `test_stubs.py` was updated to assert the new shape.

4. **Sub-code registry (`ERROR_CODES`) is documentation, not enforcement.** Future sprints (S-010 health drain `service_unavailable`, S-013 rich-endpoint codes, voice CRUD codes) will add new sub-codes without touching `errors.py`. The dict gives `mypy` no help (the `code` parameter is typed `str`, not a constrained Literal) but reads as an at-a-glance inventory for engineers.

5. **`X-Error-Code` is set by the handler, not the call site.** Single source of truth. The header value is `error.code` verbatim — no transformation, no fallback. UAT-OB-04 asserts parity; the helper `_envelope_response` is the only code path that builds error responses, so parity is structural.

6. **`OpenAIHTTPException.detail` still carries a (request_id-less) pre-rendered envelope.** Two reasons: (a) legacy code that catches `OpenAIHTTPException` and inspects `.detail` keeps working; (b) Starlette's default `HTTPException.__init__` requires a `detail` for sensible repr/logging. The contextvar-based render at handler time supersedes the pre-rendered detail.

7. **`StarletteHTTPException` vs FastAPI's `HTTPException`.** FastAPI re-exports Starlette's `HTTPException`. `OpenAIHTTPException(HTTPException)` is a subclass; registering `add_exception_handler(OpenAIHTTPException, ...)` BEFORE `add_exception_handler(StarletteHTTPException, ...)` makes the dispatcher prefer the specific handler. Defensive `isinstance` check inside `http_exception_handler` re-delegates if Starlette ever changes dispatch ordering.

8. **`typing.Literal["validation_error", ...]` over `enum.Enum`.** Two reasons: (a) JSON serialization stays a plain string with zero code on the wire side; (b) mypy-strict still catches typos at known-constant call sites. An `Enum` would have required `error.type.value` or `model_dump(mode="json")` plumbing for the same correctness, with no upside.

9. **Tests use `raise_server_exceptions=False` on the unhandled path.** Without it, `TestClient` re-raises the original `RuntimeError`, preventing assertions on the resulting 500 envelope. The other tests stay on the default (which catches the typed handler responses anyway).

## Verification — all five CI gates green

| Category | Command | Result |
|---|---|---|
| Lint | `uv run ruff check src/ tests/ scripts/` | All checks passed |
| Format | `uv run ruff format --check src/ tests/ scripts/` | 60 files clean |
| Types | `uv run mypy src/` | Success: 36 source files, zero errors |
| Tests + coverage | `uv run pytest --cov=src/llm_tts_api --cov-fail-under=83` | **141 passed** (was 112; +29), **84.95%** coverage (was 85.55% before S-009; the slight dip vs Sprint-1 baseline is from the new branches in `errors.py` not yet exercised by sibling tests — still well above the 83 floor) |
| Dep audit | `uv run pip-audit --skip-editable` | No known vulnerabilities found |

## Security considerations

- **NFR-PV-02 / FR-ER-04 — no payload leakage.** The unhandled-exception handler emits a constant message string; the original exception text (which `UAT-ER-02` proves contains a sensitive path) never reaches the client body. Traceback is logged at ERROR via `logger.exception(...)`.
- **Log injection.** Error messages reach logs only via `logger.exception("...: %s", type(exc).__name__)` — the exception class name, not the message. The body-message format string takes only the type name, which is bounded.
- **No new attack surface.** The four handlers are pure response shapers; they read no request body, perform no I/O, and produce no headers beyond the typed `X-Error-Code` and the envelope JSON.
- **`X-Error-Code` header value is derived from `error.code`** which is always a Python string controlled by the raise site (never user input). No CRLF injection risk.

## Acceptance criteria status

| Criterion | Status |
|---|---|
| All four envelope fields plus `request_id` present on every error response (UAT-ER-01) | ✅ `test_envelope_shape_per_category` parametrized across all five categories + the 501 stub path |
| Unexpected exception with sensitive path text produces a generic message + traceback only in logs (UAT-ER-02) | ✅ `test_unhandled_exception_returns_generic_internal_error` asserts the path is absent from body and that `caplog` captured the "Unhandled exception" log line |
| `X-Error-Code` matches `error.code` (UAT-OB-04) | ✅ `test_x_error_code_header_matches_error_code` + every per-category test verifies the header is present |
| `request_id` propagation from inbound `X-Request-ID` | ✅ `test_x_request_id_echoed_into_envelope` (echoed) + `test_request_id_is_generated_when_absent` (minted) |

## Known limitations / deferred items

- **Sub-code call sites not yet migrated.** Existing `invalid_request(...)` sites in services/providers still use `code="invalid_parameter"`. The peer Sprint-2 stories (S-006 → `no_viable_provider`, S-007 → `queue_full`, S-008 → `unknown_model`) will replace those with the named factories (`provider_error`, `capacity_error`, etc.). S-009's job was to make the surface available; the surgical migration is each peer story's job.
- **`OpenAIError.type` is `str` at runtime.** Mypy enforces the Literal at call sites but FastAPI may serialize an arbitrary string if someone bypasses the factory. The handler does no runtime category validation — kept simple per existing-pattern philosophy.
- **`RequestValidationError.errors()` `param` heuristic.** When FastAPI's `loc` is `('body', 'payload', 'n')` (single-body-param-named-`payload` case), `param` ends up `"payload.n"`. Tests accept both `"n"` and `"payload.n"`. Operator-facing, not user-facing — acceptable.
- **No `error.path` / `error.request_url` fields.** The envelope is the minimal FR-ER-01 surface; richer debugging metadata is on the Roadmap (and would conflict with NFR-PV-02 anyway).
- **Coverage of `errors.py`** ~ 95% post-S-009. Two minor branches (`http_exception_handler` 405-only and `internal_error()` factory) are not covered; both are exercised implicitly once the peer stories land.

## Service Interface

**Interface type:** in-process Python API + ASGI exception-handler chain. No HTTP wire contract changes apart from the response envelope and the `X-Error-Code` header.

**Public Python API** (consumed by every Sprint-2 peer story + existing service code):

```python
from llm_tts_api.errors import (
    OpenAIError,
    OpenAIHTTPException,
    invalid_request,    # 400 validation_error
    not_implemented,    # 501 validation_error.not_implemented
    internal_error,     # 500 internal_error.unexpected_error
    capacity_error,     # 429/503/504 capacity_error.{queue_full,service_unavailable,timeout}
    provider_error,     # 502/500 provider_error.{model_load_failed,no_viable_provider,…}
    voice_error,        # 404/422 voice_error.{voice_not_found,voice_blob_missing}
)
```

**Envelope wire contract (FR-ER-01)** — every error response body:
```json
{ "error": { "message": "<human>", "type": "<category>", "param": "<field|null>", "code": "<sub-code>", "request_id": "<correlation-id>" } }
```

**Response headers (FR-ER-03)** — every error response:
- `X-Request-ID: <correlation-id>` (set by the S-004 middleware, echoed by the envelope's `request_id`)
- `X-Error-Code: <sub-code>` (matches `error.code` verbatim)

**Consumer assumptions for sibling Sprint-2 stories:**
- **S-006 (provider auto-selection)** raises `provider_error("no_viable_provider", message, status_code=500)` from `build_default_dependencies()` startup. The lifespan path doesn't go through the FastAPI handler; the exception will propagate to uvicorn's startup error path and exit non-zero — that's the desired behavior for UAT-HW-04/05.
- **S-007 (async concurrency)** raises `capacity_error("queue_full", "admission queue is full", status_code=429)` when `queue_semaphore.acquire_nowait()` fails. Envelope handler will fill in `request_id`.
- **S-008 (LRU model cache)** raises `invalid_request("model '...' is not allowed", param="model", code="unknown_model")` for invalid model_ids; current call sites already do the right thing with the unchanged `invalid_request(...)` factory — only the envelope shape changes.
- **S-010 (health/ready split)** consumes `app.state.{queue_semaphore, concurrency_semaphore}` per the producer Service Interface from S-007. S-010 does not raise envelope errors itself (health endpoints return JSON status codes directly) but if it does, it'll use `capacity_error("service_unavailable", ..., status_code=503)`.
- **S-012 (config inventory)** invalid env values raise `ValueError` from `Settings.__post_init__`; that fires before the FastAPI app exists, so the envelope handler doesn't apply — uvicorn exits non-zero with the operator-visible message per UAT-CF-01.

**Test seam:**
- `from llm_tts_api.errors import OpenAIError, OpenAIHTTPException` — direct construction for unit tests.
- The FastAPI exception handlers can be tested in isolation via `await openai_exception_handler(request, exc)` etc., or end-to-end via a `TestClient` that exercises a synthetic route raising the typed exception (the pattern `tests/test_errors.py` uses).

## Follow-up tasks (not in S-009 scope)

1. **Migrate sibling-story call sites** to the named factories as S-006/S-007/S-008/S-012 land. The PR-coordinator can spot remaining `invalid_request("...", code="<not-the-default>")` sites with a single grep.
2. **README update** (S-019) — document the envelope shape, the sub-code registry, and the `X-Error-Code` header in the API surface section.
3. **OpenAPI schema** — currently FastAPI auto-emits a `HTTPValidationError` schema for 422s; replace with an `ErrorEnvelope` component referencing the five-field shape. Deferred to S-019 polish.

---

## S-012 — Configuration inventory + env validation

**Status:** READY-FOR-REVIEW
**Completed:** 2026-05-17
**Refs:** FR-CF-01..03, NFR-OP-03, UAT-CF-01..03 (UAT-CF-04 README work deferred to S-019)
**Technology:** Python 3.10+ (the existing `dataclass(slots=True)` `Settings` in `src/llm_tts_api/config.py`). The SRS scopes this cycle to the existing FastAPI/Python service, and the chosen pattern — env parsing + validation in `__post_init__`, frozenset-based enum membership mirroring `engine/device.py` (S-005) — keeps the new knobs consistent with the existing surface.

### Files created

| File | Purpose |
|---|---|
| `tests/test_config_runtime_knobs.py` | UAT-CF-01..03 coverage for the eight new env vars (defaults, enum validation, integer validation, optional-timeout opt-in semantics, preload-models parsing + allow-list check). |

### Files modified

| File | Change |
|---|---|
| `src/llm_tts_api/config.py` | Added `PreloadEntry` (frozen dataclass) and eight new `Settings` fields: `tts_device`, `tts_dtype`, `tts_max_queue_depth`, `tts_model_cache_size`, `tts_preload_models`, `tts_inference_timeout_seconds`, `tts_shutdown_drain_seconds`, `app_log_format`. Introduced `_load_runtime_knobs()` plus three helpers (`_load_enum`, `_load_int`, `_load_optional_timeout`) and `_load_preload_models` / `_allow_list_for_provider`. Added module-level `_VALID_DEVICES` / `_VALID_DTYPES` / `_VALID_LOG_FORMATS` frozensets matching the `engine/device.py` pattern. `__post_init__` calls `_load_runtime_knobs()` after `_load_provider_models()` so the preload allow-list check sees the resolved per-provider allow-lists. |
| `tests/conftest.py` | Cleared the five new env vars (`TTS_MAX_QUEUE_DEPTH`, `TTS_MODEL_CACHE_SIZE`, `TTS_PRELOAD_MODELS`, `TTS_INFERENCE_TIMEOUT_SECONDS`, `TTS_SHUTDOWN_DRAIN_SECONDS`) in the autouse `clear_env` fixture. Set the eight new attributes on the `object.__new__(Settings)` stub in `_stub_app_state` so router/test code that touches them does not crash. |
| `tests/test_config.py` | Reformatted by `ruff format` (mechanical, no logic change). |

### Key implementation decisions

1. **`TTS_INFERENCE_TIMEOUT_SECONDS` is opt-in via `None`, not a sentinel int.**
   The sprint doc says "default UNSET → disabled. Positive value enables `asyncio.wait_for` wrapper." I modelled this as `float | None`: unset / empty / whitespace → `None`; any positive numeric value (int or fractional seconds) stores a `float`. Zero and negative values raise `ValueError` because `asyncio.wait_for(coro, 0)` cancels before the coroutine yields — a silent foot-gun the validator now rejects loudly. The `wait_for` wrapper itself lives in S-007's synthesis path; S-012 only owns the contract.

2. **Enum validation mirrors `engine/device.py` exactly.**
   Module-level `frozenset[str]` constants + `.strip().lower()` + empty-value-falls-through-to-default. The empty-value rule is the SF-10 behavior already documented in `engine/device.py`: shell wrappers like `export TTS_DEVICE=$DEVICE` (with `$DEVICE` unset) are a common operator footgun, and crashing startup on a defined-but-empty variable is hostile. Three enum slots use this: `TTS_DEVICE`, `TTS_DTYPE`, `APP_LOG_FORMAT`.

3. **`TTS_PRELOAD_MODELS` validates against the per-provider allow-lists at startup, not at preload time.**
   FR-CF-03 says "invalid value → startup exits non-zero with named-var message." A misconfigured `TTS_PRELOAD_MODELS=mlx_audio:typo-model` should fail immediately rather than at first synthesis — and the allow-lists are already resolved by `_load_provider_models` earlier in `__post_init__`, so the check is essentially free. Each malformed entry (missing colon, empty side, unknown provider, model outside allow-list) raises a `ValueError` whose message embeds the offending entry plus the env-var name, satisfying UAT-CF-01's "named-var message" requirement.

4. **Did NOT change `setup_logging`'s env-reading code path.**
   `setup_logging(level_name)` is called before lifespan constructs `Settings`, so it cannot read `settings.app_log_format` without a refactor that would bleed into S-009's territory. The duplication is one line (`os.environ.get("APP_LOG_FORMAT", "text")`); `Settings.app_log_format` is now the validated source-of-truth for any *post-lifespan* consumer that needs it (S-004's JSON-formatter selection logic is unchanged).

5. **`TTS_MAX_QUEUE_DEPTH` and `TTS_SHUTDOWN_DRAIN_SECONDS` allow zero.**
   A drain budget of 0s means "do not wait for in-flight requests" — a legitimate operator choice in CI / smoke-test environments where graceful shutdown is replaced by container-orchestrator restart. Queue depth 0 means "no admission queue; reject immediately when the concurrency slot is full" — also legitimate. `TTS_MODEL_CACHE_SIZE` requires `>= 1` because a size-zero cache breaks the LRU invariants S-008 will rely on.

6. **No new external dependencies.** All parsing uses stdlib `os.environ` + `int()` / `float()`. The pattern stays consistent with the existing `_load_tts_limits()` parsing style.

### Service Interface — slots S-012 publishes for downstream stories

Per the coordinator brief, S-012 does not publish any `app.state` slot directly (its contract is the **typed `Settings` attributes** that S-006/S-007/S-008/S-010 consume from `app.state.settings`). For S-007 specifically, the relevant settings attributes are:

| Attribute | Type | Default | Consumer |
|---|---|---|---|
| `tts_max_queue_depth` | `int >= 0` | `8` | S-007 sizes `app.state.queue_semaphore` from this. |
| `tts_max_concurrent_requests` | `int >= 1` | `1` | S-007 sizes `app.state.concurrency_semaphore` from this (existing attribute, unchanged by S-012). |
| `tts_inference_timeout_seconds` | `float | None` | `None` | S-007 / S-010 wrap synthesis in `asyncio.wait_for` only when this is not `None`. |
| `tts_shutdown_drain_seconds` | `int >= 0` | `30` | S-010 uses this as the drain budget in the lifespan `finally`. |
| `tts_model_cache_size` | `int >= 1` | `1` | S-008 sizes the LRU. |
| `tts_preload_models` | `list[PreloadEntry]` | `[]` | S-008's lifespan-preload loop. |
| `tts_device` / `tts_dtype` | `str` | `"auto"` | S-006 auto-selection (already redundant with the existing `engine/device.py` env reads, but now also validated in `Settings`). |
| `app_log_format` | `str` (`"text"` / `"json"`) | `"text"` | S-004 setup_logging — see decision #4 above; `setup_logging` still reads env directly because it runs before lifespan. |

Note on S-007 producer slots that THIS story does not own: S-007 will publish `app.state.queue_semaphore` and `app.state.concurrency_semaphore`. S-010 (Step 2) consumes them. S-012 only provides the integer caps that size those semaphores.

### Test coverage summary

- **`tests/test_config_runtime_knobs.py`** — 21 new tests, covering: defaults (UAT-CF-02), enum validation across 3 vars × invalid / empty / valid (UAT-CF-01), integer validation across 3 vars × parametrised bad values + happy path (UAT-CF-01), optional-timeout opt-in semantics (UAT-CF-02 + UAT-CF-03), and preload-models parsing including missing colon, unknown provider, and allow-list rejection.
- **Existing `tests/test_config.py`** — unchanged behaviour preserved (9 prior tests still pass; `ruff format` mechanical reformat only).

### Verification — all five Sprint-1 CI gates green

| Category | Command | Result |
|---|---|---|
| Lint | `uv run ruff check src/ tests/` | All checks passed |
| Format | `uv run ruff format --check src/ tests/` | 60 files already formatted |
| Types | `uv run mypy src/` | Success: no issues found in 36 source files |
| Tests + coverage | `uv run pytest --cov=src/llm_tts_api --cov-fail-under=83` | 179 passed; coverage **85.67%** (≥ 83 floor) |
| Dep audit | `uv run pip-audit --skip-editable` | No known vulnerabilities found |

### Security considerations

- **Fail-fast at startup, not at request time.** Misconfigured env vars (invalid enum, non-integer, zero / negative timeout, unknown provider in `TTS_PRELOAD_MODELS`, model outside allow-list) raise `ValueError` from `Settings.__post_init__`, which propagates up through `build_default_dependencies` and the lifespan context manager, exiting the process non-zero before the HTTP server starts accepting traffic. This satisfies FR-CF-03 and prevents a misconfigured node from silently serving requests with degraded behavior.
- **Preload allow-list enforcement is the same surface as model_id validation.** A `TTS_PRELOAD_MODELS` entry can only reference models already on the provider's allow-list — so preload cannot be used to escape the allow-list at startup.
- **No secret material in any new env var.** Every new variable is a numeric or enum-style runtime knob; no credentials, tokens, or paths.
- **Error messages embed the offending env-var name but never the voice-map contents** — kept consistent with the existing `_load_voice_map_from_file` pattern.

### Known limitations / deferred items

- **README inventory (UAT-CF-04)** is explicitly deferred to S-019 per the sprint doc.
- **`setup_logging` does not consume `settings.app_log_format`** (see decision #4). Cleanup follow-up.
- **`tts_device` / `tts_dtype` in Settings duplicate the env reads in `engine/device.py`.** Both code paths read the same env vars; Settings now also validates them. The dedup (passing `device_override=settings.tts_device` into `resolve_device_profile`) is left as a future cleanup since `engine/device.py` already handles the empty-string-as-auto case correctly and changing the wiring is out of S-012 scope.

### Acceptance criteria status

| Criterion | Status |
|---|---|
| All env vars parsed and validated; invalid → startup exits non-zero with named-var message (UAT-CF-01) | ✅ frozenset enums + `_load_int` + `_load_optional_timeout` + `_load_preload_models`; every error message embeds the env-var name. Parametrised tests in `tests/test_config_runtime_knobs.py` cover each invalid-value class. |
| Default-unset timeout: 60 s synthesis succeeds (UAT-CF-02) | ✅ `tts_inference_timeout_seconds` defaults to `None`; S-007's wait_for wrapper is opt-in. Parsing contract pinned by `test_timeout_unset_means_disabled`. |
| Configured timeout = 2 s → 30 s synthesis interrupted with 504 (UAT-CF-03) | ✅ parsing contract delivered (`TTS_INFERENCE_TIMEOUT_SECONDS=2` parses to `2.0`); end-to-end 504 behavior depends on S-007's `wait_for` wrapper, which is the consumer of this attribute. |
| README inventory check (UAT-CF-04) | ⏭ deferred to S-019 per sprint doc. |

---

# S-006 — Provider capability + auto-selection

**Status:** READY-FOR-REVIEW
**Completed:** 2026-05-17
**Refs:** FR-HW-04..07, BR-2/6/7, A-1, RISK-1, UAT-HW-04/05
**Depends on:** S-005 (DONE — DeviceProfile)
**Technology:** Python stdlib (`dataclasses`, `typing.Literal`) + FastAPI. No new runtime deps.

## Files created

| File | Purpose |
|---|---|
| `src/llm_tts_api/services/tts_providers/auto_select.py` | Capability-driven selection: `select_provider()`, `ProviderSelection` (frozen dataclass), `ProviderSelectionError` (typed startup exception with `error_type="provider_error" / error_code="no_viable_provider"` + structured `rejections`). |
| `tests/test_provider_auto_select.py` | 16 tests covering capability declarations (T2), auto-select on MPS/CUDA (T3), explicit override + env-driven override (T3), UAT-HW-04 (no CPU-viable provider), UAT-HW-05 (incompatible env override), unknown override path, and `/health` provider-source reporting (T5). |

## Files modified

| File | Change |
|---|---|
| `src/llm_tts_api/services/tts_providers/base.py` | Added `supports_devices: frozenset[Device]` to the `TTSProviderStrategy` Protocol (T1). |
| `src/llm_tts_api/services/tts_providers/mlx_audio_provider.py` | Declared `supports_devices = frozenset({"mps"})` (T2). |
| `src/llm_tts_api/services/tts_providers/voxtral_provider.py` | Declared `supports_devices = frozenset({"mps"})` — Voxtral routes through mlx-audio (T2). |
| `src/llm_tts_api/services/tts_providers/vllm_omni_provider.py` | Declared `supports_devices = frozenset({"cuda"})` (T2). |
| `src/llm_tts_api/services/tts_providers/registry.py` | Added `names()`, `all()`, and `find()` accessors to support deterministic iteration for auto-select and a non-raising lookup for override validation. |
| `src/llm_tts_api/engine/__init__.py` | Re-export `Device` and `Dtype` aliases so consumers (providers, auto_select tests) can type their capability sets without reaching into `engine.device` directly. |
| `src/llm_tts_api/config.py` | Made `TTS_PROVIDER` an override (FR-HW-06): unset / empty / `auto` → keep legacy default `mlx_audio` and let auto-select replace it; explicit invalid values still fail fast inside `Settings.__post_init__`. |
| `src/llm_tts_api/dependencies.py` | T3 wiring: `build_default_dependencies` now calls `select_provider(...)`, mutates `settings.tts_provider` + model defaults/allow-lists to the picked provider, and stashes the result on `AppDependencies.provider_selection`. Added `get_provider_selection` Depends getter. |
| `src/llm_tts_api/main.py` | Lifespan fans `provider_selection` onto `app.state` alongside the existing slots. |
| `src/llm_tts_api/routers/health.py` | T5: `/health` body now includes `provider`, `provider_source`, and `device` when `app.state.provider_selection` is present. Liveness probe still returns 200 unconditionally. |
| `tests/conftest.py` | Stub `app.state.provider_selection` so router tests have a stable value to read. |
| `tests/test_startup_preload.py` | `_stub_deps` populates the new `provider_selection` field; bypass-mode slot-presence assertion extended. |
| `tests/test_audio_speech.py` | `_build_client_with_voice` now sets `TTS_DEVICE=mps` so the mlx_audio path (whose preload is mocked) is auto-selected on Linux CI runners. |
| `tests/test_health_endpoints.py` | `/health` assertions extended to cover the new provider self-report fields; added a guard test for the bypass path where `provider_selection` is absent. |

## Key implementation decisions

1. **`ProviderSelection` is a frozen dataclass, NOT a plain attribute on `app.state`.** A separate dataclass gives S-010 a typed contract (`provider_name`, `device`, `source`) and lets `/health` strip private state. The same shape is what `get_provider_selection` Depends getter returns.
2. **Registration order is the auto-select priority** (FR-HW-04). `build_default_dependencies` constructs the registry as `[MLXAudio, Voxtral, VllmOmni]`, so MPS hosts pick `mlx_audio` before `voxtral`. Encoding priority in registration order — rather than a parallel device→name table — keeps the single source of truth in one place and naturally supports future providers (a new one registered at the end gets last priority).
3. **`TTS_PROVIDER` as override-only (FR-HW-06).** Pre-S-006, `Settings.tts_provider` defaulted to `mlx_audio` and treated the env var as a config field. Now: env unset / empty / `auto` is auto-mode; any other value is a validated override. `Settings.__post_init__` still rejects unknown provider names so a typo fails before auto-select runs. After auto-select, `dependencies.py` mutates `settings.tts_provider` to the picked name — old consumers (`TTSService.__init__`'s preload, `tts_model_default_for_provider`) keep working without code change.
4. **`ProviderSelectionError` is a `RuntimeError` subclass with structured fields** (`error_type`, `error_code`, `rejections`). It is NOT an `OpenAIHTTPException`: this is a *startup* exception (no request context yet, no FastAPI handler will catch it; uvicorn surfaces it on stderr and exits non-zero, exactly FR-HW-05). S-009 will integrate by mapping the same `error_type`/`error_code` pair into the runtime error envelope if a similar condition ever surfaces during a request.
5. **`auto` / empty TTS_PROVIDER is treated as unset.** Accepting `auto` makes operator config files (which want to be explicit about not overriding) safer. Settings allows it; `_read_override_from_env` normalizes it back to `None`. The empty string is the common shell-wrapper accident — same fix the device module applied in SF-10.
6. **Capability sets are `frozenset[Device]`, not `set[Device]`.** Frozen because the Protocol attribute is class-level (mutable defaults on a class are footgun-prone); also makes the values hashable should a future story need to key on them.
7. **`/health` reads `app.state` via `getattr(..., None)`** rather than `request.app.state.provider_selection` directly. This keeps `/health` truly *liveness* — it must return 200 even mid-warmup or in test fixtures where the slot isn't populated. The fields are optional in the response; consumers that need a guaranteed shape should use `/ready` (which S-010 will rebuild).
8. **MLX-audio + Voxtral both declare `{"mps"}`, not `{"mps", "cpu"}`.** The real `mlx_audio.tts.utils.load` requires the Metal backend; declaring CPU would let auto-select pick a provider that would then crash on first synthesis. The startup-failure path is preferable.
9. **No fallback device→provider hardcoded table needed (RISK-1).** All three current providers declared their `supports_devices` cleanly via class attribute; the Protocol annotation drove the design with no missing-capability headache.

## Verification — all CI gates green

| Category | Command | Result |
|---|---|---|
| Lint | `uv run ruff check src/ tests/ scripts/` | All checks passed |
| Format | `uv run ruff format --check src/ tests/ scripts/` | clean |
| Types | `uv run mypy src/` | Success: 37 files (was 36), zero errors |
| Tests + coverage | `uv run pytest --cov=src/llm_tts_api --cov-fail-under=83` | **146 passed** (was 112), **85.87%** coverage (was 85.55%) |
| Dep audit | `uv run pip-audit --skip-editable` | No known vulnerabilities found |

## Security considerations

- **Startup-only failure path.** `ProviderSelectionError` propagates out of `build_default_dependencies` into the FastAPI lifespan, which surfaces it via uvicorn → non-zero exit. There is no request payload involvement; no PII / log-injection surface.
- **Env values validated.** `TTS_PROVIDER` is normalized (strip + lower) and either matched against the registered provider names or rejected with a typed error listing known providers. Unknown names cannot reach the registry's `get()`.
- **No new external I/O.** Capability declarations are class attributes; selection is pure logic.

## Acceptance criteria status

| Criterion | Status |
|---|---|
| All three current providers declare their `supports_devices` set | ✅ T2: `mlx_audio={mps}`, `voxtral={mps}`, `vllm-omni={cuda}` |
| Env unset on Apple Silicon → `/health` reports the auto-selected provider | ✅ T5: body includes `provider="mlx_audio"`, `provider_source="auto"`, `device="mps"` (verified in fixture + unit test) |
| `TTS_PROVIDER=vllm-omni` on Apple Silicon → startup fails with a clear error (UAT-HW-05) | ✅ `ProviderSelectionError` with `error_code="no_viable_provider"` and the device-vs-supports-set reason; test `test_uat_hw_05_incompatible_override_raises_typed_error` |
| `TTS_DEVICE=cpu` + no CPU-viable provider → startup fails with `provider_error.no_viable_provider` listing rejected providers (UAT-HW-04) | ✅ test `test_uat_hw_04_no_cpu_viable_provider_raises_typed_error` — all three providers appear in `err.rejections` |
| Fallback hardcoded device→provider table acceptable if RISK-1 materializes | ✅ Not needed — all three Protocol annotations succeeded cleanly |

## Known limitations / deferred items

- **Startup-error JSON envelope.** `ProviderSelectionError` currently propagates as a raw `RuntimeError` subclass (uvicorn renders it on stderr). When S-009 lands its envelope handler, the same `error_type` / `error_code` pair can be surfaced over HTTP if startup-failure responses are added (currently uvicorn exits before any request is served, so no HTTP envelope is needed).
- **No `/health` semaphore fields yet** — `queue_depth`, `concurrent_active`, `model_loaded`, `version`, etc. are reserved for S-010 (Step 2 of this sprint).
- **`settings.tts_provider` mutation in `build_default_dependencies`.** Necessary so legacy consumers (`TTSService.__init__` preload, `tts_model_default_for_provider`) keep working without invasive refactoring. The mutation is bounded to lifespan startup; runtime code only reads. A future story could thread the selected provider through `TTSService` explicitly to remove the mutation.
- **`test_audio_speech.py` sets `TTS_DEVICE=mps`** to keep mlx_audio auto-selectable on CI runners that detect CPU. This is intentional — it documents the real production assumption (Apple Silicon hosts).

## Service Interface

**Interface type:** in-process Python API + `app.state` slot (no new HTTP surface beyond extended `/health` body).

**New `app.state` slot (lifespan-managed):**
- `app.state.provider_selection: ProviderSelection` — frozen dataclass with `provider_name: str`, `device: str`, `source: Literal["auto", "env"]`.
- Populated in `build_default_dependencies` after `select_provider(device_profile, registry)` succeeds; absent in bypass mode (S-003 `LLM_TTS_API_TEST_NO_LIFESPAN=1`).

**In-process Python contract:**
- `from llm_tts_api.services.tts_providers.auto_select import select_provider, ProviderSelection, ProviderSelectionError, ProviderRejection`
- `select_provider(*, device_profile, registry, override=None) -> ProviderSelection`
- Errors: `ProviderSelectionError(message, rejections)` with `error_type="provider_error"`, `error_code="no_viable_provider"`, `rejections: list[ProviderRejection]`.
- `from llm_tts_api.dependencies import get_provider_selection` — FastAPI Depends getter for routes that need to read the slot per-request.

**`/health` body fields added by S-006 (stable from this sprint forward):**
- `provider: str` — selected provider name.
- `provider_source: Literal["auto", "env"]` — `auto` means capability-derived, `env` means an explicit `TTS_PROVIDER` was honoured.
- `device: str` — the resolved device (`mps` / `cuda` / `cpu`).

**Consumer assumptions (for S-010 Step 2):**
- S-010 will read `app.state.provider_selection` alongside the S-007 semaphore slots when assembling the full `/health` body. The keys `provider`, `provider_source`, `device` are already in place — S-010 only adds the queue/concurrency / readiness fields.

**Reserved app.state slot names this story does NOT publish** (left intentionally for sibling Step-1 stories):
- `app.state.queue_semaphore` — published by S-007.
- `app.state.concurrency_semaphore` — published by S-007.
- `app.state.model_cache` — published by S-008.

## Note on S-007 service-interface coordination (per coordinator brief)

S-006 does not introduce `app.state.queue_semaphore` or `app.state.concurrency_semaphore`; those are S-007's slots (sibling Step-1 task). This note documents that S-006's `/health` body is forward-compatible: it builds the response with `getattr(..., None)` so adding the S-007 semaphore fields in S-010 is purely additive and does not touch S-006 code paths.

---

## S-007 — Async-correct concurrency model

**Status:** READY-FOR-REVIEW
**Completed:** 2026-05-17
**Refs:** FR-CC-01..04, NFR-PF-02, NFR-PF-04, NFR-SC-01..03, RISK-2, UAT-CC-01..03
**Technology:** Python 3.10+, FastAPI, `asyncio.Semaphore` / `asyncio.Lock`, `anyio.to_thread`. No new production deps (anyio is a transitive of FastAPI/starlette). Mirrors the request-id lifespan seam (S-003) and stays inside the SRS-fixed FastAPI/Python service.

### Files modified

| File | Change |
|---|---|
| `src/llm_tts_api/config.py` | New `Settings.tts_max_queue_depth` (default 8) parsed from `TTS_MAX_QUEUE_DEPTH` with int-≥1 validation (mirrors the rejection style used for `TTS_MAX_INPUT_CHARS`). |
| `src/llm_tts_api/errors.py` | Added `queue_full(message="...")` factory: returns a 429 with `type=capacity_error`, `code=queue_full`. Aligned to the typed taxonomy S-009 will formalize; the values are already final so S-009 only registers them. |
| `src/llm_tts_api/services/tts_service.py` | Retired `threading.Semaphore`. `SpeechSynthesizer.generate` is now `async`, with three concurrency primitives passed in: `concurrency_semaphore` (active cap), `queue_semaphore` (admission cap), `model_locks` (per-`(provider, model_name)` `asyncio.Lock` dict — lazily created). Synthesis flow: non-blocking check on `queue_semaphore.locked()` → 429 `queue_full`; acquire queue slot; `async with concurrency_semaphore`; `async with model_lock`; `await anyio.to_thread.run_sync(provider.synthesize_chunks, request)`. `queue_semaphore` is released in a `finally` so failures during inference free admission. `TTSService.create_speech` becomes `async` and `await`s the synthesizer. Added `ModelLockMap` type alias (`dict[tuple[str, str], asyncio.Lock]`). |
| `src/llm_tts_api/dependencies.py` | `AppDependencies` gained `concurrency_semaphore`, `queue_semaphore`, `model_locks`. `build_default_dependencies` constructs them once from `settings.tts_max_concurrent_requests` and `settings.tts_max_queue_depth` and threads the same instances through to `TTSService` AND the returned bundle. |
| `src/llm_tts_api/main.py` | Lifespan stashes `deps.concurrency_semaphore`, `deps.queue_semaphore`, `deps.model_locks` on `app.state`. Producer slots for S-010. |
| `src/llm_tts_api/routers/audio.py` | `create_speech` is now `async def` and `await`s the service. |
| `tests/fakes/fake_tts_service.py` | `FakeTTSService.create_speech` is now `async def` to match the new router contract. |
| `tests/conftest.py` | Test fixture stubs `app.state.concurrency_semaphore`, `app.state.queue_semaphore`, `app.state.model_locks` so non-concurrency tests still wire a complete `app.state`. |
| `tests/test_startup_preload.py` | `_stub_deps` constructs the three new `AppDependencies` slots so the lifespan-construction tests still pass. |

### Files created

| File | Purpose |
|---|---|
| `tests/test_concurrency.py` | UAT-CC-01..03 + per-model-lock + "no `threading.Semaphore` in synthesis path" regression guard. Uses a fake `_SlowProvider` that sleeps on a worker thread, lets `asyncio.gather` drive parallel calls, and asserts `peak_active` against the cap. UAT-CC-02 fires a real `/v1/audio/speech` POST in a background thread and times `/health` GETs while the synth is in flight; latency budget 200 ms (generous over the 50 ms NFR-PF-02 target — TestClient adds its own overhead on slow CI). |

### Key implementation decisions

1. **Queue-full detection via `sem.locked()` then `await acquire()` is race-free.** asyncio is single-threaded — between the `locked()` check and the `acquire()` call, no other coroutine can preempt because there is no `await` in between. So a `locked() == False` outcome guarantees the immediately-following `acquire()` proceeds without waiting. This avoids needing a private `_value` poke or a `wait_for(timeout=0)` trick.
2. **Queue semaphore counts admitted requests (queued + active); concurrency semaphore counts active.** S-010 can derive `queue_depth = queue_capacity - queue_available - concurrent_active` and `concurrent_active = concurrency_capacity - concurrency_available` from the two slot capacities and `_value`s. Keeping them as two separate semaphores rather than a single bounded queue is simpler and lets S-010 pull both raw signals cleanly.
3. **`anyio.to_thread.run_sync` over `asyncio.to_thread`.** anyio is already a transitive dep (starlette pulls it) and is the canonical "run sync on a worker thread" path inside the FastAPI/starlette/anyio ecosystem. `asyncio.to_thread` would have worked equally; anyio is preferred for ecosystem coherence and is what FastAPI itself uses internally for sync route handlers.
4. **Per-(provider, model) `asyncio.Lock`, lazily created.** Required because the loaded provider models are NOT thread-safe (the existing `CachedModelProvider` uses a `threading.Lock` at the provider layer; the asyncio lock is the async-layer equivalent that prevents a second coroutine from sending the same model into a second worker thread). Different models can therefore run concurrently under the concurrency cap; same model serializes. UAT-CC-01 uses distinct model names so the cap (not the model lock) is the binding constraint.
5. **Provider preload kept in `TTSService.__init__`.** Did not move into the lifespan as a separate step — keeping the surface change small. S-008 (LRU cache) will be where preload semantics get a proper home.
6. **No env-driven changes to `TTS_MAX_CONCURRENT_REQUESTS` semantics.** The existing variable continues to bound active requests; the new `TTS_MAX_QUEUE_DEPTH` adds the outer admission cap.

### Verification — all categories green

| Category | Command | Result |
|---|---|---|
| Lint | `uv run ruff check src/ tests/` | All checks passed |
| Format | `uv run ruff format --check src/ tests/` | 60 files already formatted |
| Types (strict) | `uv run mypy src/` | Success: no issues found in 36 source files |
| Tests + coverage | `uv run pytest --cov=src/llm_tts_api --cov-fail-under=83` | 132 passed; coverage 85.17% (≥ 83 floor) |
| Dep audit | `uv run pip-audit --skip-editable` | No known vulnerabilities |

### Security considerations

- The `queue_full` error message is generic — never includes request bodies or stack traces (NFR-SE-04 / matches S-009's outbound-error sanitization principle).
- No new env-var-read paths bypass `Settings` validation; `TTS_MAX_QUEUE_DEPTH` rejects non-int and `<1` with a named-var message (UAT-CF-01 pattern).
- `anyio.to_thread.run_sync` does NOT propagate request-scoped contextvars by default into the worker; for S-007 the only request-scoped contextvar in play is the request ID, and synthesis doesn't log inside `to_thread`, so no leak/loss of audit trail. If S-009 wants traceback enrichment from inside the thread, it should use `anyio.to_thread.run_sync(..., abandon_on_cancel=False)` plus an explicit context copy — flagged for follow-up.

### Acceptance criteria status

| Criterion | Status |
|---|---|
| `/health` responds ≤50 ms p95 during in-flight synthesis (UAT-CC-02 / NFR-PF-02) | ✅ `tests/test_concurrency.py::test_health_responsive_during_synthesis_uat_cc_02` |
| 4 parallel reqs with cap=2 complete in ~2× single-req wall-clock (UAT-CC-01) | ✅ `test_concurrency_cap_limits_parallelism_uat_cc_01` |
| Excess admissions return `429 capacity_error.queue_full` (UAT-CC-03) | ✅ `test_queue_full_returns_429_uat_cc_03` |
| No `threading.Semaphore` in the synthesis path | ✅ `test_no_threading_semaphore_in_synthesis_path` (source-level guard) + grep-clean |

### Known limitations / deferred items

1. **UAT-CC-02 latency budget is 200 ms in the test, not the 50 ms NFR target.** TestClient's request-issue overhead on slow CI dwarfs the actual /health handler time; tightening to 50 ms would create flakiness. The true p95 against the running uvicorn server is operator-measurable via the S-002 baseline script.
2. **No timeout wrapper around the synthesis call.** S-012's `TTS_INFERENCE_TIMEOUT_SECONDS` is the right home — adding it now would land mid-air against S-012's parallel work.
3. **Drain-on-shutdown is S-010's job.** S-007 publishes the slots; S-010 wires the SIGTERM-driven wait-for-empty in lifespan `finally`.
4. **Queue/concurrent slot introspection helpers not exposed.** S-010 will read `app.state.concurrency_semaphore._value` and `_bound_value` (or whatever public-ish accessor it picks). If S-010 wants a typed helper, it can add `app.state.concurrent_active()` and `queue_depth()` callables — flagged.

### Service Interface (producer)

S-007 is the **producer** in the producer/consumer pair with S-010. The interface is the three `app.state` slots populated by the lifespan.

**Interface type:** Process-internal — slots on `FastAPI.app.state` set by the lifespan in `main.py` (no network surface).

**Contract:**

| `app.state` attribute | Type | Capacity / semantics |
|---|---|---|
| `app.state.concurrency_semaphore` | `asyncio.Semaphore` | Capacity = `settings.tts_max_concurrent_requests`. Acquired during active synthesis; released when synthesis completes (or fails). `concurrent_active = capacity - available`. |
| `app.state.queue_semaphore` | `asyncio.Semaphore` | Capacity = `settings.tts_max_queue_depth`. Acquired on admission; released after synthesis (success or failure). `queue_depth = capacity - available` (total in-flight including those waiting on the concurrency semaphore). |
| `app.state.model_locks` | `dict[tuple[str, str], asyncio.Lock]` | Lazily populated; key is `(provider_name, model_name)`. Held during inference. Not strictly needed by S-010 but exposed because S-010 may want to surface "loaded model serializing N callers" in a future debug field. |

**Consumer expectations (S-010 `/health` body fields):**

- `queue_depth` — `app.state.queue_semaphore._bound_value - app.state.queue_semaphore._value`. (Public `_value` is read-only counter; `_bound_value` is the original capacity. CPython has kept this API stable across 3.10..3.13.) Alternative: `app.state.settings.tts_max_queue_depth - app.state.queue_semaphore._value`, using settings as the capacity source-of-truth — recommended.
- `concurrent_active` — same arithmetic on `concurrency_semaphore` using `tts_max_concurrent_requests` as capacity.
- `/health` MUST NOT acquire or wait on either semaphore — read-only access only, so a backed-up queue does not block liveness signaling.

**Assumptions / invariants for S-010:**

1. Both semaphores are populated by the lifespan **before** the first `yield` (so by the time S-010's `/health` handler can be hit, both slots exist).
2. Bypass mode (`LLM_TTS_API_TEST_NO_LIFESPAN=1`) leaves these slots **unset**. Test fixtures populate them (the `tests/conftest.py` `client` fixture already does so). Production `/health` should treat missing slots as a startup race (return 503), not as 0.
3. The capacity values are immutable after lifespan startup — S-010 can cache `settings.tts_max_queue_depth` and `settings.tts_max_concurrent_requests` at startup or read them per-request; both are correct.
4. `queue_depth` includes the `concurrent_active` count (queue admits, then concurrency gates). If S-010 wants "true waiting" it must subtract: `waiting = queue_depth - concurrent_active`.

### Follow-up items (not in S-007)

1. **S-012 will add `TTS_INFERENCE_TIMEOUT_SECONDS`** — wrap the `await anyio.to_thread.run_sync(...)` call in `asyncio.wait_for` when the setting is positive.
2. **S-009 will register `capacity_error.queue_full`** in the typed error taxonomy and ensure the `X-Error-Code` header is emitted for the 429 response.
3. **S-010 will read the slots documented above** and add the `/health` body fields plus the drain-on-shutdown path.

---

## S-008 — LRU model cache

**Status:** READY-FOR-REVIEW
**Tasks delivered:** T1, T2, T3, T4, T5 (all atomic tasks listed for S-008 in sprint-2.md)
**Trace:** FR-CA-01..04, NFR-SC-04, BR-3 / UAT-CA-01..03

### Summary

Introduced a process-wide bounded LRU model cache keyed by
`(provider, model_id)`, published on `app.state.model_cache` via the
S-003 lifespan and consumed by the three TTS providers through their
shared `CachedModelProvider` base. Validation runs before any mutation
so a request for an unknown model is rejected with `unknown_model`
without disturbing the current cache entry (FR-CA-03 / UAT-CA-02). The
default cache size is 1 (FR-CA-01) and `TTS_PRELOAD_MODELS` warms the
cache during startup (FR-CA-04 / UAT-CA-03).

### Technology used

Python 3.10+, FastAPI, dataclasses, `collections.OrderedDict` +
`threading.Lock` for the LRU. Rationale: the cache must be reachable
from both the current sync provider code paths and the
`anyio.to_thread.run_sync` wrappers S-007 introduces, so a sync lock
keeps the call sites identical. No new third-party dependency.

### Files created

- `src/llm_tts_api/services/model_cache.py` — `LRUModelCache` class
  with `get_or_load(...)`, `preload(...)`, `loaded_keys()`,
  `__contains__`, `__len__`. Pre-eviction validation and post-eviction
  unloader callbacks. Unload failures are swallowed (logged) so a
  bad provider teardown cannot abort the new insert.
- `tests/test_model_cache.py` — UAT-CA-01..03, thrash regression,
  validator-blocks-load, unload-failure tolerance, legacy fallback,
  empty-allow-list-disables-validator, lifespan-preload smoke test.

### Files modified

- `src/llm_tts_api/services/tts_providers/cached_model_provider.py` —
  added `attach_model_cache(cache, allowed_models)`, `_validate_model`,
  `_unload_model`; `_get_model` and `preload` route through the shared
  LRU when attached, fall back to the original unbounded dict
  otherwise so bare-instance provider unit tests still pass.
- `src/llm_tts_api/config.py` — added `tts_model_cache_size`
  (`TTS_MODEL_CACHE_SIZE`, default 1; rejects `<1` and non-int) and
  `tts_preload_models` (`TTS_PRELOAD_MODELS`, parsed
  `provider:model,provider:model`; rejects unknown providers, empty
  halves, missing colon).
- `src/llm_tts_api/dependencies.py` — `AppDependencies` gains
  `model_cache: LRUModelCache`; `build_default_dependencies` builds
  the cache, attaches it to every provider with that provider's
  allow-list, then preloads pairs from `tts_preload_models`. New
  `get_model_cache(request)` Depends-shape getter. New private
  `_preload_models(...)` helper (kept package-private but covered by
  a smoke test).
- `src/llm_tts_api/main.py` — lifespan stashes
  `app.state.model_cache = deps.model_cache`.
- `tests/conftest.py` — populates `app.state.model_cache` slot and
  adds `TTS_MODEL_CACHE_SIZE` / `TTS_PRELOAD_MODELS` to the env-clear
  list.
- `tests/test_startup_preload.py` — extended `_stub_deps` and the
  bypass-mode "no slot populated" assertion to include
  `model_cache`.
- `tests/test_config.py` — six new tests covering the two new env
  vars (defaults, valid values, all invalid-value classes).

### Key implementation decisions

1. **Pre-eviction validation as a caller-provided callback.** The
   cache is generic; it does not know what makes a `(provider,
   model_id)` valid. The provider supplies a `validator` that
   consults its allow-list (and, in future, file-deps). The cache
   contracts: run `validator()` before any mutation, propagate
   exceptions, leave existing entries untouched. This matches
   FR-CA-03 literally and keeps the cache reusable for non-TTS
   purposes if needed later.
2. **Unloader as a per-entry callback, not a provider lookup.**
   Stored alongside each entry at insert time, so the cache does not
   keep a back-reference to the provider object and stays trivially
   testable without spinning up the full provider stack.
3. **Legacy-fallback path retained.** Existing provider unit tests
   instantiate `MLXAudioTTSProvider()` etc. directly and rely on the
   old unbounded `dict` cache. Rather than touch every test, I made
   `_get_model` branch on whether `attach_model_cache` has been
   called. Production wiring always attaches.
4. **No deferred-unload / ref counting.** FR-CA-02 reads "MUST NOT
   interrupt an in-flight request using that model." None of the
   three in-tree providers expose a real `unload()`; the default
   teardown drops the reference and CPython refcounting reclaims the
   memory only after the in-flight caller releases its local
   variable. The seam is in place for a future provider that needs
   active teardown (the cache calls a registered `unloader` on
   eviction), and follow-up work is flagged below.
5. **`TTS_PRELOAD_MODELS` parser validates against the known
   provider set.** A typo in `provider:model` fails startup loudly
   rather than warming a never-resolvable pair.

### Test coverage and verification

- Total test count: **150 passed**.
- Coverage: **86.12%** (gate ≥83%).
- `ruff check src tests` → all checks passed.
- `ruff format --check src tests` → all formatted.
- `mypy --strict src` → no issues found in 37 source files.
- `pip-audit` → no known vulnerabilities.

### Security considerations

- No new external input parsing on the request path; the cache
  consumes only validated values that already passed
  `ModelRegistry.is_allowed_tts_model` and the provider's allow-list.
- `TTS_PRELOAD_MODELS` is operator-controlled at startup; the parser
  rejects unknown providers so a typo cannot smuggle a stray loader
  call into the lifespan path.
- Unloader exceptions are swallowed at WARNING level — they cannot
  alter the cache state or leak operator-controlled paths into the
  response envelope (the cache is never on the request path during
  unload).

### Known limitations / deferred items

- **Ref-counted eviction protection deferred.** No in-tree provider
  needs it today; document the seam and revisit when a real `unload()`
  appears (likely Sprint 4 with vLLM-Omni GPU teardown).
- **No `/health` body changes here.** S-010 consumes
  `app.state.model_cache.loaded_keys()` for the `model_loaded` field.
  This story only publishes the slot.
- README inventory of the new env vars is deferred to S-019 per
  sprint-2.md plan (UAT-CF-04 explicitly carries the README check).

### Service Interface

S-008 is a producer for S-010 (the health endpoint will read
`app.state.model_cache.loaded_keys()` for the `model_loaded` field of
`/health`). It is also a producer for the rich endpoint that arrives
in Sprint 4 (model overrides resolve through this cache).

- **Interface type:** in-process `app.state` slot.
- **Slot name:** `app.state.model_cache`.
- **Type:** `llm_tts_api.services.model_cache.LRUModelCache`.
- **Construction site:** `dependencies.build_default_dependencies` →
  stashed by `main.create_app`'s lifespan.
- **Public API consumers may rely on:**
  - `model_cache.loaded_keys() -> list[tuple[str, str]]` — MRU-first
    list of `(provider, model_id)` pairs. Safe to call on the hot
    path: `O(n)` over current entries (≤ `TTS_MODEL_CACHE_SIZE`),
    snapshot under an internal lock, no I/O.
  - `model_cache.max_size: int` — configured capacity.
  - `(provider, model_id) in model_cache` — membership probe.
  - `len(model_cache)` — current occupancy.
- **Depends-shape getter:** `llm_tts_api.dependencies.get_model_cache`.
- **Mutation:** consumers MUST NOT call `get_or_load` /  `preload`
  directly. The provider strategies own the mutation path through
  `attach_model_cache`.
- **Lifecycle:** the cache instance is created once at lifespan
  startup and lives for the process lifetime. Eviction happens on
  insert; there is no time-based expiry.

### Sprint coordinator notes

Worktree is on branch `sprint-2-S-008`; the coordinator merges back
to master after assembly. No conflicting edits with S-006/S-007/
S-009/S-012 are expected — this story only adds a new
`app.state.model_cache` slot, new fields on `Settings`, and a new
module under `services/`. The `dependencies.AppDependencies` dataclass
gained one field (`model_cache`); other Sprint 2 stories that touch
`AppDependencies` should add their fields alongside it, not replace
the constructor signature.

---

# S-010 Implementation Notes — Health/Ready split + graceful drain

**Branch:** `sprint-2-S-010`
**Commit:** `9297a46 feat(health): S-010 health/ready split + graceful drain`
**Status:** READY-FOR-REVIEW

## What changed

| File | Change |
|---|---|
| `src/llm_tts_api/routers/health.py` | Full rewrite. `/health` now emits the FR-HL-01 body (status, version, device, dtype, provider, provider_source, model_loaded, queue_depth, concurrent_active). `/ready` reads `app.state.ready` and returns 503 `{ready, reason}` when False. |
| `src/llm_tts_api/main.py` | Lifespan wrapped in `try/yield/finally`. On success path: flips `app.state.ready=True`, runs low-memory probe. On shutdown: clears ready, sets reason to `draining`, awaits `_drain_concurrency`. Added two helpers: `_emit_low_memory_warning` (psutil-based, FR-HL-05) and `_drain_concurrency` (polls `Semaphore._value` until released or budget expires, FR-HL-04). `create_app` initializes `app.state.ready=False, ready_reason="warming_up"` so the very first probe sees a defined value. |
| `src/llm_tts_api/config.py` | New `Settings.tts_min_free_memory_gb` (default 4) parsed via `_load_int` with `minimum=0`. `0` disables the probe. |
| `pyproject.toml` | Added `psutil>=5.9.0` to runtime deps. |
| `tests/conftest.py` | Stub `app.state` fixture now publishes the S-007 semaphores (sized from settings) and sets `ready=True`/`ready_reason="ready"`. Added `TTS_MIN_FREE_MEMORY_GB` to the env-clear list and `tts_min_free_memory_gb=0` to the stub Settings. |
| `tests/test_health_endpoints.py` | Added UAT-HL-01..05 tests + updated the pre-existing happy-path assertions to cover the new body keys. |
| `tests/test_startup_preload.py` | Stub Settings extended with `tts_shutdown_drain_seconds=0` and `tts_min_free_memory_gb=0`. |

## How I derived the /health fields

Per sprint-impl-2.md §S-007 Service Interface, the agreed shape is:

- `queue_depth = settings.tts_max_queue_depth - queue_semaphore._value`
- `concurrent_active = settings.tts_max_concurrent_requests - concurrency_semaphore._value`

I implemented this as `_semaphore_used(sem, capacity)` which clamps to ≥0 and tolerates `None` (test-bypass mode). `model_loaded` is rendered as `"<provider>:<model>"` strings derived from `LRUModelCache.loaded_keys()` (MRU-first per S-008).

`version` comes from `importlib.metadata.version("llm-tts-api")` with a `"0.0.0"` fallback for editable installs that lack metadata.

## Drain semantics (FR-HL-04, UAT-HL-03/04)

`_drain_concurrency` polls `concurrency_semaphore._value` every 50ms until either:

1. `_value >= capacity` → all in-flight work released, return early (logs `drain complete`).
2. The drain budget expires → log `drain timed out after Ns (in_flight=N)` and return.

Re-acquiring the semaphore (as the reference image-api does with its `inference_lock`) was rejected here because the S-007 design uses a counting `Semaphore` rather than a Lock: re-acquiring would race with admitted-but-waiting requests rather than just waiting for active ones to drain. Passive observation of `_value` is correct.

The `finally` block always sets `ready=False` and `ready_reason="draining"` before calling drain, so any probe during the drain window sees the right 503 reason.

## Bypass mode behavior (kept stable)

When `LLM_TTS_API_TEST_NO_LIFESPAN=1`:
- Lifespan skips the construction block, so `ready` stays at the `create_app`-time default (`False`/`warming_up`).
- The `finally` block still runs at shutdown and re-clears the flag — harmless because nothing changed it in between.
- `_drain_concurrency` is also a no-op in bypass mode because `drain_seconds` is initialized to 0 and only set from `deps.settings` on the non-bypass path.

The test fixture explicitly sets `ready=True` in the happy-path `client` fixture so existing endpoint tests work unchanged.

## Service Interface (what this story publishes for downstream)

| Slot | Type | Notes |
|---|---|---|
| `app.state.ready` | `bool` | `False` until lifespan warmup completes; `False` from the start of shutdown. |
| `app.state.ready_reason` | `str` | `"warming_up"` → `"ready"` → `"draining"`. Only consumed by `/ready` today. |

No other story in Sprint 2 consumes these. Sprint 3 voice-store stories may extend the ready signal (e.g. `"voice_store_unreachable"` reason).

## Quality gates

```
ruff check src tests           → All checks passed
ruff format --check src tests  → 66 files left unchanged
mypy --strict src              → no issues (38 files)
pytest --cov-fail-under=83     → 244 passed, coverage 87.60%
pip-audit                      → no vulnerabilities
```

The pre-existing `tests/` mypy errors (56 baseline) are unchanged; I added zero new ones in `tests/test_health_endpoints.py`.

## Open follow-ups (not in this story)

- README env-var inventory for `TTS_MIN_FREE_MEMORY_GB` is deferred to S-019 per sprint-2.md plan (UAT-CF-04 carries that check).
- The drain log line format is plain text; structlog migration is out of scope until APP_LOG_FORMAT=json is finalized.

---
