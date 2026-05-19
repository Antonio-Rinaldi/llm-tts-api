# Sprint 7: Presets foundation + resolution + hot-reload

> Source: docs/planning/journal.md (cycle 2 — Group F + Group G)
> SRS: docs/specs/software-spec.md §4.14 (cycle 2)
> FRS: docs/specs/analyst-frs.md §4.14 (FR-PR)
> NFR: docs/specs/writer-nfr.md §11b.1 + §11b.4 + §11b.5 + §11b.6
> UAT: docs/specs/analyst-UAT.md UAT-PR-01..17
> Author: Sprint Planner (AI-assisted)
> Date: 2026-05-19
> Status: READY-FOR-REVIEW
> Version: 1.0

## 1. Sprint Objective

Land the **preset registry foundation** and the two primitives that everything else in cycle 2 depends on: **request-time resolution** (producing the frozen `EffectiveSynthesisConfig` consumed by all downstream synthesis code) and the **hot-reload + in-flight snapshot** semantic (matching cycle-1 voice-map reload UX, NFR-SE-10 attack-tolerant). This sprint proves the cycle-2 spine end-to-end — config to wire, resolution to wire, reload to wire — without yet adding post-processing, format extension, or quality-stream downgrade.

## 2. Value Statement

Every cycle-2 story (S-030..S-036) reads `app.state.preset_registry` (S-027), `EffectiveSynthesisConfig` (S-028), or relies on the snapshot semantics (S-029). Sprint 7 unblocks all four parallel-Group-H stories at once. It also locks the **load-bearing S-018 byte-identity invariant** (NFR-PT-05): S-028 must resolve rich-with-balanced ↔ OpenAI-default to the same `EffectiveSynthesisConfig`, otherwise the paired UAT breaks and the rest of the cycle is built on sand. Sprint 7 is where the cycle either confirms its core abstraction or finds out it's broken — earliest possible.

## 3. Sprint Summary

| Metric | Value |
|--------|-------|
| Stories | 3 (S-027, S-028, S-029) |
| User stories | 0 |
| Technical stories | 3 |
| Total tasks | 14 |
| Parallel tracks | 1 (Step 1: S-027 alone) → 2 (Step 2: S-028 ∥ S-029) |

## 4. Execution Order

Service-boundary rule applies. S-028 and S-029 both consume `app.state.preset_registry` from S-027 — they MUST run in a later step than S-027. They do NOT communicate with each other (S-028 owns request-time resolution; S-029 owns watcher + swap + snapshot), so they parallelize cleanly in Step 2.

| Step | Stories | Can start after |
|------|---------|----------------|
| 1 | S-027 | Immediately (cycle-1 S-003 lifespan + S-012 Settings are DONE) |
| 2 | S-028, S-029 | Step 1 complete — both consume `app.state.preset_registry` initialized by S-027's lifespan wiring |

## 5. Stories

### S-027: Presets configuration foundation
- **Status:** READY-FOR-REVIEW
- **Type:** Technical
- **Parallel with:** None within this sprint
- **Depends on (intra-sprint):** None (cycle-1 deps all DONE)
- **Refs:** FR-PR-01, FR-PR-02, FR-PR-05, FR-PR-13, NFR-SE-09, NFR-PR-02
- **Architecture:** SRS §4.14 (presets), §12 (traceability), BR-10 (resolution precedence), BR-16 (startup-fail tier)

#### Tasks

