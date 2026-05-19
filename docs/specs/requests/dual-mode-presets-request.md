# llm-tts-api — Dual-Mode Audio Presets Improvement Request

**Date:** 2026-05-19
**Reference quality bar:** `/Volumes/Coding/Projects/Applications/epub/llm-image-api/config/presets.json`
**Target codebase:** `/Volumes/Coding/Projects/Applications/epub/llm-tts-api`
**Cycle:** Cycle 2 — Dual-mode audio generation (presets)
**Mode:** Feature addition. Behavior-additive on top of the just-finished Sprints-1-6 baseline. **Not a rewrite.**
**Status:** Scoped (Product Owner Phase 1 complete) — ready for business-analyst handoff.

---

## 1. Background

llm-tts-api is now a complete, tested, OpenAI-compatible TTS service after Sprints 1-6 (in this repo's first improvement cycle): rich endpoint `POST /v1/tts/synthesize` with streaming + trailers + cancellation, OpenAI-compatible `POST /v1/audio/speech` as a thin translator over the rich endpoint, voice CRUD + storage, provider auto-selection, model cache, error envelope, full docs + container + perf baseline.

What it does NOT yet have: a clean way for callers to **pick an audio-generation mode**. Today, every request goes through one synthesis pipeline with one set of default knobs (sampling, chunking, post-processing, output format). Callers who want low-TTFB streaming for an interactive agent get the same defaults as callers who want a polished audiobook chapter. The fine-grained fields exist (`temperature`, `top_p`, `max_sentences_per_chunk`, `normalize_db`) but require the caller to know what knobs to twist.

The sibling project llm-image-api ships `config/presets.json` with named presets (`quality` / `fast`) — each carrying mode-specific defaults (steps, cfg, sampler, scheduler, dimensions, LoRAs, upscale). The same pattern shape applies cleanly to TTS, where the trade-off axes are TTFB / chunk cadence vs sampling fidelity / model size / post-processing / output format.

## 2. Goals

### G1 — Ship named audio-generation presets (PRIMARY)
Three named presets out of the box, defined in a new `config/presets.json` (hot-reloadable like `voice_map.json`):

- **fast** — optimized for low TTFB and steady chunk cadence; smaller/faster model variant where available; lower sampling cost; streaming-first.
- **balanced** — sensible middle ground; the server-side default (configurable via env).
- **quality** — best per-provider model + max sampling fidelity; longer chunk windows; post-processing pass (RMS normalize, silence trim, optional denoise); higher-quality output format (FLAC / 24-bit WAV); buffered-only (streaming silently downgrades).

### G2 — Preset selection on `/v1/tts/synthesize`
- `preset` field on `SynthesizeRequest` (Pydantic, `Literal["fast", "balanced", "quality"]` initially, but presets.json may add operator-defined entries).
- Preset sets defaults; explicit per-request fields override (precedence: explicit field > preset default > settings default).
- Default when `preset` is absent = server default (env: `TTS_DEFAULT_PRESET`, defaults to `balanced`).

### G3 — OpenAI-compat path uses server default
- `POST /v1/audio/speech` always applies the server default preset; no escape hatch in the request body.
- Preserves S-018 byte-identity contract: OpenAI request shape stays byte-identical to upstream OpenAI; operators tune via env.

### G4 — Post-processing service-layer module
- New `services/audio_postprocess.py`: RMS normalize + silence trim (both stdlib/numpy/scipy or similar light deps). Optional denoise behind a feature flag or deferred to a later cycle (decision flagged in OQs below).
- Pipeline insertion point: AFTER provider chunks are assembled, BEFORE response encoding. Skipped by `fast` and `balanced` presets unless explicitly enabled.

### G5 — Extend response_format
- `response_format` field gains `wav24` (24-bit WAV) and `flac` values in addition to existing `wav` (16-bit).
- Provider capability matrix updated: not every provider can emit all formats. Where a provider can't, a clear `validation_error` is returned.
- `quality` preset defaults to `flac` (or `wav24` — final default flagged as OQ below); `fast` and `balanced` stay at `wav`.

### G6 — Quality preset is buffered-only
- If `preset="quality"` and `stream=true` on the same request: server silently buffers (no stream), runs the full post-processing pass, and returns the result as a normal response. Response headers reflect the actual response shape (no streaming trailers).
- Documented behavior; not an error.

## 3. Non-goals (explicit OUT-of-scope)

- **More than three built-in presets.** Operators can add custom presets via `config/presets.json`, but only `fast` / `balanced` / `quality` are shipped.
- **Per-request preset override on the OpenAI path.** OpenAI request body stays byte-identical. No `preset` field added to `SpeechRequest`; no query-string escape hatch.
- **Removing the existing fine-grained fields** (`temperature`, `top_p`, `max_sentences_per_chunk`, `normalize_db`). They stay as overrides on the preset defaults.
- **Cross-provider model auto-distillation.** "Fast preset uses a faster model" means the preset config NAMES the smaller model where available — not that the server distills/quantizes a new variant at runtime.
- **Mid-stream preset switching.** Preset is per-request, decided at request start.

## 4. Scoped decisions (from PO Phase 1 dialog)

The following were resolved during the PO scoping dialog and are inputs to the business analyst (not open questions):

| # | Decision | Value |
|---|---|---|
| D1 | Preset count | 3 built-in: `fast` / `balanced` / `quality` |
| D2 | Server default preset | `balanced` (overridable via `TTS_DEFAULT_PRESET`) |
| D3 | OpenAI-compat preset selection | Always server default; no body/query escape hatch |
| D4 | Preset vs explicit fields | Preset sets defaults; explicit fields override |
| D5 | Per-preset model selection | YES — preset can pin `(provider, model)`; otherwise auto-select |
| D6 | Post-processing location | New `services/audio_postprocess.py` module (in-cycle) |
| D7 | Output format extension | `wav` + `wav24` + `flac` |
| D8 | Quality + streaming | Silently downgrades to buffered |
| D9 | Preset storage | `config/presets.json` (hot-reloadable, mirrors llm-image-api pattern) |
| D10 | Reference quality bar | `/Volumes/Coding/Projects/Applications/epub/llm-image-api/config/presets.json` |

## 5. Open questions for downstream phases

These were surfaced during scoping but deferred for the business analyst / writer to drive to closure:

- **OQ-1 — Denoise in cycle or deferred?** Adding denoise (e.g. `rnnoise`, `noisereduce`) adds a non-trivial dependency. Options: (a) feature-flag it behind an extra (`pip install .[denoise]`); (b) ship the postproc module without denoise this cycle, leave the interface ready; (c) skip entirely.
- **OQ-2 — Quality preset default response_format**: `flac` (compressed lossless) or `wav24` (uncompressed 24-bit)? Affects bandwidth and decoder compatibility downstream.
- **OQ-3 — Per-provider capability declaration for formats**: should each provider expose a `supported_response_formats` set (analogous to S-006's `supports_devices`)? Or is format conversion a service-layer post-step independent of provider?
- **OQ-4 — Preset hot-reload semantics**: same as `voice_map.json` (`watchfiles` + polling fallback)? What happens to in-flight requests when a preset changes — frozen at request-start, or always read live?
- **OQ-5 — `config/presets.json` schema validation**: should it be JSON Schema, Pydantic, or freeform with runtime validation? Affects operator UX when editing the file.
- **OQ-6 — Per-preset perf budgets (NFR)**: should we declare a hard SLO per preset (e.g. fast TTFB p95 < 400 ms, quality wall-clock p95 < 3× fast)? Or only soft documentation? NFR writer's call.
- **OQ-7 — Custom operator presets**: if an operator adds `cinematic` to `presets.json`, does the OpenAPI spec / `/v1/models` enumerate it? Does the inventory test pin it? Lifecycle decisions.
- **OQ-8 — Backward compatibility**: existing callers of `/v1/tts/synthesize` who pass `temperature=...` etc. but no `preset`: do they continue to work unchanged? (Expected answer: yes — preset is optional, absent preset = server default, explicit fields still override. Confirmation needed.)
- **OQ-9 — Voxtral / vLLM-Omni preset support**: not every provider exposes the same knobs. How does a preset with knobs the active provider doesn't support behave? (Soft-ignore, reject, warn?)
- **OQ-10 — Should `voice_args.py` resolve preset → effective config, or should `synthesize_service.py`?** Layering decision for the BA / writer.

## 6. Risks

- **RISK-1 — Format-extension scope creep**: adding `wav24` and `flac` touches every provider. Each provider's `synthesize_chunks` may need a format-arg plumbing change. Risk: per-provider PR drag.
- **RISK-2 — Post-processing perf cost**: RMS normalize + silence trim on a 60s chunk should be sub-100ms but needs measurement. May push quality preset over an acceptable wall-clock if not tuned.
- **RISK-3 — Streaming downgrade surprise**: callers requesting `stream=true` + `preset=quality` get a buffered response. Documented, but a power user might miss it. Mitigation: emit a response header (`X-Stream-Downgraded: quality-postproc`) so clients can detect.
- **RISK-4 — Preset-vs-override precedence ambiguity**: if a preset pins `provider="voxtral"` and the request says `provider="mlx_audio"`, the request wins. Risk: caller surprised that "preset" didn't fully constrain. Mitigation: clear docs + a `X-Preset-Effective` header listing the resolved effective config.
- **RISK-5 — Hot-reload races**: preset reload mid-request. Same mitigation as voice_map.json (snapshot at request start).
- **RISK-6 — OpenAI compat path locked to server default**: power users who use OpenAI SDK + want quality can't get it. Acceptable per D3 (operators tune via env), but a known limitation.

## 7. Assumptions

- **A1** — The current `SynthesizeRequest` schema is extensible (Pydantic, `extra="forbid"`). Adding `preset` is an additive change; existing callers unaffected per D4.
- **A2** — `services/synthesize_service.py::synthesize_core` is the single insertion point for preset resolution. Both routers (rich + OpenAI adapter) call this; preset logic lives here.
- **A3** — The hot-reload machinery from S-011 (`watchfiles` + polling fallback) generalizes — presets.json reload uses the same primitive, not a copy-paste.
- **A4** — S-018 byte-identity paired UAT remains the load-bearing parity gate. Any preset change that affects bytes on the OpenAI path must be guarded so that path-with-default-preset still produces the same bytes as the rich-path-with-default-preset would.

## 8. Reference quality bar

- `/Volumes/Coding/Projects/Applications/epub/llm-image-api/config/presets.json` — the explicit shape reference. Mirror per-preset `label` + `description` + `defaults` block layout; add TTS-specific blocks (`postprocess`, `model`).
- `/Volumes/Coding/Projects/Applications/epub/llm-image-api/docs/specs/SPECIFICATION.md` — for how llm-image-api documents presets-as-API-surface (if applicable).
- This repo's `docs/specs/software-spec.md` § the rich-endpoint section — to be amended in the cycle's eventual SRS rewrite.

## 9. Acceptance shape for the eventual cycle

Day-end SRS for this cycle should let an integrator answer:

- "How do I get fast streaming TTS for a voice agent?" → `preset: "fast"` (or rely on `TTS_DEFAULT_PRESET=fast` deployment).
- "How do I batch-generate audiobook chapters?" → `preset: "quality"`, `response_format: "flac"`.
- "How do I keep my OpenAI SDK code working?" → no changes; gets the server default preset; latency / quality identical to pre-cycle defaults when operator chooses `TTS_DEFAULT_PRESET=balanced` to match.
- "How do I add my own preset?" → drop it into `config/presets.json`, hot-reload picks it up.

---

**Status:** Ready for `business-analyst` skill (Phase 2a of PO workflow). The BA will run its challenge rounds against this scoped request, produce `analyst-frs-cycle-2.md` and `analyst-UAT-cycle-2.md` (or extend the cycle-1 docs in place — file-naming decision deferred to BA).
