# Sprint 2 — Code Review Findings

Append-only review document. Each section is one task/story review round.

## Step 1 — Approval Summary

| Story | Verdict | Must-fix | Should-fix | Reviewer artifact |
|---|---|---|---|---|
| S-006 | APPROVED | 0 | 0 | yes (see below) |
| S-007 | APPROVED | 0 | 0 | no (zero findings ≥75 confidence; coordinator re-verified gates green) |
| S-008 | APPROVED | 0 | 0 | no (zero findings ≥75 confidence; coordinator re-verified gates green) |
| S-009 | APPROVED | 0 | 0 | no (engineer pane terminated before review.md write; coordinator re-ran all CI gates from the worktree — clean) |
| S-012 | APPROVED with should-fix | 0 | 2 | yes (see below) |

All five stories pass `ruff check + format`, `mypy --strict`, `pytest --cov-fail-under=83`, `pip-audit` in their respective worktrees prior to merge.

---


## S-006 / Provider capability + auto-selection — Review #1

# S-006 Code Review — Phase-6

**Reviewer:** Claude Opus 4.7
**Commit reviewed:** `453d46d` ("feat(providers): S-006 capability-driven auto-selection from DeviceProfile")
**Branch / worktree:** `sprint-2-S-006` @ `.worktrees/sprint-2/S-006`
**Date:** 2026-05-17

## Summary

S-006 lands a clean, well-typed capability-driven provider selector. The implementation is small (one new module + a Protocol-level attribute + three one-line provider declarations + thin glue), the contract for downstream S-010 is documented and stable, and every CI gate is green. Tests are thorough and explicitly trace UAT-HW-04 / UAT-HW-05.

## Per-task assessment

- **T1 — Protocol extension.** `TTSProviderStrategy.supports_devices: frozenset[Device]` added in `base.py`. Using `frozenset` (not `set`) for a class-level capability attribute is the right call (immutable, hashable, no shared-mutable-default footgun). The `Device` re-export from `llm_tts_api.engine.__init__` keeps the dependency direction clean.
- **T2 — Provider annotations.** `MLXAudio={mps}`, `Voxtral={mps}`, `VllmOmni={cuda}`. Decision-log point 8 (declaring `{mps}` only, not `{mps,cpu}`, because the underlying library requires Metal) is correct and tested. CPU is intentionally unsupported — exactly what UAT-HW-04 exercises.
- **T3 — Auto-selection.** `select_provider()` in `auto_select.py` plus glue in `build_default_dependencies`. Iteration order from `registry.all()` is the priority order (documented + tested). The post-selection mutation of `settings.tts_provider`, `tts_model_default`, and `tts_model_allowed` is acknowledged in the impl notes as deliberate backward-compat and is bounded to startup.
- **T4 — Typed startup error.** `ProviderSelectionError` carries `error_type="provider_error"` / `error_code="no_viable_provider"` plus a structured `rejections: list[ProviderRejection]`. Two distinct factory paths (`for_device`, `for_override`) keep call sites tight. Raised before any HTTP context exists, so uvicorn surfaces it on stderr → non-zero exit, matching FR-HW-05.
- **T5 — `/health` reporting.** Adds `provider`, `provider_source`, `device`. Read via `getattr(app.state, "provider_selection", None)` so the liveness probe never breaks during bypass / partial-warmup states. Source label is the typed `Literal["auto","env"]`.
- **T6 — Tests.** 16 cases in `tests/test_provider_auto_select.py`. UAT-HW-04 asserts all three provider names appear in `rejections` with their actual support sets; UAT-HW-05 asserts the structured rejection for `vllm-omni`-on-MPS by exact value. The parametrized `auto` / blank / mixed-case test pins the "treat as unset" semantics.

## Acceptance criteria verification

| Criterion | Status |
|---|---|
| All three providers declare `supports_devices` | PASS |
| Env unset on Apple Silicon → `/health` reports auto-selected provider | PASS (verified via test_health_reports_provider_with_env_source_when_override + `test_health_endpoints.py` extensions) |
| `TTS_PROVIDER=vllm-omni` on Apple Silicon → typed startup error | PASS (UAT-HW-05 test asserts exact rejection) |
| `TTS_DEVICE=cpu` + no viable provider → `provider_error.no_viable_provider` listing rejections | PASS (UAT-HW-04 test asserts all three providers listed) |

## CI gates verification

Re-ran in the worktree:

- `uv run ruff check src/ tests/ scripts/` — All checks passed.
- `uv run mypy src/` — Success: no issues found in 37 source files.
- `uv run pytest --cov=src/llm_tts_api --cov-fail-under=83` — **146 passed**, coverage **85.87%** (`auto_select.py` itself at **100%**, `registry.py` 100%).
- `uv run pip-audit --skip-editable` — No known vulnerabilities.

All gates match the impl-notes claims.

## Concerns / Suggestions