| # | Task | Purpose | Parallel | Status | Refs |
|---|------|---------|----------|--------|------|
| 1 | Define `PresetConfig` Pydantic model + the inner `PresetEntry` + `PresetDefaults` + `PresetPostprocess` shapes | The schema is the boundary between operator-edited JSON and Python. `extra="forbid"` at every level. Field paths in validation errors (e.g. `presets.quality.defaults.temperature`) — Pydantic v2 native. | No (foundation) | READY-FOR-REVIEW | FR-PR-02, FR-PR-03 |
| 2 | Ship `config/presets.json` with three built-in presets | `fast`, `balanced`, `quality` with the defaults derived from cycle-2 SRS §4.14 + cycle-1 baseline (so `balanced` matches cycle-1 default behavior for client compat per A-PR-1). `quality` defaults: `response_format=flac`, `postprocess.rms_normalize=true`, `postprocess.silence_trim=true`. `fast`: `temperature` lowered, `max_sentences_per_chunk` lowered, smaller model if a distilled checkpoint is available else same model. | Yes (with T1 after the model exists) | READY-FOR-REVIEW | FR-PR-01, FR-PR-03, FR-FMT-05 |
| 3 | Add new `Settings` env vars + validators | New env vars from cycle 2: `TTS_DEFAULT_PRESET` (str, default `"balanced"`, validated against loaded registry), `TTS_PRESETS_FILE` (path, default `config/presets.json`), `TTS_SILENCE_TRIM_THRESHOLD_DB` (float, default `-50.0`, for S-031 later). Each validated in `Settings.__post_init__`. | Yes (independent surface) | READY-FOR-REVIEW | FR-PR-05 |
| 4 | Wire startup validation into the lifespan | In `main.py::lifespan`: load `presets.json`, parse via `PresetConfig`, validate file-permission posture (owner uid match + not world-writable), validate `TTS_DEFAULT_PRESET` resolves to a defined preset name, validate every preset's pinned `(provider, model)` is in the corresponding provider's allow-list. Each failure exits non-zero with the corresponding `config_error.*` code from the cycle-2 taxonomy. The loaded registry hangs off `app.state.preset_registry` as a frozen dataclass (immutable for the lifetime of one config; replaced atomically on reload in S-029). | No (consumes T1+T2+T3) | READY-FOR-REVIEW | FR-PR-02, FR-PR-05, FR-PR-13, NFR-SE-09 |
| 5 | Add cycle-2 error codes to `errors.py` | Three new codes: `config_error.presets_invalid`, `config_error.preset_provider_invalid`, `config_error.presets_unsafe_permissions`. (Two more — `validation_error.preset_unknown`, `validation_error.format_unsupported` — added in S-028 and S-033 respectively.) | Yes (independent file) | READY-FOR-REVIEW | FR-PR-02, FR-PR-13, NFR-SE-09 |
| 6 | Tests: UAT-PR-11/12/13/14 + Pydantic schema unit tests + permission-check unit tests | Each of the four startup-fail UATs is a separate test that builds a tampered `presets.json` / env var / file mode and asserts the lifespan refuses to start with the correct error code. Pydantic schema tests pin field-path messages. Permission test uses `tempfile` + `chmod 0o666` to simulate world-writable. | No (verifies T1..T5) | READY-FOR-REVIEW | UAT-PR-11, UAT-PR-12, UAT-PR-13, UAT-PR-14, NFR-PR-02 |

#### Acceptance Criteria
- `PresetConfig` model accepts the three built-ins and rejects unknown fields with clear field-path messages.
- `config/presets.json` loaded by lifespan; `app.state.preset_registry` is set after startup.
- Permission check refuses startup on world-writable or owner-mismatched files (UAT-PR-14).
- Unknown `TTS_DEFAULT_PRESET` refuses startup (UAT-PR-13).
- Preset pinning unknown `(provider, model)` refuses startup with `config_error.preset_provider_invalid` (UAT-PR-12).
- Pydantic violation refuses startup with `config_error.presets_invalid` + path (UAT-PR-11).
- Resolution overhead from the loaded registry is ≤1 ms p95 — measured by a micro-bench in tests (NFR-PR-02; documented, not test-gated).
- Existing 380 cycle-1 tests still pass; no regression in master.

#### Testing & Verification
Pytest extensions: `tests/test_presets_config.py` (schema unit tests + permission tests + the 4 startup-fail UATs via spawning the lifespan with `TestClient` + a configured-but-broken `presets.json`). The 4 startup-fail UATs need a way to invoke the lifespan with a non-default `TTS_PRESETS_FILE` pointing at a tampered fixture — fixtures live under `tests/fixtures/presets/`. Standard gates (ruff, ruff format, mypy --strict, pytest, pip-audit) stay green.

---

### S-028: Preset resolution + EffectiveSynthesisConfig
- **Status:** READY-FOR-REVIEW
- **Type:** Technical
- **Parallel with:** S-029 (Step 2 — no cross-deps)
- **Depends on (intra-sprint):** S-027 (reads `app.state.preset_registry`)
- **Refs:** FR-PR-04, FR-PR-06, FR-PR-07, FR-PR-08, FR-PR-09, FR-PR-10, BR-10, BR-12, BR-17, NFR-PT-05, NFR-PR-02
- **Architecture:** SRS §4.14, BR-10 (resolution precedence), BR-12 (OpenAI-path lock), BR-17 (soft-ignore via `X-Preset-Ignored-Knobs`), NFR-PT-05 (S-018 byte-identity invariant)

