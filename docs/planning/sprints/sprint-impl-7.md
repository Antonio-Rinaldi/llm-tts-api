# Sprint 7 — Implementation Notes

Per-story implementation notes assembled by the sprint-coordinator after each story
completes in its isolated worktree. Companion to `sprint-7.md`.

## Summary

| Story | Type | Status | Worktree branch |
|---|---|---|---|
| S-027 | Technical | READY-FOR-REVIEW | sprint-7-S-027 (merged) |
| S-028 | Technical | READY-FOR-REVIEW | sprint-7-S-028 (merged) |
| S-029 | Technical | READY-FOR-REVIEW | sprint-7-S-029 (merged) |

Sprint 7 status: All stories READY-FOR-REVIEW; pending story + sprint reviews.

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