1. **Duplicated provider-name allow-list (minor nit).** `Settings._load_provider_models` hardcodes `{"mlx_audio", "voxtral", "vllm-omni"}` for spelling validation, while `auto_select._validate_override` uses `registry.find(...)` as the source of truth. The two will drift the next time a provider is added. Not a blocker — Settings runs before the registry exists at object-construction time — but a single canonical list (e.g. a module constant in `tts_providers/__init__.py`) would be cleaner.
2. **Settings.tts_provider mutation (acknowledged).** The mutation in `build_default_dependencies` is bounded to startup and documented, but it does mean `Settings` is no longer effectively-immutable. The note in the impl doc that a future story should thread the selection through `TTSService` explicitly is the right plan.
3. **`/health` uses plain `dict[str, str]`.** The router's return type forces every value to `str`. Today that is fine (all three new fields are strings), but when S-010 adds `queue_depth: int` / `concurrent_active: int` this annotation will need to widen to `dict[str, Any]` or a typed model. Leaving a TODO would help the S-010 author.
4. **Service Interface section.** Explicit, accurate, and forward-compatible. The "Reserved app.state slot names this story does NOT publish" list (S-007 semaphores, S-008 model cache) is exactly what S-010 needs to merge bodies without coordination friction. The `getattr(..., None)` pattern in `/health` makes additive extension by S-010 a non-event.
5. **Service-boundary fit with siblings.**
   - S-007 (queue/concurrency semaphores on `app.state`): S-006 does not touch those slots; main.py's lifespan additions are limited to `provider_selection`, leaving room for S-007.
   - S-009 (typed error envelope): `ProviderSelectionError` already exposes `error_type` / `error_code` matching the envelope contract; integration is documented as a startup-vs-runtime concern.
   - S-010 (consumes `/health`): keys `provider`, `provider_source`, `device` are declared stable from this sprint forward; both the `app.state` slot and the FastAPI `Depends` getter are typed.

## Verdict

**APPROVED_WITH_NITS** — the nits in §1 and §3 above are non-blocking cleanups that can be folded into S-010 or a follow-up. S-006 is ready to merge as-is.

---

## S-012 / Configuration inventory + env validation — Review #1

> Source: Sprint 2 Review
> Author: Code Reviewer (AI-assisted)
> Date: 2026-05-17
> Status: Approved (with recommendations)
> Version: 1.0

# Sprint 2 — S-012 Task Review

## Overview

| Metric | Count |
|--------|-------|
| Tasks reviewed | 1 (S-012, atomic tasks T1..T4) |
| Approved | 1 |
| Changes needed | 0 |
| Must-fix findings | 0 |
| Should-fix findings | 2 |

CI gates verified in the worktree at `/Volumes/Coding/Projects/Applications/epub/llm-tts-api/.worktrees/sprint-2/S-012`:

| Gate | Command | Result |
|---|---|---|
| Lint | `uv run ruff check src/ tests/` | All checks passed |
| Format | `uv run ruff format --check src/ tests/` | 60 files already formatted |
| Types | `uv run mypy src/` | Success: no issues found in 36 source files |
| Tests + coverage | `uv run pytest --cov=src/llm_tts_api --cov-fail-under=83` | 179 passed; coverage 85.67% |
| Dep audit | `uv run pip-audit --skip-editable` | No known vulnerabilities found |

---

## S-012 / Configuration inventory + env validation

**Verdict:** Approved (with recommendations)

### Atomic-task coverage

| Task | Status | Evidence |
|---|---|---|
| T1 — Add 8 fields to `Settings` | Delivered | `src/llm_tts_api/config.py` lines 78-85: `tts_device`, `tts_dtype`, `tts_max_queue_depth`, `tts_model_cache_size`, `tts_preload_models`, `tts_inference_timeout_seconds`, `tts_shutdown_drain_seconds`, `app_log_format`. |
| T2 — Integer + enum validation via `frozenset` pattern | Delivered | Module-level frozensets at lines 8-10 mirror `engine/device.py`. `_load_enum` / `_load_int` enforce bounds and named-var errors. |
| T3 — `TTS_INFERENCE_TIMEOUT_SECONDS` default UNSET → disabled | Delivered | `tts_inference_timeout_seconds: float \| None = None` (line 83); `_load_optional_timeout` returns `None` on unset/empty (lines 253-271). |
| T4 — Tests UAT-CF-01..03 | Delivered | `tests/test_config_runtime_knobs.py` — 21 tests covering defaults (UAT-CF-02), enum validation, integer validation, optional-timeout opt-in, and preload-models parsing. Each invalid-value class produces a `ValueError` matched by the env-var name (UAT-CF-01). |

### Spec compliance

