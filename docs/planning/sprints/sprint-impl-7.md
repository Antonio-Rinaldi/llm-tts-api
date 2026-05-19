# Sprint 7 — Implementation Notes

Per-story implementation notes assembled by the sprint-coordinator after each story
completes in its isolated worktree. Companion to `sprint-7.md`.

## Summary

| Story | Type | Status | Worktree branch |
|---|---|---|---|
| S-027 | Technical | READY-FOR-REVIEW | sprint-7-S-027 (merged) |
| S-028 | Technical | PLANNED | sprint-7-S-028 (pending — Step 2) |
| S-029 | Technical | PLANNED | sprint-7-S-029 (pending — Step 2) |

Sprint 7 status: Step 1 complete (S-027 merged, 406 tests passing); Step 2 (S-028 + S-029 parallel) pending.

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