#### Tasks

| # | Task | Purpose | Parallel | Status | Refs |
|---|------|---------|----------|--------|------|
| 1 | Define `EffectiveSynthesisConfig` frozen dataclass | The single shape consumed downstream by all synthesis code. Fields: `preset_name`, `provider`, `model`, `temperature`, `top_p`, `max_sentences_per_chunk`, `normalize_db`, `response_format`, `postprocess` (placeholder shape — full wiring is S-031), `ignored_knobs: tuple[str, ...]`, `effective_overrides: dict[str, str]` (for the `X-Preset-Effective` header). | No (foundation) | READY-FOR-REVIEW | FR-PR-06, BR-10 |
| 2 | Add `preset: str \| None` to `SynthesizeRequest` | Open-string Pydantic field (NOT `Literal[...]`) so operator-defined presets work without OpenAPI regeneration. Description + example pin the three built-ins. | Yes (independent surface) | READY-FOR-REVIEW | FR-PR-04 |
| 3 | Implement `resolve_preset(request, settings, app_state) -> EffectiveSynthesisConfig` in `services/synthesize_service.py` | Single resolution site. Precedence per BR-10: explicit request field > preset defaults > Settings/VoiceRecord defaults. Unknown preset name ⇒ raise `validation_error.preset_unknown` (HTTPException) listing available preset names. Conflict between explicit field and preset pin ⇒ explicit wins + WARN log + record in `effective_overrides`. Provider-incompatible knobs ⇒ recorded in `ignored_knobs`. The resolver is pure (no I/O); reads `app_state.preset_registry`. | No (depends on T1+T2) | READY-FOR-REVIEW | FR-PR-06, FR-PR-07, FR-PR-08, FR-PR-09, BR-10, BR-17 |
| 4 | Wire `resolve_preset` into `synthesize_core` + add response header emission | `synthesize_core` calls `resolve_preset` once at the top, hands the resulting `EffectiveSynthesisConfig` to all downstream code. Emits `X-Preset-Effective` (always) and `X-Preset-Ignored-Knobs` (only when non-empty) response headers on the rich path. **OpenAI path strips them** per cycle-1 S-017's `_RICH_ONLY_HEADERS` — extend the set. | No (depends on T3) | READY-FOR-REVIEW | FR-PR-06, FR-PR-10, BR-12, NFR-PT-05 |
| 5 | Add `validation_error.preset_unknown` to `errors.py` | New error code for unknown preset name. Returned as 400 with the available preset names in the message. | Yes (independent file) | READY-FOR-REVIEW | FR-PR-07 |
| 6 | Tests: UAT-PR-01..07 + S-018 byte-identity regression check | Each UAT becomes a focused pytest. UAT-PR-06 is the load-bearing test: rich(`preset=balanced`, no overrides) sha256-equals OpenAI-path body for the same effective request — extends `test_openai_adapter_parity.py` parametrization (NOT modifies the existing tests — adds a parametrized case where rich explicitly sets `preset="balanced"`). Run the full `test_openai_adapter_parity.py` suite at the end and assert no regression. | No (verifies T1..T5) | READY-FOR-REVIEW | UAT-PR-01..07, NFR-PT-05 |

#### Acceptance Criteria
- `EffectiveSynthesisConfig` frozen; consumed by `synthesize_core` and (after S-031/S-033) by downstream postproc/format code.
- `SynthesizeRequest.preset: str | None` — open string at the Pydantic level.
- Unknown preset returns 400 `validation_error.preset_unknown` with available names listed (UAT-PR-03).
- Explicit field overrides preset pin; `X-Preset-Effective` shows resolved values; WARN log carries request_id (UAT-PR-04).
- Provider-incompatible knobs ⇒ soft-ignore + `X-Preset-Ignored-Knobs` header (UAT-PR-05).
- OpenAI path ignores body/query preset; always resolves to `TTS_DEFAULT_PRESET` (UAT-PR-06, UAT-PR-07).
- **S-018 paired UAT passes byte-identically** post-S-028; `tests/test_openai_adapter_parity.py` source file is byte-identical to its cycle-1 form (NFR-PT-05).
- `test_openai_adapter_parity.py` parametrized to also cover `rich(preset=balanced) ↔ OpenAI-default` — new test case, no modification to existing cases.

