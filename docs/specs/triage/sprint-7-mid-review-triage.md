# Sprint 7 — Mid-Review Triage (Cycle 2)

**Date:** 2026-05-19
**Trigger:** User review of Sprint 7 (READY-FOR-REVIEW state) surfaced 4 issues with the just-landed cycle-2 spine.
**Master at triage:** commit `03f01d1`. 426 tests passing, mypy --strict clean across 57 source files. S-018 byte-identity preserved.
**Status:** Sprint 7 remains READY-FOR-REVIEW. Hotfix sprint scheduled before final approval.

---

## User complaints (verbatim, translated from Italian)

1. "Why does it tell me the Qwen provider is unsupported?"
2. "All Qwen models must be supported AND voice cloning, as they were before the refactor."
3. "How do you choose the model in a preset? In a preset you should be able to choose and configure EVERYTHING."
4. "Update ALL documentation — diagrams, README, examples.http, etc."

---

## Triage classification

| # | Issue | Type | User decision | Disposition |
|---|---|---|---|---|
| 1 | Qwen provider unsupported | (d) docs clarification | Docs only — README explains provider vs model; error message lists valid providers | Hotfix `HF-3` (small code change to error message + README section) |
| 2 | Voice cloning + Qwen models | (d) docs clarification | Docs only — worked example in README + examples.http demonstrates voice CRUD roundtrip | Folded into `HF-1` (docs hotfix) |
| 3 | Preset must configure everything | (b) cycle-2 SRS expansion | Focused: add `language`, `number_lang`, `voice` to `PresetDefaults` (3 fields). NOT maximal expansion. `input` and `stream` stay per-request. | Hotfix `HF-2` + FRS amendment |
| 4 | All docs updated NOW | (c) pull S-036 forward | Pull S-036 forward as hotfix sprint to Sprint 7. Hotfix engineers parallelize HF-1/HF-2/HF-3. | Hotfix sprint = Sprint 7 Path B (Modifications Requested) |

---

## Resolution decisions (locked)

### Resolution T-1 — Qwen provider naming (Issue 1)

**Decision:** Docs clarification only. The provider/model distinction stays (provider = engine, model = checkpoint). No alias, no rename.

**Implementation scope (`HF-3`):**
- README: dedicated subsection under "Architecture" or "Providers" explaining:
  - Provider names (engine identifiers): `mlx_audio`, `voxtral`, `vllm-omni`
  - Each provider's typical models, drawn from `tts_*_model_allowed` env vars
  - Worked example: "to use Qwen3-TTS, set `provider: mlx_audio` + `model: Qwen/Qwen3-TTS-12Hz-0.6B-Base`"
- `src/llm_tts_api/services/tts_providers/registry.py::get()`: enrich the 400 `provider_error.unknown_provider` (or equivalent) error message to list valid provider names + a hint about model-vs-provider. Keep the error code stable (no taxonomy change).

**Out of scope:** adding `qwen` as a provider alias. Renaming `mlx_audio`. Touching X-Provider response header.

**Trace:** addresses user issue 1; no FRS/NFR change required.

---

### Resolution T-2 — Voice cloning is still supported via voice CRUD (Issue 2)

**Decision:** Docs clarification only. Voice cloning works today end-to-end via `POST /v1/tts/voices` (multipart audio + metadata) → `POST /v1/tts/synthesize` with `voice: "<id>"`. UAT-VS-01..12 all pass in master.

**Implementation scope (folded into `HF-1` docs hotfix):**
- README: dedicated subsection "Voice cloning" explaining the cycle-1 retirement of inline `ref_audio` and the cycle-1 introduction of voice CRUD as the canonical path.
- `examples.http`: working voice-cloning roundtrip — upload a voice, synthesize against it, delete it. (See HF-1 below.)
- Confirm in README that `tts_mlx_audio_model_allowed` lists ALL Qwen models that work with the mlx_audio provider.

**Out of scope:** re-adding the retired `ref_audio` field on the rich endpoint. The S-022 voice-store-as-canonical decision stands.

**Trace:** addresses user issue 2; no FRS/NFR change required.

---

### Resolution T-3 — Preset schema expansion: `language` + `number_lang` + `voice` (Issue 3)

**Decision:** Focused expansion. `PresetDefaults` (Pydantic, from S-027) gains three new optional fields:

| Field | Type | Source on `SynthesizeRequest` | Rationale for preset-level default |
|---|---|---|---|
| `language` | `str \| None` | existing | Per-preset pronunciation hint (e.g. `fast` = "en", `quality` = unset) |
| `number_lang` | `str \| None` | existing | Per-preset number-to-word language (often same as `language`) |
| `voice` | `str \| None` | existing | Per-preset default voice id when caller omits one. Useful for "this preset always uses voice X" workflows. |

**Fields that intentionally STAY per-request only:**
- `input` — the actual text to synthesize.
- `stream` — per-request streaming toggle, not a preset attribute.
- `instructions`, `speed`, `stream_format` — OpenAI-shape ignored fields (per S-017 mapping), not exposed on rich `SynthesizeRequest`.

**FRS amendment scope:**
- `FR-PR-03` text amended to enumerate the new fields.
- A new UAT case `UAT-PR-18` added: "preset pinning `voice` + `language` is honored on `/v1/tts/synthesize`; explicit request fields still override per BR-10".
- `EffectiveSynthesisConfig` (S-028) extended with the same 3 fields; resolver merges them with cycle-1 precedence.