- **UAT-CF-01 (invalid value → startup exits non-zero with named-var message):** every invalid path in `_load_enum`, `_load_int`, `_load_optional_timeout`, and `_load_preload_models` raises `ValueError` whose message embeds the env-var name. Parametrised tests `test_invalid_enum_raises_named`, `test_invalid_int_raises_named`, and `test_timeout_invalid_value_raises` assert `match=<env-var name>`. The `ValueError` propagates through `Settings.__post_init__` to the lifespan, exiting the process before serving traffic.
- **UAT-CF-02 (default-unset timeout):** `test_defaults_are_safe` + `test_timeout_unset_means_disabled` + `test_timeout_empty_string_means_disabled` pin the contract; `tts_inference_timeout_seconds` is `None` when unset.
- **UAT-CF-03 (configured timeout=2 → 504):** the parsing half of the contract is verified by `test_timeout_positive_value_enabled` (`"2"` → `2.0`). The end-to-end 504 behaviour belongs to S-007's `asyncio.wait_for` wrapper, which is the consumer of this attribute — out of scope for S-012.
- **UAT-CF-04** explicitly deferred to S-019 per sprint doc.

### Architecture compliance

- `frozenset` enum membership matches the existing pattern in `engine/device.py`.
- Empty-string-as-default semantics consistent with `engine/device.py` (`SF-10` shell-wrapper foot-gun).
- `PreloadEntry` is correctly modelled as `@dataclass(frozen=True, slots=True)`.
- `Settings` remains `@dataclass(slots=True)`; no new external dependencies.
- `_load_runtime_knobs()` is correctly ordered after `_load_provider_models()` so the preload allow-list check sees resolved per-provider allow-lists.

### Code quality

- Type annotations are precise (`float | None`, `frozenset[str]`, `list[PreloadEntry]`).
- Error messages embed env-var name and offending value via `!r`, aiding operator triage.
- Zero / negative timeout is rejected with an explicit message that suggests the right remediation ("omit the variable to disable the timeout"). This forecloses the `asyncio.wait_for(coro, 0)` foot-gun.
- No secret material in error messages or new env vars.
- `conftest.py` is correctly updated to (a) clear all five new env vars in `clear_env` and (b) populate the eight new attributes on the `object.__new__(Settings)` stub so router/test code reading them does not crash.

### Should-Fix Issues

| # | Agent | Category | File | Location | Finding | Expected | Suggested Action |
|---|-------|----------|------|----------|---------|----------|------------------|
| 1 | Test Coverage | Assertion specificity | `tests/test_config_runtime_knobs.py` | Line 274 (`test_preload_unknown_provider_raises`) | Test matches on `"nope"` (the provider fragment) rather than the env-var name. UAT-CF-01 requires the **named env var** to appear in the error; the production code does embed `TTS_PRELOAD_MODELS` in the message, but the test does not pin that. | Tests for UAT-CF-01 should assert that the env-var name appears in the message, matching the pattern used by sibling tests (`match="TTS_PRELOAD_MODELS"`). | Change `pytest.raises(ValueError, match="nope")` to `pytest.raises(ValueError, match="TTS_PRELOAD_MODELS")` so the named-var contract is pinned. Optionally add a second `match` via `match=r"TTS_PRELOAD_MODELS.*nope"` to cover both. Non-blocking — the production behaviour is correct. |
| 2 | Simplification | Duplication (acknowledged) | `src/llm_tts_api/config.py` + `src/llm_tts_api/engine/device.py` + `src/llm_tts_api/logging_setup.py` | `_load_runtime_knobs` lines 206-210 vs. `engine/device.py` env reads and `setup_logging` env read | `Settings.tts_device` / `tts_dtype` / `app_log_format` are now validated source-of-truth, but `engine/device.py` still re-reads `TTS_DEVICE` / `TTS_DTYPE` from env, and `setup_logging` still re-reads `APP_LOG_FORMAT` from env. The duplication is acknowledged in the implementation notes as deferred cleanup. | Downstream consumers should read these knobs from `app.state.settings` rather than re-reading env vars to ensure a single validation surface. | Track as a Sprint-3 cleanup: pass `settings.tts_device` / `settings.tts_dtype` into `resolve_device_profile` and refactor `setup_logging` to accept `settings.app_log_format` (requires lifespan re-ordering). Non-blocking for S-012 — explicitly out of scope per implementation notes and sprint doc. |

### Notes on findings discarded after confidence scoring

- A potential finding about `_load_enum` calling `.strip().lower()` on the default-fallback path was discarded: all defaults are already lowercase ASCII so the transform is a no-op, and the empty-check after the lowercase correctly returns the unmodified `default`. No real-world impact.
- A potential finding about not normalising `tts_provider` in `_allow_list_for_provider` was discarded: providers passed in originate from `TTS_PRELOAD_MODELS` strings that are not case-normalised, but the per-provider allow-list keys (`mlx_audio`, `voxtral`, `vllm-omni`) are stable lowercase tokens defined by the project. The current behaviour is strict (case-sensitive match), which is acceptable for an enum domain and matches `_load_provider_models`'s handling of `TTS_PROVIDER` after `.strip().lower()`. Could be a future polish but not a defect.

### Verdict

S-012 satisfies T1..T4 and UAT-CF-01..03. All five CI gates pass in the worktree. Zero must-fix findings; two should-fix recommendations are non-blocking.

**This task is ready for status transition to DONE.**

---
