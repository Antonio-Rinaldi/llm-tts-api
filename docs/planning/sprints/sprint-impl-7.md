# Sprint 7 — Implementation Notes

Per-story implementation notes assembled by the sprint-coordinator after each story
completes in its isolated worktree. Companion to `sprint-7.md`.

## Summary

| Story | Type | Status | Worktree branch |
|---|---|---|---|
| S-027 | Technical | READY-FOR-REVIEW | sprint-7-S-027 (merged) |
| S-028 | Technical | READY-FOR-REVIEW | sprint-7-S-028 (merged) |
| S-029 | Technical | READY-FOR-REVIEW | sprint-7-S-029 (merged) |

Sprint 7 status: Complete — reviewed.

---

# S-027 — Presets configuration foundation

**Branch:** `sprint-7-S-027` (merged into master)
**Worktree:** `.worktrees/sprint-7/S-027`

# S-027 — Presets configuration foundation (impl notes)

> Sprint: 7
> Status: READY-FOR-REVIEW
> Branch: `sprint-7-S-027`
> Refs: FR-PR-01/02/03/05/13, NFR-SE-09, NFR-PR-02, UAT-PR-11..14

## What landed

| Task | Surface | File(s) |
|------|---------|---------|
| T1 | `PresetConfig` + inner Pydantic models (`extra="forbid"`, field-path errors prefixed with `presets.`) | `src/llm_tts_api/services/presets/config.py` |
| T2 | Three shipped presets (`fast` / `balanced` / `quality`) — `balanced` mirrors cycle-1 `VoiceConfig` defaults for A-PR-1; `quality` defaults to `flac` + `rms_normalize` + `silence_trim` | `config/presets.json` |
| T3 | Three new `Settings` env vars: `TTS_DEFAULT_PRESET`, `TTS_PRESETS_FILE`, `TTS_SILENCE_TRIM_THRESHOLD_DB` | `src/llm_tts_api/config.py` (`_load_presets_settings`) |
| T4 | Lifespan startup validation hooked after `provider_registry` init; `app.state.preset_registry` set to a frozen `PresetRegistry`; typed errors translated to `SystemExit("config_error.*: …")` | `src/llm_tts_api/main.py` (`_load_presets_or_exit`), `src/llm_tts_api/services/presets/startup.py` |
| T5 | New `config_error` taxonomy category + three codes (`presets_invalid`, `preset_provider_invalid`, `presets_unsafe_permissions`); README documents them | `src/llm_tts_api/errors.py`, `README.md` |
| T6 | 28 unit tests in `tests/test_presets_config.py` covering Pydantic invariants, permission posture, UAT-PR-11..14, and the new Settings env vars; existing 380 cycle-1 tests untouched | `tests/test_presets_config.py` |

## Sequencing inside the lifespan

```
build_default_dependencies()            # cycle-1, unchanged
  → app.state.provider_registry         # cycle-1
  → _load_presets_or_exit(settings, provider_registry)
        ↳ check_presets_file_permissions(path)        # NFR-SE-09
        ↳ load_preset_registry(path)                  # FR-PR-02 (Pydantic + field paths)
        ↳ default_preset ∈ registry.names()           # FR-PR-05
        ↳ validate_preset_providers(registry, allow_lists)  # FR-PR-13
  → app.state.preset_registry           # frozen PresetRegistry snapshot
  → … model_cache / tts_service / voice store …
```

The permission check runs **before** the JSON parse (defense-in-depth: a
tampered-permissions file never reaches the parser). Provider allow-list
cross-check is restricted to providers actually present in
`app.state.provider_registry` — a preset pinning a provider that was
filtered out at auto-select is treated as misconfiguration.

## Locked Service Interface (S-028 / S-029 contract)

These shapes are **frozen** in this story; S-028 (resolver) and S-029
(hot-reload + swap) MUST build to them. Diverging is a story-review
failure per cycle-1 protocol.

### 1. `PresetRegistry` (snapshot held on `app.state.preset_registry`)

```python
@dataclass(frozen=True, slots=True)
class PresetRegistry:
    _presets: Mapping[str, PresetEntry]

    def get(self, name: str) -> PresetEntry | None: ...
    def names(self) -> frozenset[str]: ...
    def __contains__(self, name: object) -> bool: ...
    def __len__(self) -> int: ...
```

* Immutable. S-029 replaces the slot atomically on hot-reload; never
  mutates in place.
* `.get()` returns `None` for unknown names. S-028's resolver MUST
  translate `None` into a `400 validation_error.preset_unknown` per
  FR-PR-07 (resolver — not registry — owns the HTTP error.)
* `.names()` returns a `frozenset[str]` — safe to surface in error
  messages without leaking internal mutability.

### 2. `PresetEntry` / `PresetDefaults` / `PresetPostprocess` schema

```python
class PresetPostprocess(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rms_normalize: bool = False
    silence_trim: bool = False
    denoise: bool = False

class PresetDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str | None = None
    model: str | None = None
    temperature: float | None  # bounded [0.0, 2.0]
    top_p: float | None        # bounded (0.0, 1.0]
    max_sentences_per_chunk: int | None  # >= 1
    normalize_db: float | None = None
    response_format: Literal["wav", "wav24", "flac"] | None = None
    postprocess: PresetPostprocess | None = None

class PresetEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str           # min_length=1
    description: str     # min_length=1
    defaults: PresetDefaults
```

Top-level file is a flat `{"<name>": PresetEntry, ...}` dict — the
llm-image-api reference shape per cycle-2 D10. `PresetConfig` is a
`RootModel[dict[str, PresetEntry]]`; field-path errors are surfaced
with a `presets.` prefix (e.g. `presets.quality.defaults.temperature`).

### 3. `resolve_preset` signature for S-028

S-028 MUST implement the resolver with the following signature so the
in-flight snapshot from S-029 is an **explicit** parameter (testable;
never read from `app_state` inside the resolver):

```python
def resolve_preset(
    request: SynthesizeRequest,
    snapshot: PresetRegistry,
    settings: Settings,
) -> EffectiveSynthesisConfig: ...
```

* `snapshot` is the request-scoped `PresetRegistry` reference that
  S-029 T3 captures at request-start. The resolver MUST use this
  snapshot for the full duration of the request, even if
  `app.state.preset_registry` is replaced mid-flight (NFR-PR-04).
* `request: SynthesizeRequest` includes the new `preset: str | None`
  field S-028 T2 adds (open string per FR-PR-04).
* `settings: Settings` supplies `tts_default_preset` (used when
  `request.preset is None`) and the per-provider model allow-lists
  for the resolver's compatibility checks.
* Resolution precedence per BR-10: explicit request field > preset
  defaults > Settings/VoiceRecord defaults.
* Unknown preset name → raise `OpenAIHTTPException` with
  `validation_error.preset_unknown` (S-028 T5 adds the code; this
  story does NOT add it).
* The resolver is pure (no I/O, no global state reads); the only
  external input is the three explicit arguments above.

### 4. `app.state.preset_registry` slot