**Implementation scope (`HF-2`):**
- `src/llm_tts_api/services/presets/config.py::PresetDefaults` — 3 new fields, validators where applicable.
- `src/llm_tts_api/services/synthesize_service.py::EffectiveSynthesisConfig` — 3 new fields.
- `src/llm_tts_api/services/synthesize_service.py::resolve_preset` — merge the 3 new fields per BR-10 precedence.
- `synthesize_core` downstream consumers — read the new fields from `EffectiveSynthesisConfig` rather than from `request` directly.
- Tests: extend `tests/test_preset_resolution.py` with one new case (UAT-PR-18 happy path + explicit-override regression).
- **Byte-identity gate (NFR-PT-05)**: `tests/test_openai_adapter_parity.py` must still pass byte-identically. The new fields default to `None` everywhere; OpenAI path with default `balanced` preset behaves unchanged.

**Out of scope:** maximal expansion (every `SynthesizeRequest` field). The 3-field focus keeps the cycle-2 schema lean.

**Trace:** amends FR-PR-03; adds UAT-PR-18; no NFR-PT-05 impact; no API surface change beyond `SynthesizeRequest.preset` already shipped.

---

### Resolution T-4 — Pull S-036 forward as hotfix sprint (Issue 4)

**Decision:** Sprint 7 enters **Path B (Modifications Requested)** of the sprint-coordinator workflow. Three hotfix engineers parallelize:

| HF | Title | Scope | Engineer skill |
|----|-------|-------|----------------|
| HF-1 | Cycle-2 docs catch-up (pulled forward from S-036) | README + class diagrams + sequence diagrams + OpenAPI + `examples.http` reflecting cycle-2 state (presets, postproc TBD, format-ext TBD, voice-cloning worked example, provider vs model explanation) | software-engineer |
| HF-2 | Preset schema expansion (T-3 implementation) | `PresetDefaults` + `EffectiveSynthesisConfig` + resolver + UAT-PR-18 | software-engineer |
| HF-3 | Qwen / unknown-provider error message clarification (T-1 implementation) | `registry.py::get()` error message + README section | software-engineer |

**Sprint 7 disposition after hotfixes:**
1. Hotfix sprint executes Path B.
2. Hotfix engineers commit + write `HF-{1,2,3}-impl.md` + status.
3. Coordinator assembles `## Hotfixes` section in `sprint-impl-7.md`.
4. Affected stories re-reviewed (story-review phase narrowed to S-027/S-028 since they own the affected schema + error surface).
5. Sprint review re-run.
6. Sprint 7 re-presented for human approval as a whole.

**Scope discipline for HF-1 (docs catch-up):**
- IN: README (full refresh with cycle-2 sections), `docs/diagrams/class/*.md` (presets module addition), `docs/diagrams/sequence/preset-resolution.md` + `preset-hot-reload.md` (new), `docs/openapi/openapi.yaml` (`preset` field + new error codes + new headers), `examples.http` (full cycle-1 + cycle-2 surface with worked examples per issue 1 + issue 2).
- OUT: postproc-related docs (S-031 not landed yet), format-ext docs (S-033 not landed yet), quality-stream docs (S-032 not landed yet). HF-1 docs cover ONLY what's in master at commit `03f01d1` plus HF-2/HF-3 changes — not future work.
- Quality bar: `/Volumes/Coding/Projects/Applications/epub/llm-image-api/examples.http` for the examples.http shape.

**Out of scope:** marking S-036 itself as DONE — S-036 still owns the FINAL docs refresh after S-031/S-032/S-033/S-034/S-035 land. HF-1 catches docs up to current master only; S-036 catches the remainder up at cycle-2 close.

---

## Next action

**Hand back to the sprint-coordinator** with the four resolutions as Path B inputs. Coordinator dispatches HF-1 / HF-2 / HF-3 in parallel via tmux:

```text
HF-1: Docs catch-up (HF-1)        — software-engineer, .worktrees/sprint-7/HF-1
HF-2: Preset schema expansion     — software-engineer, .worktrees/sprint-7/HF-2
HF-3: Provider error clarity      — software-engineer, .worktrees/sprint-7/HF-3
```

Hotfix order: parallel-safe (disjoint surfaces). HF-1 touches docs + examples.http. HF-2 touches `services/presets/config.py` + `services/synthesize_service.py` + tests. HF-3 touches `services/tts_providers/registry.py` + a small README section.

**Coordination guardrail:** HF-1's README must reflect HF-2's expanded schema and HF-3's error message. To avoid conflict:
- HF-2 lands first (smallest blast radius + schema change is structural).
- HF-3 lands second (touches a separate file + a small README excerpt).
- HF-1 lands last (docs reflect HF-2 + HF-3 changes).
- Coordinator sequences merges in this order even though engineers may finish out-of-order.

Alternative if simpler: serialize all three in one execution step (1 → 2 → 3) — slower wall-clock, zero coordination risk. **Recommended: sequence merges, not engineers.** Engineers run in parallel; the coordinator merges in dependency order.

---

## Quality checks (PO sign-off before handoff)

- [x] Each issue has a documented resolution.
- [x] Each resolution names the affected SRS / FRS / NFR sections (only Issue 3 amends an FR; the rest are docs/UX-only).
- [x] Hotfix scope is bounded — no scope creep ("all docs" narrowed to: README, class+sequence diagrams, OpenAPI, examples.http; future-work docs deferred to S-036).
- [x] Byte-identity invariant (NFR-PT-05) explicitly preserved across all hotfixes; S-018 paired UAT is the gate.
- [x] Sprint 7 stays READY-FOR-REVIEW until hotfix sprint completes — no premature DONE.
- [x] User's "everything in a preset" wording explicitly bounded to 3 fields with rationale for inclusion/exclusion per field.
- [x] User-decided non-deliverable preserved: NFR-OP-07 (no migration tooling) still holds; HF-1 docs reflect current state without claiming legacy compat.