#### Testing & Verification
The S-018 byte-identity invariant is the gate. Run `uv run pytest tests/test_openai_adapter_parity.py -v` end-of-story and assert all cases pass. The 7 new UAT-PR tests (01..07) cover the resolution and header behaviors. The resolver's micro-bench (NFR-PR-02 ≤1ms p95) is captured as a benchmarks test, not asserted in CI.

---

### S-029: Preset hot-reload + in-flight snapshot
- **Status:** READY-FOR-REVIEW
- **Type:** Technical
- **Parallel with:** S-028 (Step 2 — no cross-deps)
- **Depends on (intra-sprint):** S-027 (consumes `app.state.preset_registry`)
- **Refs:** FR-PR-11, NFR-SE-10, NFR-PR-03, NFR-PR-04, BR-11, RISK-3, RISK-PR-3
- **Architecture:** SRS §4.14 (hot-reload semantics), NFR-SE-10 (validating before swap), NFR-PR-04 (in-flight snapshot), RISK-3 (watchfiles in Docker) — reuses cycle-1 S-011 watcher primitive.

#### Tasks

| # | Task | Purpose | Parallel | Status | Refs |
|---|------|---------|----------|--------|------|
| 1 | Extract / generalize the cycle-1 S-011 watcher primitive | Cycle-1's `services/voice_store/seed_ingestion.py` already has a `watchfiles` + polling-fallback loop for `voice_map.json`. Refactor (do NOT rewrite) the inner watcher mechanic into a small reusable helper (e.g. `services/config_watcher.py::ConfigWatcher`) parameterized by file path + reload callback + validation callback. Cycle-1 seed-ingestion code keeps its existing behavior; the new presets-watcher uses the same primitive. | No (foundation) | READY-FOR-REVIEW | FR-PR-11, RISK-3 |
| 2 | Implement the `PresetRegistryReloader` | The validation-before-swap reloader. Watches `TTS_PRESETS_FILE`; on change: read file → parse via `PresetConfig` → run full startup validation (incl. provider allow-list + `TTS_DEFAULT_PRESET` resolution); only if all pass, atomically swap `app.state.preset_registry`; on any failure, log WARN with field-path + keep prior registry live (NFR-SE-10 attack-tolerant). Reload latency target ≤2s on Linux/macOS native; polling fallback on Docker bind-mounts. **Permission check is startup-only**, NOT re-run on reload (documented in NFR-OP-PR-3 risk — RISK-PR-3 trade-off). | No (depends on T1) | READY-FOR-REVIEW | FR-PR-11, NFR-SE-10, NFR-PR-03 |
| 3 | Wire in-flight request snapshot via a request-scoped `presets` reference | In the rich endpoint's request entry (FastAPI dependency or `synthesize_core`'s preamble): take a reference to `app.state.preset_registry` at request-start and pass it explicitly into `resolve_preset` (S-028 T3 reads via `app_state.preset_registry` — we change the contract so that the *snapshot* is the input, not `app_state`). This makes the snapshot semantic explicit and testable; resolution code never sees a torn registry mid-request. **Coordinator note:** this is the one point of contact with S-028 — both stories must agree on the function signature. The signature is locked in this task and recorded in `sprint-impl-7.md`. | Yes (with T2 — independent code) | READY-FOR-REVIEW | FR-PR-11, NFR-PR-04, BR-11 |
| 4 | Hook the reloader into the lifespan | In `main.py::lifespan`: after S-027's startup validation initializes `app.state.preset_registry`, start the reloader as a background task using the same pattern S-011 already uses for voice-map watchfiles. On shutdown, cancel the task (clean drain — uses the cycle-1 S-010 graceful drain machinery). | No (depends on T2) | READY-FOR-REVIEW | FR-PR-11, NFR-PR-03 |
| 5 | Tests: UAT-PR-08, UAT-PR-09, UAT-PR-15 + watcher unit tests | Three pytest tests: (a) happy-path reload (write valid new presets.json, wait ≤2s, request uses new preset); (b) in-flight snapshot (start long-running quality-preset synthesis, modify presets.json mid-flight to remove `quality`, assert in-flight finishes successfully; subsequent requests with `preset=quality` return 400 `preset_unknown`); (c) invalid reload skipped (write invalid presets.json, wait, assert WARN log + service still serves old config). Watcher unit tests exercise the cycle-1 primitive via direct invocation (no actual file watching) for speed. | No (verifies T1..T4) | READY-FOR-REVIEW | UAT-PR-08, UAT-PR-09, UAT-PR-15, NFR-SE-10 |