* Type: `PresetRegistry`.
* Lifetime: replaced atomically by S-029 on successful hot-reload.
  S-027's lifespan sets the initial value; S-029's reloader does the
  swap. Both writers agree on the type — no `Any` upgrades, no
  per-mutable-dict surgery.

### 5. New error codes registered in this story

* `config_error.presets_invalid`
* `config_error.preset_provider_invalid`
* `config_error.presets_unsafe_permissions`

`validation_error.preset_unknown` (S-028) and
`validation_error.format_unsupported` (S-033) are explicitly **out of
scope** for S-027 — they belong to the consuming stories.

## Permission posture (NFR-SE-09)

* Owner-uid match (`os.geteuid() == st.st_uid`).
* Mode bits exclude `S_IWOTH` (`0o002`).
* Startup-only per RISK-PR-3 — the hot-reload path in S-029 does NOT
  re-run this check. Documented limitation; a `mv`+`chmod` race remains
  the operator's responsibility.

## Decisions worth recording

* **File shape**: flat name->entry dict (D10 / llm-image-api reference),
  NOT a nested `{"presets": {...}}` shape. Error-path prefix `presets.`
  is added by `_format_validation_error()` so operator-facing messages
  match FR-PR-02's example (`presets.quality.defaults.temperature`).
* **`PresetConfig` lacks `extra="forbid"` at root**: Pydantic 2.x does
  not let `RootModel` set top-level `extra`. The forbid invariant lives
  on `PresetEntry` / `PresetDefaults` / `PresetPostprocess`, which is
  sufficient — unknown top-level keys land as new preset names, which
  is intentional (operator-defined presets per FR-PR-12).
* **Provider cross-check restricted to registered providers**: a preset
  pinning a provider that auto-select filtered out is misconfiguration,
  not "we'll discover it at request time." Symmetric with the
  `(provider, model)` allow-list check.
* **Permission check runs before parse**: defense-in-depth ordering. A
  world-writable file never reaches the JSON parser.

## Test coverage

* 28 new tests in `tests/test_presets_config.py`.
* Full suite: 406 passed, 2 skipped, 1 xfailed, 3 deselected (cycle-1
  baseline of 380 + 26 net-new passing presets tests).
* `tests/test_openai_adapter_parity.py` (S-018 paired UAT) byte-identical
  to its `master` form per `git diff master tests/test_openai_adapter_parity.py`.
* Gates: `ruff check`, `ruff format --check`, `mypy --strict src/`,
  `pytest`, `pip-audit` — all green.

## Follow-ups (NOT this story)

* S-028 — implement `resolve_preset` per the locked signature; add
  `SynthesizeRequest.preset: str | None`; emit `X-Preset-Effective` /
  `X-Preset-Ignored-Knobs`; ship UAT-PR-01..07.
* S-029 — generalize the cycle-1 watcher primitive; ship validating
  reloader that calls `initialize_preset_registry` and swaps atomically;
  request-scoped snapshot capture; UAT-PR-08/09/15.
* S-031 — consume `TTS_SILENCE_TRIM_THRESHOLD_DB` in the silence-trim
  step.

---

# S-028 — Preset resolution + EffectiveSynthesisConfig
**Branch:** sprint-7-S-028 (merged)


> Sprint: 7
> Status: READY-FOR-REVIEW
> Branch: `sprint-7-S-028` (worktree `.worktrees/sprint-7/S-028`)
> Refs: FR-PR-04, FR-PR-06..10, BR-10, BR-12, BR-17, NFR-PT-05, NFR-PR-02

## What landed

| Task | Surface | File(s) |
|------|---------|---------|
| T1 | `EffectiveSynthesisConfig` frozen dataclass (slots) — single shape consumed downstream by all synthesis code | `src/llm_tts_api/services/synthesize_service.py` |
| T2 | `SynthesizeRequest.preset: str \| None` (open string, examples document the three built-ins) | `src/llm_tts_api/schemas/synthesis.py` |
| T3 | `resolve_preset(request, snapshot, settings) -> EffectiveSynthesisConfig` — pure, BR-10 precedence, FR-PR-08 conflict log, FR-PR-09 soft-ignore | `src/llm_tts_api/services/synthesize_service.py` |
| T4 | Wiring into `synthesize_core`: snapshot captured once from `request.app.state.preset_registry`; emits `X-Preset-Effective` always and `X-Preset-Ignored-Knobs` when non-empty; OpenAI adapter strips both via extended `_RICH_ONLY_HEADERS` | `src/llm_tts_api/services/synthesize_service.py`, `src/llm_tts_api/routers/audio.py` |
| T5 | `validation_error.preset_unknown` registered in the taxonomy + README error table updated | `src/llm_tts_api/errors.py`, `README.md` |
| T6 | 11 new tests in `tests/test_preset_resolution.py` covering UAT-PR-01..07 + a parametrized byte-identity case (`rich(preset=balanced) ↔ OpenAI-default`); `SpeechRequest` gains `extra="forbid"` so UAT-PR-07 returns 422 | `tests/test_preset_resolution.py`, `src/llm_tts_api/schemas/speech.py` |

## Locked Service Interface adopted verbatim

`resolve_preset` is implemented with the signature locked in S-027's
impl notes:

```python
def resolve_preset(
    request: SynthesizeRequest,
    snapshot: PresetRegistry,
    settings: Settings,
) -> EffectiveSynthesisConfig: ...
```

The resolver is **pure**: no `app.state` read, no I/O. The caller
(`synthesize_core`) is responsible for capturing the registry snapshot
at request-start. Until S-029 lands a real request-scoped capture, the
snapshot is read once from `request.app.state.preset_registry` at the
top of `synthesize_core` (the current invariant — lifespan-only
mutation — already gives that read request-scoped semantics).

## Header shape (FR-PR-08 / FR-PR-09)

* `X-Preset-Effective: <name>(field=value,...)` — always emitted on the
  rich path. Fields are listed in sorted order so operators see a
  stable shape across requests. `response_format` is always included;
  every other knob is included only when non-`None`.
* `X-Preset-Ignored-Knobs: knob1,knob2,...` — emitted on the rich path
  only when at least one knob was soft-ignored. Current pipeline is
  wav-only, so a preset's `response_format=flac|wav24` lands in the
  ignored set until S-033 extends format support.

Both headers are in `_RICH_ONLY_HEADERS` so the OpenAI adapter strips
them — that preserves the S-018 byte-identity invariant **and** the
OpenAI-identical response shape (FR-PR-10 / NFR-PT-05).

## Soft-ignore matrix (S-028 scope)

Only `response_format` is currently a soft-ignore candidate. The
resolver checks the resolved format against
`_PIPELINE_SUPPORTED_FORMATS = {"wav"}` and appends `response_format`
to `ignored_knobs` when the preset asks for anything else. S-033 will
expand the supported set and shrink the ignored set automatically.

Per BR-17 / FR-PR-09: postprocessing knobs (`rms_normalize`,
`silence_trim`, `denoise`) are **service-layer**-driven and never
soft-ignored — they ride into `EffectiveSynthesisConfig.postprocess`
intact for S-031 to consume.

## Conflict precedence (BR-10 / FR-PR-08)

The internal `_pick(field, explicit, preset)` closure realizes the
precedence rule per-field:

1. `explicit is not None and preset is not None and explicit != preset`
   → log WARN with `request_id`, record `field → repr(explicit)` in
   `effective_overrides`, return `explicit`.
2. `explicit is not None` → return `explicit`.
3. otherwise → return `preset` (which may be `None` — falls through
   to downstream Settings/VoiceRecord defaults in `_build_voice_config`).

`response_format` is handled outside `_pick` because
`SynthesizeRequest.response_format` is `Literal["wav"]` with a default
of `"wav"` — operator-explicit and Pydantic-default look identical at
the schema level, so the preset's `response_format` wins when set
(deferring the explicit-vs-default disambiguation to S-033 / future
schema work).

## S-018 byte-identity (NFR-PT-05 / RISK-PR-5)

* `tests/test_openai_adapter_parity.py` is **byte-identical** to its
  cycle-1 form (verified via `git diff master tests/test_openai_adapter_parity.py`
  → empty).
* All three paired UAT cases pass post-S-028 (`uv run pytest
  tests/test_openai_adapter_parity.py -v` — 3 passed).
* A new parametrized case in `tests/test_preset_resolution.py`
  exercises `rich(preset='balanced') ↔ OpenAI-default` and asserts the
  same sha256 — the load-bearing invariant per RISK-PR-5.

## Test surface

* 11 new tests in `tests/test_preset_resolution.py` (UAT-PR-01..07 +
  HTTP-level + header-level + the new paired byte-identity case).
* Full suite: **417 passed, 2 skipped, 1 xfailed, 3 deselected** (baseline 406 + 11 net-new).
* Conftest changes: seeded `app.state.preset_registry` with a
  3-preset stub matching the shipped registry, plus `tts_default_preset`
  /`tts_presets_file`/`tts_silence_trim_threshold_db` on the stub
  `Settings`. Two ad-hoc fixtures in `tests/test_concurrency.py` and
  `tests/test_perf_regression.py` were extended to seed the registry
  the same way (they bypass the shared conftest fixture and build
  their own app.state).

## Gates

* `uv run ruff check .` — clean
* `uv run ruff format --check .` — clean
* `uv run mypy --strict src/` — Success
* `uv run pytest` — 417 passed, 2 skipped, 1 xfailed
* `uv run pip-audit` — no known vulnerabilities

## Decisions worth recording

* **`response_format` is taken from the preset when set.** Because
  `SynthesizeRequest.response_format` is `Literal["wav"]` defaulting
  to `"wav"`, the resolver cannot tell operator-explicit `wav` from
  the schema default. Letting the preset win matches the BR-10 spirit
  (preset > Settings) and routes the future-flac path through the
  soft-ignore mechanism today.
* **Soft-ignore is captured but not enforced downstream.** The current
  pipeline still uses `payload.response_format` (always `"wav"`) for
  the actual synthesis path — the EffectiveSynthesisConfig records
  what the preset *resolved to* + what was *ignored*; S-033 will wire
  the resolved format into the format-conversion step. This split
  keeps UAT-PR-02 ("quality preset → flac in EffectiveSynthesisConfig")
  truthful without forcing a flac encoder into S-028's scope.
* **Resolver is HTTP-aware but pure.** It raises
  `OpenAIHTTPException(validation_error.preset_unknown)` directly so
  the call site doesn't have to translate. The function still has no
  side effects beyond raising — testable in unit form.
* **TypeVar at module scope.** `_T` is module-level (not nested) so
  the `_pick` closure type-checks under `mypy --strict`. Closures over
  function-local TypeVars are not supported by mypy in non-PEP-695
  Python.

## Follow-ups (NOT this story)

* **S-029** — replace the inline `request.app.state.preset_registry`
  read in `synthesize_core` with a request-scoped snapshot capture
  (FastAPI dependency or middleware) so a mid-flight hot-reload
  cannot tear the registry. The locked resolver signature already
  takes the snapshot as an explicit argument — no resolver changes
  needed.
* **S-031** — consume `EffectiveSynthesisConfig.postprocess` in the
  postprocessing pipeline; honor `TTS_SILENCE_TRIM_THRESHOLD_DB`.
* **S-033** — extend `_PIPELINE_SUPPORTED_FORMATS` (and the format
  conversion step) to include `flac` / `wav24`; the soft-ignore set
  shrinks automatically.

---

# S-029 — Preset hot-reload + in-flight snapshot
**Branch:** sprint-7-S-029 (merged)


Story: **S-029** (sprint 7, cycle 2)
Branch: `sprint-7-S-029`
Status: READY-FOR-REVIEW

## What landed

### T1 — `ConfigWatcher` primitive (extract from cycle-1 S-011)

Module: `src/llm_tts_api/services/config_watcher.py`.

Generic watcher parameterised by:

* `path: Path | None` — `None` is a clean no-op (cycle-1 FR-VM-05
  "unset is valid" semantics preserved).
* `on_change: Callable[[], Awaitable[None]]` — invoked once per detected
  touch of the resolved target path.
* `force_polling: bool` — surfaces watchfiles' polling backend for Docker
  bind-mounts (RISK-3).
* `step_ms: int = 200` — same 200 ms cadence cycle-1 used; well under the
  NFR-PR-03 ≤2 s SLO.

Internals: `awatch(parent_dir, …)` then filter the change stream to
events that resolve to the target path. Editor "save = rename" patterns
are handled because the watch root is the parent directory, not the
file. A callback that raises is logged and the watcher loop continues —
a downstream bug must never crash the watcher task (NFR-OP-05 spirit).

`services/voice_store/seed_ingestion.py::VoiceSeedIngestor.watch_and_ingest`
was refactored to delegate to `ConfigWatcher`; behavior is preserved
(all 23 cycle-1 voice-map tests still pass, including UAT-VM-03's
≤2 s reload test).

### T2 — `PresetRegistryReloader`

Module: `src/llm_tts_api/services/presets/reloader.py`.

Run-loop: `await ConfigWatcher(..., on_change=self.reload_once).watch()`.

`reload_once()` is the validate-before-swap routine:

1. `load_preset_registry(path)` — JSON parse + Pydantic schema.
2. Default-preset check — `TTS_DEFAULT_PRESET` must still resolve.
3. `validate_preset_providers(registry, allow_lists)` — FR-PR-13
   cross-check restricted to currently-registered providers (same
   helper as startup).
4. On all-green: `on_swap(new_registry)`. The lifespan binds this to
   `app.state.preset_registry = new_registry` — an atomic frozen-object
   swap, never a per-key mutation.
5. On any failure: WARN log keyed by `preset_reload_failed` carrying
   the `config_error.*` code + field-path detail, and the prior
   registry stays live (NFR-SE-10).

**Permission posture is intentionally NOT re-run on reload** per
RISK-PR-3 / NFR-OP-PR-3. A test (`test_reload_skips_permission_check`)
pins this behavior so a future refactor doesn't accidentally add it
back and break the documented contract.