#### Acceptance Criteria
- `ConfigWatcher` primitive exists; cycle-1 seed-ingestion still works using it (regression-free).
- Valid new `presets.json` swap atomically within ≤2s on Linux/macOS native; polling fallback used in Docker (UAT-PR-08).
- In-flight requests use the registry snapshot taken at request-start, even if reload happens mid-flight (UAT-PR-09).
- Invalid new `presets.json` ⇒ reload skipped + WARN log + prior registry stays live; no service downtime (UAT-PR-15).
- Permission check is startup-only (documented limitation per RISK-PR-3).
- Reloader cancelled cleanly on shutdown via cycle-1 S-010 drain.

#### Testing & Verification
3 new pytest cases (UAT-PR-08, UAT-PR-09, UAT-PR-15). The reload latency SLO (≤2s) is verified by polling `app.state.preset_registry` for the new preset name after writing the file. In-flight snapshot is verified by spawning a synthesis task with `asyncio.create_task`, modifying the file mid-flight, and observing the task succeeds. Invalid-reload skipped is verified via caplog WARN inspection.

---

## 6. References

- [SRS](../../specs/software-spec.md) — §4.14 presets, §5 cycle-2 conflict resolutions, §6 BR-10..BR-17, §8 RISK-PR-1..5, §13 cycle-2 success criteria
- [FRS](../../specs/analyst-frs.md) — FR-PR-01..13 (cycle 2)
- [NFR](../../specs/writer-nfr.md) — §11b.1 NFR-PR-01..04, §11b.4 NFR-SE-09/10, §11b.5 NFR-OP-06/07, §11b.6 NFR-PT-05/06
- [UAT](../../specs/analyst-UAT.md) — UAT-PR-01..17
- [Journal](../journal.md) — Cycle 2 stories S-027, S-028, S-029
- [Cycle-2 request](../../specs/requests/dual-mode-presets-request.md) — PO-scoped decisions D1-D10
- [Sibling project — quality bar](../../../../llm-image-api/config/presets.json)

## 7. Risks & Dependencies

| Risk/Dependency | Affected Stories | Mitigation |
|----------------|-----------------|------------|
| **NFR-PT-05 — S-018 byte-identity breaks** under preset resolution drift (RISK-PR-5) | S-028 | S-028 T6 runs the full `test_openai_adapter_parity.py` suite at end-of-story. Resolution centralized in one function (`resolve_preset`) so divergence cannot accidentally appear in two places. |
| **S-027 ↔ S-029 lifespan coupling**: reloader hooked into lifespan must not race with S-027's initial validation | S-027, S-029 | S-029 T4 runs AFTER the initial registry is set; uses cycle-1 S-010 drain pattern. Coordinator merges S-027 before S-029's reloader hooks. |
| **S-028 T3 ↔ S-029 T3 function signature alignment**: both stories touch the resolver's input contract | S-028, S-029 | Lock the `resolve_preset(request, snapshot, settings) -> EffectiveSynthesisConfig` signature in S-027's impl notes; both consumer stories build to that contract. If a divergence appears at merge time, the coordinator runs the story-review phase per cycle-1 protocol. |
| **`watchfiles` in Docker still flaky (RISK-3)** | S-029 | Polling fallback inherited from cycle-1 S-011 mechanism — verified in cycle-1 UAT-VM-03 in container; same primitive used here. |
| **Per-preset `(provider, model)` allow-list validation depends on provider registry being fully initialized** | S-027 | S-027 T4 runs AFTER provider registry initialization (cycle-1 S-006 already in `app.state.provider_registry` by the time preset validation runs). Documented in S-027 impl notes. |
| **`PresetConfig` schema drift between S-027 and downstream cycle-2 stories**: future cycle-2 stories may want to add fields | S-027 | `extra="forbid"` at every level; future additions are explicit + must update all 3 built-ins; surfaced in story reviews. |