`force_polling_from_env()` reads a new `TTS_PRESETS_WATCH_FORCE_POLLING`
env var (parallel to cycle-1's `TTS_VOICE_MAP_WATCH_FORCE_POLLING`).

### T3 — In-flight snapshot pattern

Module: `src/llm_tts_api/dependencies.py::get_preset_registry_snapshot`.

A FastAPI `Depends`-shape getter that reads
`request.app.state.preset_registry` exactly once at request-entry,
binding the captured `PresetRegistry` for the whole request lifecycle.

**Contract for S-028 (locked in `sprint-impl-7.md` § "Locked Service
Interface"):**

```python
def resolve_preset(
    request: SynthesizeRequest,
    snapshot: PresetRegistry,   # <- bound via Depends(get_preset_registry_snapshot)
    settings: Settings,
) -> EffectiveSynthesisConfig: ...
```

S-028 wires this into `synthesize_core` via FastAPI dependency
injection. The resolver MUST consume the `snapshot` argument and MUST
NOT re-read `app.state.preset_registry`, otherwise a mid-flight S-029
swap could tear the resolution. The reverse contract (S-029 only writes
through `on_swap`, S-028 only reads through the snapshot) means there's
no shared mutable state between the two stories' code paths.

This is verified end-to-end by
`test_in_flight_snapshot_survives_mid_flight_swap`: a snapshot bound
before `reload_once()` retains the prior preset set even after the
slot has been swapped.

### T4 — Lifespan wiring

`src/llm_tts_api/main.py::lifespan`:

* After `_load_presets_or_exit` initialises `app.state.preset_registry`,
  construct a `PresetRegistryReloader` whose `on_swap` writes the new
  registry back to `app.state.preset_registry`.
* Spawn `asyncio.create_task(reloader.watch(), name="preset-registry-reloader")`
  (the same pattern S-011 already uses for the voice-map watcher).
* Stash the reloader on `app.state.preset_reloader` for observability.
* On shutdown: cancel the task and `await` it under
  `contextlib.suppress(asyncio.CancelledError, Exception)` BEFORE the
  cycle-1 S-010 concurrency drain — same ordering pattern as the
  voice-map watcher.

### T5 — Tests

* `tests/test_config_watcher.py` (4 cases): file-change detection,
  None-path no-op, unrelated-directory-changes ignored, callback-error
  resilience.
* `tests/test_preset_hot_reload.py` (5 cases):
  * UAT-PR-08 valid swap within ≤2 s
  * UAT-PR-15 invalid edit keeps prior registry + WARN log
  * RISK-PR-3 permission check skipped on reload
  * Reload rejects unknown `TTS_DEFAULT_PRESET`
  * UAT-PR-09 in-flight snapshot survives mid-flight swap
* Cycle-1 regression: all 23 `test_voice_seed_ingestion.py` tests still
  pass with the `ConfigWatcher`-backed `VoiceSeedIngestor.watch_and_ingest`.

## Gates

| Gate | Result |
|------|--------|
| `uv run ruff check .` | green |
| `uv run ruff format --check .` | green |
| `uv run mypy --strict src/` | green (57 files) |
| `uv run pytest` | **415 passed**, 2 skipped, 3 deselected, 1 xfailed |
| `uv run pip-audit` | no known vulnerabilities |

Baseline was 406 passed; +9 from S-029 (4 watcher + 5 reloader).

## Notes for the reviewer

* The S-027 ↔ S-029 lifespan ordering invariant is preserved: reloader
  starts AFTER `_load_presets_or_exit` populates the initial registry,
  so the first watcher tick never observes an unset
  `app.state.preset_registry` (RISK row in the sprint plan).
* `PresetRegistry` is a frozen dataclass: the swap rotates the whole
  object, never mutates a member. This is what makes the in-flight
  snapshot semantic free (a request holding the prior reference cannot
  see structural changes from a swap).
* The reloader is wired directly in `lifespan` (not in
  `build_default_dependencies`) because it requires `app` to write back
  `app.state.preset_registry`. Tests construct the reloader manually
  with a captured `on_swap` callback — see `test_preset_hot_reload.py`.

## Coordination with S-028 (running in parallel)

S-028 will wire `resolve_preset` into `synthesize_core`. The
contract S-029 has established is:

* S-028 declares a parameter `snapshot: PresetRegistry =
  Depends(get_preset_registry_snapshot)` on its resolver entry point.
* S-029's reloader writes through `on_swap` only; it never touches
  the resolver path.
* If S-028 instead reads `request.app.state.preset_registry` inside
  the resolver, that is a contract violation (NFR-PR-04 in-flight
  snapshot invariant) and the story-review phase must flag it.

---

---

# Story Reviews

# S-027 — Story-level review (Phase 1S cross-task coherence)

Sprint: 7
Scope: S-027 + its two dependents S-028 and S-029 (the "Locked Service
Interface" contract documented in `sprint-impl-7.md`).
Review branch: `sprint-7-S-027-review` (worktree
`.worktrees/sprint-7/S-027-review`).

## Verdict

**One coherence gap found and fixed in this review.** All locked-contract
invariants now hold end-to-end. Gates re-run green:

| Gate | Result |
|------|--------|
| `uv run ruff check .` | clean |
| `uv run ruff format --check .` | clean |
| `uv run mypy --strict src/` | Success — 57 source files |
| `uv run pytest` | 426 passed, 2 skipped, 3 deselected, 1 xfailed |
| `uv run pytest tests/test_openai_adapter_parity.py -v` | 3 passed |
| `git diff master -- tests/test_openai_adapter_parity.py` | empty (byte-identical) |
| `uv run pytest tests/test_voice_seed_ingestion.py -v` | 20 passed (cycle-1 UAT-VM-* intact) |

## Locked-contract checklist (S-027 → S-028 / S-029)

1. **`PresetRegistry` snapshot type** — frozen dataclass; atomic swap on
   reload; never mutated in place. ✅ Held by `services/presets/registry.py`
   and exercised by `test_in_flight_snapshot_survives_mid_flight_swap`.
2. **`PresetEntry` / `PresetDefaults` / `PresetPostprocess` schema** —
   `extra="forbid"` at every level; bounded numeric fields; field-path
   errors prefixed with `presets.`. ✅ Held by `services/presets/config.py`;
   covered by `tests/test_presets_config.py`.
3. **`resolve_preset(request, snapshot, settings)` signature** — implemented
   verbatim in `services/synthesize_service.py`; resolver is pure (no
   `app.state` read, no I/O). ✅
4. **`app.state.preset_registry` slot** — written only by `lifespan`
   (initial value) and the reloader's `on_swap` callback. ✅ Verified
   by `grep`; no other writer in `src/`.
5. **Error-code ownership** — `config_error.{presets_invalid,
   preset_provider_invalid, presets_unsafe_permissions}` registered in
   S-027; `validation_error.preset_unknown` registered in S-028; no
   duplication. ✅

## NFR-PT-05 — S-018 byte-identity invariant (S-028 spotlight)

- `tests/test_openai_adapter_parity.py` byte-identical to master:
  `git diff master -- tests/test_openai_adapter_parity.py` returns
  empty. ✅
- The full 3-case parity suite passes post-S-028 + post-this-fix
  (`uv run pytest tests/test_openai_adapter_parity.py -v` → 3 passed).
- The OpenAI adapter strips `X-Preset-Effective` /
  `X-Preset-Ignored-Knobs` via `_RICH_ONLY_HEADERS` in
  `routers/audio.py`, so the response shape stays OpenAI-identical.
- UAT-OA-03 static check still passes (the only `*synthesize*` import
  in `routers/audio.py` is `synthesize_core`); the `create_speech`
  handler body is still ≤ 30 LOC.

## Cycle-1 regression (S-029 spotlight)

- `VoiceSeedIngestor.watch_and_ingest` now delegates to the extracted
  `ConfigWatcher` primitive. All 20 cycle-1 voice-seed-ingestion tests
  pass unchanged (`tests/test_voice_seed_ingestion.py`), including the
  UAT-VM-03 `≤ 2 s reload` case. The cycle-1 seed-ingestion contract
  is preserved.

## Coherence gap found and fixed

### Gap: `get_preset_registry_snapshot` was unused

The S-029 impl notes ("Coordination with S-028") state:

> S-028 declares a parameter
> `snapshot: PresetRegistry = Depends(get_preset_registry_snapshot)`
> on its resolver entry point.
> […]
> If S-028 instead reads `request.app.state.preset_registry` inside
> the resolver, that is a contract violation (NFR-PR-04 in-flight
> snapshot invariant) and the story-review phase must flag it.

S-028 honored the *spirit* of the contract — `resolve_preset` itself
is pure and takes `snapshot` as an explicit argument — but the
snapshot was captured inline at the top of `synthesize_core`:

```python
preset_registry: PresetRegistry = request.app.state.preset_registry
effective = resolve_preset(payload, preset_registry, settings)
```

instead of via the dependency declared in `dependencies.py:307`.
Functionally equivalent today (the read happens before any `await` on
the resolution path, and `PresetRegistry` is frozen), but it left
`get_preset_registry_snapshot` as dead code and the locked-contract
wire-point implicit rather than explicit.

### Fix (commit on this review branch)

Wired the dependency through both endpoints:

- `routers/synthesize.py` — `synthesize` handler now declares
  `preset_snapshot: Annotated[PresetRegistry, Depends(get_preset_registry_snapshot)]`
  and passes it to `synthesize_core`.
- `routers/audio.py` — `create_speech` handler does the same.
- `services/synthesize_service.py::synthesize_core` — now takes
  `preset_snapshot: PresetRegistry` as a keyword-only argument; the
  inline `request.app.state.preset_registry` read is gone. The
  in-flight-snapshot semantic is now explicit in the endpoint
  signatures rather than hidden inside the service function.

Test surface unchanged (no fixture updates needed — the tests that
construct app.state directly continue to seed `app.state.preset_registry`,
which `get_preset_registry_snapshot` reads via `request.app.state`).
UAT-OA-03 static checks still hold: the new imports come from
`services.presets` and `dependencies`, not from any `*synthesize*`
module.

## Risks reviewed and confirmed mitigated

| Sprint plan risk | Where mitigated | Verified |
|---|---|---|
| NFR-PT-05 — S-018 byte-identity breaks under preset resolution drift | S-028 T6 + this review | parity diff empty; 3/3 paired UAT pass |
| S-027 ↔ S-029 lifespan coupling (reloader races with initial validation) | S-029 T4 sequencing | reloader spawned after `_load_presets_or_exit` in `main.py::lifespan` |
| S-028 ↔ S-029 resolver-signature alignment | locked in S-027 impl notes | resolver matches contract verbatim |
| Permission posture not re-run on reload (RISK-PR-3) | S-029 reloader | pinned by `test_reload_skips_permission_check` |
| `watchfiles` flaky in Docker (RISK-3) | `force_polling_from_env` via `TTS_PRESETS_WATCH_FORCE_POLLING` | parallels cycle-1 voice-map mechanism |

## Recommendation

S-027 / S-028 / S-029 are **READY-FOR-MERGE** at the story level after
the wiring fix on this review branch. No further sub-task changes
required for the cycle-2 Step 2 bundle.

---

# S-028 Story-Level Review (Phase 1S — cross-task coherence)

**Scope:** S-028 "Preset resolution + EffectiveSynthesisConfig" — verify T1–T6
fit together coherently and that the locked Service Interface from S-027 is
honored.

**Result:** ✅ No cross-task coherence issues found.

---

## Verifications performed

| Check | Result |
|-------|--------|
| `git diff master -- tests/test_openai_adapter_parity.py` | empty (file byte-identical to cycle-1 master) |
| `uv run pytest tests/test_openai_adapter_parity.py -v` | 3 passed (UAT-PT-05 invariant intact) |
| `uv run pytest` (full suite) | 426 passed, 2 skipped, 1 xfailed, 3 deselected — matches baseline |
| `uv run mypy --strict src/` | Success, 57 files |
| `uv run ruff check .` | All checks passed |
| Cycle-1 voice-map seed-ingestion regression (`tests/test_voice_seed_ingestion.py`) | 20 passed (post-`ConfigWatcher` refactor; cycle-1 UAT-VM-* intact) |

> Note: `sprint-impl-7.md` S-029 section claims "all 23 cycle-1 voice-map
> tests still pass"; the actual count in `tests/test_voice_seed_ingestion.py`
> is 20. This is a documentation count mismatch in S-029's impl notes (not a
> coherence defect) — flagged for the S-029 review.

## T1–T6 coherence within S-028

* **T1 ↔ T3** — `resolve_preset` returns `EffectiveSynthesisConfig`
  (`synthesize_service.py:140`, `:214`). Single shape consumed downstream.
* **T2 ↔ T3** — `SynthesizeRequest.preset: str | None` (`schemas/synthesis.py:41`)
  is the resolver's `request.preset` input (`synthesize_service.py:152`).
  Open-string per FR-PR-04.
* **T3 ↔ T5** — Resolver raises `OpenAIHTTPException` with the
  `validation_error.preset_unknown` code (`synthesize_service.py:164`), which
  is the same code registered in the taxonomy (`errors.py:74`).
* **T3 ↔ T4** — `resolve_preset` is pure (no `app.state` reads inside the
  function); the caller `synthesize_core` captures the registry once at
  `synthesize_service.py:561` and passes it as the locked `snapshot`
  argument. This matches S-027's locked Service Interface verbatim.
* **T4 ↔ NFR-PT-05** — `X-Preset-Effective` and `X-Preset-Ignored-Knobs` are
  added to `_RICH_ONLY_HEADERS` (`routers/audio.py:73–74`); the OpenAI
  adapter strips them. The byte-identity invariant is verified by both the
  unchanged `test_openai_adapter_parity.py` and the new parametrized
  `rich(preset='balanced') ↔ OpenAI-default` case.
* **T6 ↔ T2** — `SpeechRequest` gains `extra="forbid"` so the OpenAI surface
  rejects a stray `preset` field per UAT-PR-07. This preserves the contract
  that preset selection is a rich-endpoint affair only.

## Locked Service Interface (S-027 → S-028) — adopted verbatim

```python
def resolve_preset(
    request: SynthesizeRequest,
    snapshot: PresetRegistry,
    settings: Settings,
) -> EffectiveSynthesisConfig: ...
```

`synthesize_service.py:136–215` matches the locked shape exactly: three
explicit args, pure (no `app.state`, no I/O beyond the documented raise),
BR-10 precedence realized via the `_pick` closure, soft-ignore set populated
only for `response_format ∉ {"wav"}`. No drift from S-027's frozen contract.

## In-flight snapshot semantics (NFR-PR-04)

`synthesize_core` reads `request.app.state.preset_registry` exactly once at
`synthesize_service.py:561` — before any `await` — and the captured
`PresetRegistry` reference is the only one used for the remainder of the
request. Because `PresetRegistry` is a frozen dataclass and S-029's reloader
swaps the slot atomically (rather than mutating), this single-read pattern
gives the same tear-free guarantee as the `Depends(get_preset_registry_snapshot)`
dependency S-029 ships.

**Observation (not a S-028 defect):** S-029's
`dependencies.py::get_preset_registry_snapshot` helper is defined but no
production caller wires it in; the in-flight invariant is upheld through the
direct read in `synthesize_core`. The S-028 impl notes acknowledge this and
flag the dependency-injection rewrite as a follow-up. Forwarding to the
S-029 story-level review for visibility.

## Conclusion

S-028 is coherent across its tasks and faithful to the S-027 locked
interface. NFR-PT-05 (S-018 byte-identity) is preserved both by the
unchanged parity test and by the new parametrized sha256 check inside
`tests/test_preset_resolution.py`. No fixes required.

---

# S-029 Story-Level Review (Phase 1S — cross-task coherence)

**Reviewer:** code-reviewer (story-level)
**Date:** 2026-05-19
**Branch under review:** `sprint-7-S-029` (merged) → `sprint-7-S-029-review` worktree
**Scope:** Cross-task coherence inside S-029 only (Phase 1S). Cross-story coherence with S-027/S-028 is Phase 2S and out of scope.

## Verdict

**No cross-task coherence issues found.** All five tasks compose into a self-consistent feature: ConfigWatcher (T1) is the I/O primitive, PresetRegistryReloader (T2) wires validate-before-swap on top of it, the snapshot dependency (T3) is the read-side counterpart guarded by FastAPI's once-per-request `Depends` resolution, lifespan (T4) connects both ends, and the test matrix (T5) exercises each seam (incl. the cross-task invariant — snapshot survives mid-flight swap).

## Verification performed

| Check | Result |
|---|---|
| `uv run pytest tests/test_openai_adapter_parity.py -v` | **3 passed** (NFR-PT-05 holds end-to-end) |
| `git diff master -- tests/test_openai_adapter_parity.py` | **empty** (test file byte-identical to cycle-1) |
| `uv run pytest tests/test_voice_seed_ingestion.py` | **20 passed** — cycle-1 UAT-VM-01..05 + the rest of S-011 still green post-`ConfigWatcher` refactor of `seed_ingestion.py` |
| `uv run pytest tests/test_config_watcher.py` | **4 passed** (T1) |
| `uv run pytest tests/test_preset_hot_reload.py` | **5 passed** (T2/T3/T4 incl. UAT-PR-08/09/15) |
| `uv run pytest` (full) | **426 passed, 2 skipped, 1 xfailed, 3 deselected** — exact baseline parity |
| `uv run mypy --strict src/` | **clean across 57 source files** — exact baseline parity |

## Cross-task coherence findings

### T1 ↔ cycle-1 S-011 (the refactor surface)
`services/voice_store/seed_ingestion.py::VoiceSeedIngestor.watch_and_ingest` now delegates entirely to `ConfigWatcher` (no `awatch` import remains in seed_ingestion.py). The cycle-1 contract is preserved:
- `seed_file_path=None` ⇒ no-op (FR-VM-05 "unset is valid") — `ConfigWatcher.watch()` returns immediately when `path is None`.
- 200 ms `step` cadence preserved.
- `force_polling` plumbed through (RISK-3 Docker bind-mount path).
- Editor save-as-rename handled by watching the **parent directory** then filtering on `Path.resolve() == target` — same primitive cycle-1 used.
- All 20 cycle-1 voice-seed tests pass; specifically UAT-VM-01 (initial seed) and UAT-VM-03 (≤2 s hot-reload) still hold.

### T1 ↔ T2 (watcher → reloader)
`PresetRegistryReloader.watch()` constructs `ConfigWatcher(path=settings.tts_presets_file, on_change=self.reload_once, force_polling=…)`. The reloader is purely "what to do on change," and the watcher is purely "when did it change." No leakage in either direction. Callback exceptions are swallowed inside `ConfigWatcher` (logged via `logger.exception`), so a `reload_once` bug cannot crash the watcher task — coherent with the watcher's stated NFR-OP-05-spirit guarantee.

### T2 validate-before-swap chain
`reload_once()` runs exactly the cycle-2 startup validation chain MINUS the permission check (RISK-PR-3 documented carve-out), in the right order:
1. `load_preset_registry(path)` — schema parse.
2. Default-preset existence check against `settings.tts_default_preset`.
3. `validate_preset_providers(registry, allow_lists)` — same helper as startup (`_allow_lists_from_settings`).
4. Only on all-green: `self._on_swap(new_registry)`.

Every failure path emits a single WARN line keyed `preset_reload_failed` with the corresponding `config_error.*` code from the cycle-2 taxonomy, then `return`s — the prior registry stays live (NFR-SE-10). The reloader **never raises**, matching the docstring contract and the watcher's design that callback exceptions are swallowed anyway.

The permission-skip is pinned by `test_reload_skips_permission_check`, so a future refactor that re-adds the check will trip a red test rather than silently change the documented contract.

### T3 ↔ T4 (snapshot read ↔ atomic write)
- T4 writes via the `on_swap` closure: `app.state.preset_registry = new_registry` — one assignment of a frozen dataclass reference, never an in-place mutation.
- T3 reads via `get_preset_registry_snapshot(request)` which returns `cast(PresetRegistry, request.app.state.preset_registry)`. FastAPI resolves `Depends(...)` once per request before the handler body executes — the captured reference cannot tear under a mid-flight T2 swap because the swap rotates the slot's reference, not the object's interior.
- The `test_in_flight_snapshot_survives_mid_flight_swap` test exercises exactly this seam: bind the snapshot, call `reload_once()` (which swaps the slot), then assert the snapshot still resolves the prior preset set.

This is the cross-task invariant most likely to drift; the test is explicit and the contract is documented both in the dependency's docstring and `sprint-impl-7.md` §"Locked Service Interface" — coherent.

### T4 lifespan ordering
`main.py::lifespan` performs the required sequence:
1. Build deps, stash settings/registries on `app.state`.
2. `_load_presets_or_exit(...)` — initial validated load (S-027 hand-off) before the reloader exists. The risk-row "reloader hooked into lifespan must not race with initial validation" is structurally impossible: the reloader is constructed AFTER the slot is populated, so the first watcher tick can never observe an unset slot.
3. Construct `PresetRegistryReloader(on_swap=…)`; stash on `app.state.preset_reloader` for diagnostics.
4. `asyncio.create_task(reloader.watch(), name="preset-registry-reloader")`.
5. On shutdown: cancel the task, `await` it under `contextlib.suppress(CancelledError, Exception)`, BEFORE the cycle-1 S-010 drain — symmetric with the voice-map watcher cancellation pattern. Coherent with the sprint plan's risk mitigation.

### T2 / T4 `force_polling` plumbing
`presets.reloader.force_polling_from_env()` reads `TTS_PRESETS_WATCH_FORCE_POLLING` independently of the cycle-1 `TTS_VOICE_MAP_WATCH_FORCE_POLLING` — intentional, both surfaces are documented and namespaced. No accidental coupling.

### T5 test taxonomy covers each seam
- T1 (watcher): change detection, none-path no-op, unrelated-dir-changes ignored, callback-error resilience — every behavior the reloader relies on.
- T2 (reloader): UAT-PR-08 valid swap, UAT-PR-15 invalid edit keeps prior, RISK-PR-3 permission skip, unknown-default rejected — the full validation matrix.
- T3 (snapshot): in-flight snapshot survives mid-flight swap — the load-bearing NFR-PR-04 invariant.
- Implicit cross-cut: cycle-1 `test_voice_seed_ingestion.py` is the regression net for T1's "do not break the existing consumer" promise.

## Items examined and explicitly cleared

- **NFR-PT-05 — S-018 byte-identity preserved.** `git diff master -- tests/test_openai_adapter_parity.py` is empty; the three parametrized cases pass. S-029 does not touch the OpenAI parity path, but this story-review's brief required the explicit verification.
- **Cycle-1 voice-map seed ingestion still works post-refactor.** All 20 `test_voice_seed_ingestion.py` tests pass (UAT-VM-01..05 inclusive).
- **`force_polling=False` default in T1 vs. T2 plumbing.** T1's default is `False`; T2 reads its own env helper to compute the flag and forwards explicitly. No silent default-mismatch.
- **Reloader as `Callable[[PresetRegistry], None]` vs async.** T4's `_swap_preset_registry` is sync; T2's `on_swap` annotation is sync; the only `await` inside `reload_once` is the parse path. Consistent.
- **No dual-write paths to `app.state.preset_registry`.** S-027's initial load and T2's `on_swap` are the only writers; T3's getter is the only reader-of-record on the request path. No other module references `app.state.preset_registry` for writes.

## No fixes required.

This review is informational; no code changes were made on `sprint-7-S-029-review`.

---


---

# Sprint 7 — Sprint-Level Review (Phase 1P cross-story coherence)

**Reviewer:** code-reviewer (sprint-level, Phase 1P)
**Date:** 2026-05-19
**Sprint:** 7 (cycle-2 Step 1+2 — S-027 / S-028 / S-029)
**Worktree:** `.worktrees/sprint-7/sprint-review` (branch `sprint-7-sprint-review`)
**Scope:** Cross-story coherence across the three Sprint-7 stories *after* all three story-level reviews and the S-027-review wiring fix have landed. Phase 1S (intra-story) coherence was cleared in the three story reviews assembled into `sprint-impl-7.md`; this review focuses strictly on the *sprint-level* seams the plan calls out.

## Verdict

**READY-FOR-MERGE at the sprint level. No cross-story coherence issues remain.**

The cycle-2 spine — config → snapshot → resolution → atomic swap — composes cleanly on top of cycle-1's lifespan, `app.state`, error envelope, and the S-011 watcher primitive. The Locked Service Interface from S-027 is honored verbatim by both consumers, the request-scoped snapshot dependency is now wired through both rich endpoints (S-027-review fix), and the S-018 byte-identity invariant (NFR-PT-05) survives intact.

## Gates re-run in this worktree

| Gate | Result | Baseline |
|---|---|---|
| `uv run ruff check .` | clean | clean |
| `uv run ruff format --check .` | clean (103 files) | clean |
| `uv run mypy --strict src/` | Success — **57 source files** | 57 files ✅ |
| `uv run pytest` | **426 passed, 2 skipped, 3 deselected, 1 xfailed** | 426/2/1 ✅ |
| `uv run pytest tests/test_openai_adapter_parity.py -v` | **3 passed** | 3 ✅ |
| `git diff master -- tests/test_openai_adapter_parity.py` | **empty (0 lines)** | empty ✅ |
| `uv run pytest tests/test_voice_seed_ingestion.py` | **20 passed** (post-`ConfigWatcher` refactor) | 20 ✅ |

Exact baseline parity. No regressions.

## 1. Shared cycle-1 infrastructure — does cycle-2 sit cleanly?

| Cycle-1 surface | Cycle-2 use | Verdict |
|---|---|---|
| `main.py::lifespan` | S-027 initializes `app.state.preset_registry`; S-029 spawns `preset-registry-reloader` task after the initial slot is populated; shutdown cancels the reloader before the S-010 drain — same ordering as the voice-map watcher. | ✅ Coherent. Reloader cannot observe an unset slot. |
| `app.state` slots | Single new slot `preset_registry: PresetRegistry`; written by lifespan init + reloader `on_swap` only (verified via `grep`); read by `Depends(get_preset_registry_snapshot)`. | ✅ No dual-write. |
| `errors.py` taxonomy | Three new `config_error.*` codes registered by S-027; `validation_error.preset_unknown` registered by S-028; no duplication. | ✅ Boundary respected per Locked Interface §5. |
| `services/voice_store/seed_ingestion.py` (S-011 watcher) | S-029 T1 extracted the inner mechanic into `services/config_watcher.py::ConfigWatcher`; seed ingestion now delegates. | ✅ All 20 cycle-1 voice-seed tests green, incl. UAT-VM-03 (≤2 s reload). |
| `_RICH_ONLY_HEADERS` in `routers/audio.py` | S-028 T4 extended the set with `X-Preset-Effective` + `X-Preset-Ignored-Knobs`; OpenAI adapter strips both. | ✅ Cycle-1 OpenAI-identity preserved. |

The S-029 `ConfigWatcher` generalization is the highest-leverage piece of cycle-1↔cycle-2 reuse and the place most at risk of regressing the cycle-1 UX. `tests/test_voice_seed_ingestion.py` (20 tests) is the standing regression net; running it in this worktree gives 20 passed — no behavioral drift in the FR-VM-05 (`path=None` no-op), the 200 ms cadence, the `force_polling` plumbing, or the parent-dir-watch + resolve-filter pattern that handles editor save-as-rename.

## 2. Integration boundaries — Locked Service Interface honored?

The Locked Interface (S-027 impl notes §"Locked Service Interface"):

```python
def resolve_preset(
    request: SynthesizeRequest,
    snapshot: PresetRegistry,
    settings: Settings,
) -> EffectiveSynthesisConfig: ...
```

Verified post-S-027-review fix:

* **Resolver shape** — `services/synthesize_service.py::resolve_preset` matches the signature verbatim. Pure (no `app.state` read, no I/O beyond the documented HTTPException raise).
* **Snapshot binding** — both rich endpoints now declare `preset_snapshot: Annotated[PresetRegistry, Depends(get_preset_registry_snapshot)]` and pass it explicitly into `synthesize_core` (`routers/audio.py:146,160`, `routers/synthesize.py:68,80`). `synthesize_core` takes `preset_snapshot` as a keyword-only parameter and forwards it to `resolve_preset` — no inline `request.app.state.preset_registry` read remains on the request path.
* **Atomic-swap writer** — `main.py::lifespan::_swap_preset_registry` is the only write site beyond the initial lifespan load; it rotates the slot to a fresh `PresetRegistry` reference, never mutating the prior object. `PresetRegistry` is `@dataclass(frozen=True, slots=True)`.
* **Reader/writer asymmetry** — `Depends(get_preset_registry_snapshot)` resolves *once per request* before the handler body executes; the captured reference is the resolver's input for the full request lifecycle. Combined with the frozen-object swap, this gives the NFR-PR-04 tear-free guarantee structurally rather than by convention.

The S-027-review commit (`b26d62f` + merge `224f9e0`) is the load-bearing fix that takes the contract from "honored in spirit" to "honored mechanically" — `get_preset_registry_snapshot` is no longer dead code; it is the documented wire-point and now the actual wire-point.

## 3. Behavioral interactions — atomic swap × snapshot semantic

The cross-story invariant most likely to drift is "an in-flight request resolving against a snapshot bound at request-entry must continue to resolve correctly after the reloader has rotated the slot." Two structural guarantees keep this invariant:

1. `PresetRegistry` is immutable; the swap is a slot rotation, not an in-place mutation.
2. `Depends(get_preset_registry_snapshot)` runs once per request before the handler awaits anything; the resolver consumes the captured reference exclusively.

Both are exercised end-to-end by `test_in_flight_snapshot_survives_mid_flight_swap` (in `tests/test_preset_hot_reload.py`): bind snapshot → call `reload_once()` (which swaps) → assert the prior preset set is still resolvable through the snapshot.

The reloader's WARN-then-keep-prior path on invalid edits (`reload_once` returns without calling `on_swap` on any of the three failure modes — schema-invalid, default-preset-unknown, provider-allow-list-fail) is the NFR-SE-10 attack-tolerant counterpart: an attacker writing a tampered file cannot take the service down or replace the running config silently.

## 4. NFR-OP-06 (per-synthesis log line) — premature wiring check

NFR-OP-06 is deferred to S-034. `grep -rn 'synthesis.*completed\|preset_name=.*log\|S-034' src/` returns only `services/synthesize_service.py:215: preset_name=name,` — that line is a *struct field assignment* inside the `EffectiveSynthesisConfig` constructor, not a log emission. **No premature S-034 wiring** in production code. The pre-existing structured log lines in the rich path (`request_id` carrier WARNs on conflicts, `preset_registry_loaded` / `preset_registry_reloaded` info lines) are appropriate Sprint-7 scope and do not pretend to be the deferred NFR-OP-06 per-synthesis line.

## 5. Regression risk — NFR-PT-05 (S-018 paired UAT)

The most load-bearing cycle-2 invariant. Verified end-to-end in this worktree:

* `git diff master -- tests/test_openai_adapter_parity.py` → **0 lines** (byte-identical to master).
* `uv run pytest tests/test_openai_adapter_parity.py -v` → **3 passed**.
* The added parametrized case in `tests/test_preset_resolution.py` exercises `rich(preset='balanced') ↔ OpenAI-default` and asserts identical sha256 on the response body — the load-bearing cycle-2 path through the resolver.

Cycle-1 voice-map regression (UAT-VM-*) post-`ConfigWatcher` refactor: all 20 tests in `tests/test_voice_seed_ingestion.py` pass — the S-029 T1 refactor preserved the cycle-1 behavioral contract.

## 6. Test fixture coverage of the new `app.state` slot

S-028 conftest changes seed `app.state.preset_registry` (plus the three new `Settings` fields `tts_default_preset` / `tts_presets_file` / `tts_silence_trim_threshold_db`) for every request-path test. Two tests that build their own app-state bypass (`tests/test_concurrency.py:267-276`, `tests/test_perf_regression.py:194-202`) also seed the registry — verified by `grep`. No test on the request path can hit a `KeyError`/`AttributeError` on the new slot.

## 7. Minor observations (not Sprint-7 defects)

* **Documentation count nit** flagged by the S-028 review and confirmed here: S-029 impl notes claim "all 23 cycle-1 voice-map tests still pass", actual count is 20 (`tests/test_voice_seed_ingestion.py` collected: 20). Pure doc nit; the impl notes are append-only and the substantive claim (zero regressions) is correct. No fix required.
* **Permission check is startup-only** — explicitly documented in S-027 + S-029 impl notes and pinned by `test_reload_skips_permission_check` (RISK-PR-3 / NFR-OP-PR-3). This is a documented trade-off, not a coherence gap; operators own the `mv`+`chmod` race per the published risk row.

## 8. Risks examined and confirmed mitigated

| Sprint plan risk | Where mitigated | Sprint-level verification |
|---|---|---|
| NFR-PT-05 — S-018 byte-identity breaks under preset resolution drift (RISK-PR-5) | S-028 T6 + S-027 story review | parity diff empty; 3/3 paired UAT pass; new parametrized sha256 case in `test_preset_resolution.py` |
| S-027 ↔ S-029 lifespan coupling (reloader races with initial validation) | S-029 T4 sequencing | reloader spawned only after `_load_presets_or_exit` returns a populated slot; shutdown cancel ordered before S-010 drain |
| S-028 ↔ S-029 resolver-signature alignment | Locked in S-027 impl notes; honored verbatim | `synthesize_service.py::resolve_preset` matches; `Depends(get_preset_registry_snapshot)` wired through both endpoints post-fix |
| `watchfiles` flaky in Docker (RISK-3) | `force_polling_from_env` reading `TTS_PRESETS_WATCH_FORCE_POLLING` | parallels cycle-1's `TTS_VOICE_MAP_WATCH_FORCE_POLLING`; independent env vars; no accidental coupling |
| `PresetConfig` schema drift across cycle-2 stories | `extra="forbid"` at every level; root-level is `RootModel` (operator-defined presets per FR-PR-12 require this) | gated by 28 unit tests in `tests/test_presets_config.py` |

## Recommendation

Sprint 7 is **READY-FOR-MERGE** at the sprint level. The cycle-2 spine is end-to-end coherent, all gates match the cycle-1 baseline, and the load-bearing S-018 byte-identity invariant is preserved both structurally (frozen `EffectiveSynthesisConfig` resolution shared across rich and OpenAI paths via the same `_RICH_ONLY_HEADERS` stripping mechanism) and behaviorally (paired UAT byte-diff is zero). No cross-story fixes were required in this sprint review; the necessary wiring fix already landed during the S-027 story-level review.

The three downstream cycle-2 parallel-Group-H stories (S-030..S-036) can now consume `EffectiveSynthesisConfig`, the request-scoped snapshot, and the validate-before-swap reload semantic on a stable foundation.

