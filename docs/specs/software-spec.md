# llm-tts-api — Software Requirements Specification

**Status:** Draft
**Date:** 2026-05-17 (cycle 1) · **2026-05-19 (cycle 2 extension)**
**Cycle 1:** Engineering parity with llm-image-api + selected feature work; **not a rewrite**. (CLOSED — Sprints 1-6, 25/25 stories DONE.)
**Cycle 2:** Dual-mode audio generation (named presets: `fast`/`balanced`/`quality` + post-processing pipeline + response_format extension). (ANALYSIS DONE — sprints pending.)
**Reference codebase (quality bar):** `/Volumes/Coding/Projects/Applications/epub/llm-image-api`
**Cycle-2 reference shape:** `/Volumes/Coding/Projects/Applications/epub/llm-image-api/config/presets.json`

**Source documents** (this SRS supersedes for high-level navigation; source docs remain authoritative for detail):
- Cycle-1 request: `docs/specs/requests/improvement-request.md`
- Cycle-2 request: `docs/specs/requests/dual-mode-presets-request.md`
- Functional: `docs/specs/analyst-frs.md` (cycle-1 §1-4.13 + cycle-2 §4.14-4.16)
- Acceptance tests: `docs/specs/analyst-UAT.md` (cycle-1 UAT-* + cycle-2 UAT-PR/PP/FMT)
- Non-functional: `docs/specs/writer-nfr.md` (cycle-1 §1-§17 + cycle-2 §11b)
- Forward-looking analysis: `docs/specs/improvement-analysis.md`
- Cycle-1 closure: `docs/planning/sprint-log.md` (Sprints 1-6 DONE)

---

## 1. Purpose & Scope

`llm-tts-api` is an internal FastAPI TTS service with a pluggable provider registry (MLX-audio, Voxtral, vLLM-Omni), voice cloning via reference audio, semantic chunking, per-chunk RMS normalization, and an OpenAI-compatible endpoint. It is well-architected but trails its sibling project `llm-image-api` on several engineering axes (hardware auto-detection, async-correct concurrency, lifespan-managed singletons, structured observability, CI quality gates).

This cycle closes that gap **incrementally**, preserves the TTS-specific strengths intact, introduces a new richer endpoint surface with the OpenAI endpoint as a thin translator, and captures a roadmap for the currently-stubbed (501) endpoints and other future improvements.

**In scope:** hardware auto-detection (device + provider), rich endpoint `/v1/tts/synthesize`, OpenAI-adapter refactor, **voice CRUD with pluggable storage backends (filesystem-JSON default; Postgres + S3 implemented as optional)**, JSON-seed ingestion on startup, async-correct concurrency model, LRU model cache, lifespan-managed singletons, lock-free health/ready split, structured logging with request IDs, typed error taxonomy, strict mypy + ruff + 80% coverage CI gate, updated Dockerfile (CPU/MPS + CUDA variant) and docs.

**Out of scope (captured in Roadmap, §11):** STT endpoints, chat endpoints, realtime WebSocket, Prometheus `/metrics`, audio caching, rate limiting, SSML, MP3/Opus encoders, parallel chunk synthesis, multi-replica deploy, formal signed-consent records, auth/authz.

**Note on Voice CRUD inclusion (was Roadmap):** Following decision OQ-3, voice enrollment is promoted into this cycle. The original inline-ref_audio path on the synthesis endpoint is **removed** in favor of a CRUD-managed voice store; the synthesis endpoint accepts only a `voice` id.

## 2. System Quality Profile

| Attribute | Profile |
|---|---|
| System type | API/service (FastAPI), single-process, ML-backed (TTS inference) |
| Deployment context | Internal LAN / trusted network only |
| Primary host | Apple Silicon, ≥ 32 GB unified memory |
| Container target | Linux x86_64 (CUDA optional) via Dockerfile |
| Expected load | Single-user / few internal callers; ≤ 4 concurrent typical |
| Availability target | Best-effort; container restart recovers; no SLA |
| Data sensitivity | Synthesis text: low/medium. Voice records (audio + transcript): high (voice biometric) |
| Compatibility | OpenAI Python SDK (including `with_streaming_response`) |

## 3. Actors

| Actor | Role |
|---|---|
| API Client (OpenAI-shaped) | Existing clients using `/v1/audio/speech` and OpenAI SDK. Must keep working unchanged. |
| API Client (rich) | New consumers using `/v1/tts/synthesize` for full capability (per-request normalization, chunking knobs, language overrides) and `/v1/tts/voices/*` for voice management. |
| Operator | Runs the service; sets env vars; mounts voice map and ref audio; reads logs. |
| Voice Curator | Manages `voice_map.json` (often same as Operator). |
| Developer | Subject to CI quality gates (ruff, mypy, pytest+coverage, pip-audit). |

## 4. Requirements by Functional Area

Each area lists the source FRs, NFRs, and UAT IDs. **For detailed wording, see source documents.**

### 4.1 Hardware & Provider Auto-Detection
- **FR:** FR-HW-01..07
- **NFR:** NFR-OP-01 (fail-fast on no viable provider)
- **UAT:** UAT-HW-01..06
- **Summary:** Startup detects device (MPS → CUDA → CPU) and selects provider from device capability. Env vars `TTS_DEVICE`, `TTS_DTYPE`, `TTS_PROVIDER` are overrides (not defaults). CPU with no viable provider is a hard startup error. Provider declares `supports_devices`.

### 4.2 Rich Endpoint `/v1/tts/synthesize`
- **FR:** FR-EP-01..05
- **NFR:** NFR-PF-01..03, NFR-OB-03 (response headers), NFR-MT-04 (Pydantic `extra="forbid"`)
- **UAT:** UAT-EP-01..07
- **Summary:** New rich endpoint exposes the full capability surface (text, required `voice` id resolved from the voice store, provider/model overrides, normalization & chunking knobs, language overrides, streaming flag). Response is raw audio bytes with metadata in `X-*` headers — no per-chunk JSON frames.

### 4.3 OpenAI Adapter `/v1/audio/speech`
- **FR:** FR-OA-01..04
- **NFR:** NFR-PT-03 (SDK compat), **NFR-PT-03b** (audio equivalence — see §5 resolution C-G-1)
- **UAT:** UAT-OA-01..04
- **Summary:** OpenAI endpoint remains shape-compatible and is reimplemented as a thin translator over `/v1/tts/synthesize`. No duplicated business logic. SDK streaming (`with_streaming_response`) must work end-to-end.

### 4.4 Voice CRUD & Pluggable Storage
- **FR:** FR-VS-01..12 (replaces former FR-VC-02..05 inline path)
- **NFR:** NFR-SE-01..03 (size cap, content-type allow-list — now applied at CRUD upload), NFR-PV-01..05, NFR-CP-01, NFR-ST-01..04 (storage backends)
- **UAT:** UAT-VS-01..12 (replaces former UAT-VC-01..05 inline)
- **Summary:** Voices are managed via REST CRUD under `/v1/tts/voices/*` (rich namespace). `/v1/audio/voices` remains reserved 501 in this cycle as the future OpenAI-compatibility adapter — same pattern as `/v1/audio/speech` over `/v1/tts/synthesize`. Audio bytes are exposed via a dedicated `GET /v1/tts/voices/{id}/audio` endpoint (no `?include_audio=` query knob). The metadata repository and audio-blob repository are abstracted behind `VoiceMetadataRepository` and `VoiceBlobRepository` protocols. **Implemented backends in this cycle:** `FsJsonMetadataRepository` + `FsBlobRepository` (default; `pip install .`); `PostgresMetadataRepository` (opt-in `pip install .[postgres]`); `S3BlobRepository` (opt-in `pip install .[s3]`). Backend selection is via `TTS_VOICE_METADATA_BACKEND` and `TTS_VOICE_BLOB_BACKEND` env vars. Audio uploads validated per NFR-SE-01..03 (size, content-type, magic bytes). At create time, a `consent_acknowledged: bool` MUST be true; the value is stored with the record. Formal signed-consent remains Roadmap.

### 4.5 Voice Seed Ingestion (legacy JSON)
- **FR:** FR-VM-01..05 (re-scoped: now an ingestion mechanism, not the runtime source)
- **NFR:** NFR-OP-05 (ingestion latency budget ≤ 2 s on file change)
- **UAT:** UAT-VM-01..05 (re-scoped)
- **Summary:** `voice_map.json` (path via `TTS_VOICE_MAP_FILE`) is read at every startup AND on file change (watchfiles). Each entry is upserted into the voice store **only if not already present** (idempotent by `id`). Existing voices in the store are left untouched. If the JSON is invalid, ingestion is aborted; the previous store state is preserved; an error is logged. `GET /v1/tts/voices` (in §4.4) lists active voices regardless of origin (seeded vs CRUD-created).

### 4.6 Concurrency, Queueing & Cancellation
- **FR:** FR-CC-01..05
- **NFR:** NFR-PF-02 (event-loop responsiveness), NFR-PF-04 (throughput), NFR-SC-01..03, NFR-RL-03 (no cascading failure)
- **UAT:** UAT-CC-01..04
- **Summary:** Replace blocking `threading.Semaphore` with `asyncio.Semaphore`; dispatch sync provider calls via `anyio.to_thread`; per-(provider, model) `asyncio.Lock` for serialization; queue admission semaphore at `TTS_MAX_QUEUE_DEPTH`; client disconnect stops further chunks at the next boundary.

### 4.7 Model Cache & Lifecycle
- **FR:** FR-CA-01..04
- **NFR:** NFR-SC-04 (predictable memory footprint)
- **UAT:** UAT-CA-01..03
- **Summary:** LRU keyed by `(provider, model_id)`, configurable size, **default 1**. Validate model_id and file deps **before** evicting current entry. Preload models honored at startup.

### 4.8 Health, Readiness & Lifecycle
- **FR:** FR-HL-01..05
- **NFR:** NFR-RL-01 (drain), NFR-RL-05 (ready accuracy), NFR-OP-01 (fail-fast)
- **UAT:** UAT-HL-01..05
- **Summary:** Lock-free `/health` always 200; `/ready` 503 during warmup/drain/voice-map-invalid; lifespan-managed singletons via `app.state`; graceful SIGTERM drain; soft memory-floor warning.

### 4.9 Observability
- **FR:** FR-OB-01..04
- **NFR:** NFR-OB-01..04, NFR-PV-02..03 (log redaction)
- **UAT:** UAT-OB-01..04
- **Summary:** `X-Request-ID` end-to-end; structured logging (human or JSON via `APP_LOG_FORMAT`); response headers FR-EP-04 on success, `X-Error-Code` on failure; INFO-level logs payload-free; DEBUG may include ≤ 80-char text snippets, never audio bytes.

### 4.10 Configuration
- **FR:** FR-CF-01..03
- **NFR:** NFR-OP-03 (env-only)
- **UAT:** UAT-CF-01..04
- **Summary:** Env-only configuration; new vars `TTS_*` / `APP_*`-prefixed and README-documented. `TTS_INFERENCE_TIMEOUT_SECONDS` **default unset (disabled)**; positive value enables `asyncio.wait_for` enforcement.

### 4.11 Error Model
- **FR:** FR-ER-01..04
- **NFR:** NFR-SE-04 (no payload echo)
- **UAT:** UAT-ER-01..02, UAT-OB-04
- **Summary:** OpenAI-compatible envelope `{ error: { type, code, message, param?, request_id } }` with broad type categories (`validation_error`, `voice_error`, `provider_error`, `capacity_error`, `internal_error`) and specific sub-codes. No payloads, paths, or tracebacks leak to clients.

### 4.12 Quality Gates
- **FR:** FR-QG-01..04
- **NFR:** NFR-MT-01..05, NFR-SE-05..06, NFR-PT-01..02
- **UAT:** UAT-QG-01..05
- **Summary:** CI runs ruff + ruff format + `mypy --strict` + `pytest --cov-fail-under=80` + `pip-audit` + `docker build`. Strict typing across `src/`; `py.typed` shipped; provider interfaces are `Protocol`-typed; PRs scoped per FR area.

### 4.13 Documentation
- **FR:** FR-DC-01..03
- **NFR:** NFR-MT-06 (diagram freshness), NFR-CP-01 (biometric notice)
- **UAT:** UAT-DC-01..03
- **Summary:** README updated with auto-detection rules, env-var inventory, rich endpoint examples, voice-CRUD endpoints (incl. consent attestation), seed-ingestion mechanism, storage-backend selection (defaults vs `[postgres]`/`[s3]` extras), error taxonomy, biometric notice. Diagrams refreshed. OpenAPI covers `/v1/tts/synthesize`, `/v1/tts/voices/*`, and `/v1/audio/speech`.

### 4.14 Audio-Generation Presets *(cycle 2)*
- **FR:** FR-PR-01..13
- **NFR:** NFR-PR-01..04, NFR-SE-09 (presets.json file permissions), NFR-SE-10 (validating reload), NFR-OP-06 (per-synthesis log), NFR-PT-05 (S-018 byte-identity invariant), NFR-OP-07 (no migration deliverable)
- **UAT:** UAT-PR-01..17
- **Summary:** Three built-in named presets (`fast`, `balanced`, `quality`) defined in `config/presets.json`, hot-reloadable like `voice_map.json`. `SynthesizeRequest` accepts `preset: str`; resolution lives in `services/synthesize_service.py` producing a frozen `EffectiveSynthesisConfig`. Resolution precedence: explicit request field > preset defaults > Settings/VoiceRecord. Server default is `balanced`, overridable via `TTS_DEFAULT_PRESET`. Each preset MAY pin `(provider, model)`; explicit request fields win with WARN log + `X-Preset-Effective` header. Provider knobs the active provider can't honor are soft-ignored + reported via `X-Preset-Ignored-Knobs`. Custom operator presets work but are NOT enumerated in `/v1/models` or OpenAPI. OpenAI-compat path always uses the server default — no escape hatch. `presets.json` startup checks: schema validation (Pydantic, `config_error.presets_invalid`), preset-pinned `(provider, model)` allow-list match (`config_error.preset_provider_invalid`), file permissions (`config_error.presets_unsafe_permissions`). Hot-reload validates BEFORE swap — bad file ⇒ reload skipped, service keeps running on prior good config. S-018 paired UAT is the load-bearing portability gate; the rich-with-balanced ↔ OpenAI byte-identity invariant survives cycle 2 unchanged.

### 4.15 Audio Post-Processing *(cycle 2)*
- **FR:** FR-PP-01..08
- **NFR:** NFR-PP-01..03, NFR-CP-03 (denoise inherits biometric notice)
- **UAT:** UAT-PP-01..07
- **Summary:** New service-layer module `services/audio_postprocess.py` exposes a pure-function pipeline `postprocess_audio(...)` running on a fully-assembled WAV body. Pipeline order is deterministic: **denoise → silence_trim → rms_normalize** (rationale: denoise removes noise that would inflate trim thresholds; trim removes leading/trailing silence; normalize sets final loudness). Each step is a no-op when its flag is false. `denoise` is feature-flagged via optional dependency extra `[denoise]` (matching `[postgres]`/`[s3]` pattern from Sprint 3) — when absent, `denoise=true` logs WARN and no-ops (NOT a request error). When any step runs, response header `X-Postprocess-Applied` lists the applied steps (absent when none). Quality preset + `stream=true` silently downgrades to buffered + applies the full pipeline, with `X-Stream-Downgraded: quality-postproc` header on the response. Post-processing buffer is request-scoped (no logging of audio bytes, no module-level retention).

### 4.16 Response Format Extension *(cycle 2)*
- **FR:** FR-FMT-01..07
- **NFR:** NFR-FMT-01..03, NFR-PT-06 (provider capability declaration)
- **UAT:** UAT-FMT-01..06
- **Summary:** `SynthesizeRequest.response_format` extended from `Literal["wav"]` to `Literal["wav", "wav24", "flac"]`. `wav` = 16-bit PCM (default for `fast`/`balanced`); `wav24` = 24-bit PCM; `flac` = lossless compressed (default for `quality`). Each provider declares a `supported_response_formats` capability (mypy-strict-required) consulted at startup (preset+capability mismatch ⇒ `config_error.preset_provider_invalid`) and at request-time (explicit unsupported format ⇒ `400 validation_error.format_unsupported` listing the supported set). Format conversion runs in the service-layer via `soundfile` (existing dep) AFTER post-processing AND AFTER provider chunk assembly — the provider's native WAV16 output is the canonical intermediate. Response `Content-Type` matches the resolved format: `audio/wav` for `wav`/`wav24`, `audio/flac` for `flac`. Supported encoder portability: Linux x86_64 + macOS arm64; CI exercises all three formats on both.

## 5. Conflict Resolutions

(Identified during Phase 3 cross-reference.)

### Resolution C-1 — Concurrency default
**Conflict:** FR-CC-01 sets `TTS_MAX_CONCURRENT_REQUESTS` default `1`; NFR-PF-04 references `2` as the reference-host setting.
**Resolution:** Code default remains `1` (safe, predictable on small hosts). The reference deployment (≥32 GB Apple Silicon) is documented as recommending `2`. README MUST contain a "Sizing recommendations" subsection that names the recommended value per host class.

### Resolution C-2 — Response header inventory
**Conflict:** FR-VC-05 introduces `X-Voice-Source` / `X-Voice-Id` headers not enumerated in FR-EP-04.
**Resolution:** The canonical response-header inventory is **here in the SRS**, superseding both FRs for the list:

| Header | Set on | Meaning |
|---|---|---|
| `X-Request-ID` | always (success & error) | request correlation id |
| `X-Provider` | success | provider used |
| `X-Model` | success | model id used |
| `X-Device` | success | inference device (mps/cuda/cpu) |
| `X-Dtype` | success | inference dtype |
| `X-Voice-Source` | success | `map` or `inline` |
| `X-Voice-Id` | success, map only | id from voice map |
| `X-Chunks` | success (non-streamed) or trailer (streamed, if supported) | number of chunks synthesized |
| `X-Total-Duration-Ms` | success (non-streamed) or trailer (streamed, if supported) | total audio duration |
| `X-Error-Code` | error | error sub-code (matches envelope `error.code`) |

### Resolution G-1 — OpenAI-adapter audio equivalence (NFR-PT-03b)
**Gap:** FR-OA-02 mandates "thin translator, no duplicated business logic" but nothing measurable enforces equivalence.
**Resolution:** Add **NFR-PT-03b**: for a request through `/v1/audio/speech` and an equivalent request through `/v1/tts/synthesize` (same model, same input, same voice, same temperature/top_p/seed where applicable, no per-request overrides absent from the OpenAI shape), the audio output bytes MUST be byte-identical when both run on the same warm model. Verified by a paired UAT: synthesize via OA path, synthesize via rich path, `hashlib.sha256` compare.
**New UAT:** **UAT-OA-05 (Happy)** — paired byte-identical synthesis.
**RISK-8 fallback contract:** if a provider proves non-deterministic in CI, the paired UAT relaxes to `±1 PCM sample on audio length + perceptual hash within Hamming distance threshold`. The thresholds, rationale, and escalation policy are pinned in [`docs/perf/baseline.md` § "RISK-8 byte-identity relaxation"](../perf/baseline.md#risk-8-byte-identity-relaxation-nfr-pt-03b--srs-5-g-1) and the code path is covered by `tests/test_openai_adapter_parity.py::test_paired_byte_identity_relaxed_under_risk8`.

### Resolution G-2 — Voice map reload latency
**Gap:** Latency budget for hot-reload was informal.
**Resolution:** Formalize NFR-OP-05 — hot-reload of a valid `voice_map.json` MUST take effect within **2 seconds** of file write, measured from `mtime` to first request seeing the new map. UAT-VM-03 already covers this; SRS pins the budget.

### Resolution G-3 — Streaming trailer fallback
**Gap:** What happens if the HTTP client doesn't support trailing headers?
**Resolution:** Codify in this SRS: when streaming, the service emits `X-Chunks` and `X-Total-Duration-Ms` as response **trailers** if and only if it can do so without breaking the byte stream. When trailers are not feasible (uvicorn limitation or client doesn't advertise `TE: trailers`), the service **omits** those two headers entirely — it MUST NOT emit synthesized/fake values, and it MUST NOT block the stream waiting for chunk-count finality. Clients consuming streams MUST NOT depend on those two headers being present.

### Resolution I-1 — Single Assumptions and Open Questions list
**Inconsistency:** FRS and NFR each had Assumptions and Open Questions; some overlapped.
**Resolution:** §9 and §10 of this SRS are the consolidated authoritative lists.

### Resolution I-2 — Single Roadmap section
**Inconsistency:** Roadmap appeared in both FRS §6 and NFR §14.
**Resolution:** §11 of this SRS is the consolidated Roadmap. The detailed scoring of feature candidates lives in the separate `docs/specs/improvement-analysis.md`.

### Cycle-2 Conflict Resolutions

> Identified during PO Phase 3 cross-reference of FRS §4.14-4.16, UAT-PR/PP/FMT, and NFR §11b.

### Resolution CY2-C-1 — Migration scope (A-PR-1 vs NFR-OP-07)
**Conflict:** FRS A-PR-1 said operators get a "migration note"; NFR-OP-07 (per user TW Round 3 decision) explicitly says no migration deliverable.
**Resolution:** A-PR-1 was rewritten to drop the migration-note phrase. The assumption is preserved: cycle-2's `balanced` preset is operator-tunable; cross-cycle byte-identity (cycle-1 → cycle-2) is NOT promised. Same-cycle horizontal byte-identity (rich-with-balanced ↔ OpenAI server-default) IS preserved as load-bearing (NFR-PT-05). Migration tooling, docs, or compat reference files are explicitly OUT of scope.

### Resolution CY2-G-1 — UAT coverage of NFR-SE-09 file-permission check
**Gap:** NFR-SE-09 (presets.json startup permission check) had no UAT.
**Resolution:** Added UAT-PR-14 (startup-fail when presets.json world-writable or owner-mismatched).

### Resolution CY2-G-2 — UAT coverage of NFR-SE-10 validating reload
**Gap:** Hot-reload happy-path was covered (UAT-PR-08); validating-before-swap attack-tolerance was not.
**Resolution:** Added UAT-PR-15 (runtime invalid `presets.json` write → reload skipped, service continues on prior good config).

### Resolution CY2-G-3 — UAT coverage of NFR-OP-06 observability log
**Gap:** Per-synthesis INFO log line content (`resolved_preset`, `ignored_knobs`, `postprocess_applied`, `response_format`, `stream_downgraded`) had no UAT.
**Resolution:** Added UAT-PR-16 (JSON-formatted log line carries the five required fields; payload-free).

### Resolution CY2-G-4 — Explicit reference to S-018 paired UAT
**Gap:** NFR-PT-05 codified S-018 as load-bearing but cycle-2 UAT section didn't enumerate the existing paired UAT.
**Resolution:** Added UAT-PR-17 (regression assertion: cycle-2 master leaves `tests/test_openai_adapter_parity.py` byte-identical to its cycle-1 form, and the paired sha256 invariant still passes).

## 6. Business Rules

Repeated here for self-containment (source: FRS §5).

| ID | Rule |
|---|---|
| BR-1 | Voice resolution: explicit `voice` id → voice map. Otherwise inline `ref_audio` required. Both → error. Neither → error. |
| BR-2 | Provider resolution: explicit `provider` field → env `TTS_PROVIDER` → auto from `DeviceProfile`. |
| BR-3 | Model resolution: explicit `model` field → provider `default_model`. Must be in `allowed_models`. |
| BR-4 | Streaming applies chunking + per-chunk normalization same as non-streamed; only the flush boundary differs. |
| BR-5 | Hot-reload never invalidates voice ids referenced by in-flight requests. |
| BR-6 | Auto-detection picks device first, then provider from device capability. |
| BR-7 | Incompatible env override (e.g. `TTS_PROVIDER=vllm_omni` on Apple Silicon-only host) is a **startup** error. |
| BR-8 | Synthesis-time temp files derived from a voice blob are deleted in `finally`, regardless of exception class. Voice records themselves persist in the store until explicit DELETE. |
| BR-9 | OpenAI adapter calls only the public rich-endpoint service surface — no private internals. |
| BR-10 *(cycle 2)* | Preset resolution precedence: **explicit request field > preset defaults > Settings / VoiceRecord defaults**. Resolved frozen `EffectiveSynthesisConfig` is the single source of truth for downstream synthesis. |
| BR-11 *(cycle 2)* | Hot-reload of `config/presets.json` never affects in-flight requests; preset snapshot taken at request-start. |
| BR-12 *(cycle 2)* | `POST /v1/audio/speech` ignores any `preset` field or `?preset=` query string; always resolves to `TTS_DEFAULT_PRESET`. Preserves S-018 byte-identity. |
| BR-13 *(cycle 2)* | `preset="quality"` + `stream=true` silently downgrades to buffered with `X-Stream-Downgraded: quality-postproc` response header. NOT an error. |
| BR-14 *(cycle 2)* | Post-processing pipeline order is **denoise → silence_trim → rms_normalize**. Each step is a no-op when its flag is false. |
| BR-15 *(cycle 2)* | Format conversion runs in the service layer AFTER post-processing AND AFTER provider chunk assembly. Provider's native WAV16 is the canonical intermediate. |
| BR-16 *(cycle 2)* | Preset-pinned `(provider, model, response_format)` mismatches with the auto-selected provider's capabilities are **startup-fail** errors, not runtime errors. |
| BR-17 *(cycle 2)* | Soft-ignored preset knobs (provider-incompatible) are reported via `X-Preset-Ignored-Knobs` response header. Service-layer-driven knobs (postprocess, format conversion) are NEVER soft-ignored. |

## 7. Cross-Cutting Trade-offs

| Trade-off | Decision | Rationale |
|---|---|---|
| Single-process vs. multi-replica | Single-process this cycle | LAN-only, ≤4 concurrent typical; multi-replica adds shared-state complexity without current value. |
| Auth absent vs. in scope | Absent this cycle | LAN-only; mitigated by size caps and closed CORS. Roadmap. |
| Inference timeout default | Default-disabled | TTS legitimately runs long for long passages. Operators with SLAs opt in. |
| Streaming format | Raw bytes + headers/trailers | OpenAI SDK compatibility. No multipart, no SSE. |
| Model cache default | Single slot (default 1) | Predictable memory on ≥32 GB hosts running Voxtral-class models. |
| Log payload exposure | DEBUG-only snippets | Diagnostic richness at cost of operator responsibility. INFO is payload-free. |
| Voice biometric enforcement | Documented, not enforced | Defer formal consent flow to Roadmap; document operator responsibility. |
| Streaming end-of-stream metadata | Best-effort trailers, omit on unsupported clients | Don't block the stream waiting for totals. |
| *(cycle 2)* Soft preset SLOs vs. CI-enforced | Soft documentation only | Hardware variability + CI flake risk; operator-driven via S-021 scripts. |
| *(cycle 2)* Quality preset buffered-only | Buffered + `X-Stream-Downgraded` header | Full post-processing requires assembled audio; surface the downgrade rather than degrade silently. |
| *(cycle 2)* Migration tooling | None shipped; operator-tunes-balanced | Cycle-2 is a feature addition, not a maintenance event; avoids permanent legacy-compat machinery. |
| *(cycle 2)* Custom presets in OpenAPI | Operator-private — NOT enumerated | Stable type surface for client SDKs; operators tune per-deploy without spec churn. |
| *(cycle 2)* presets.json file-permission check | Startup check + reject | Defense-in-depth against malicious-config substitution; small dev-host friction. |
| *(cycle 2)* Denoise default | Default-off when `[denoise]` extra absent | Honors operator's deploy choice; missing optional dep doesn't fail requests. |
| *(cycle 2)* Format conversion layer | Service-layer | One canonical conversion site; providers stay focused on synthesis. |

## 8. Risk Register

(Consolidated from FRS Assumptions and NFR Risk Register.)

| ID | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| RISK-1 | Providers can't be cleanly retrofitted with `supports_devices`; auto-selection becomes a hardcoded device→provider table. | Medium | Low | Spike provider-capability API early; document the fallback table as the FR-HW-07 implementation. |
| RISK-2 | Async refactor of `SpeechSynthesizer` is more invasive than the "no rewrite" constraint allows; residual blocking remains. | Medium | Medium | Stage refactor; benchmark NFR-PF-02 (event-loop responsiveness) before/after; accept some sync-wrap in `anyio.to_thread` if needed. |
| RISK-3 | `watchfiles` unreliable inside Docker on bind-mounted volumes; hot-reload silently broken. | Medium | Medium | Polling fallback path; UAT-VM-03 executed inside container in CI. |
| RISK-4 | Voice-CRUD uploads enable storage/DoS exhaustion via many medium-sized uploads. | Low (LAN) | Medium | NFR-SE-01 per-file caps + NFR-SC-02 concurrency ceiling. Per-client rate limiting and per-tenant storage quotas on Roadmap. |
| RISK-5 | Biometric documentation-only posture judged insufficient post-deploy. | Low | High | NFR-CP-01 README notice + optional `X-Voice-Consent-Acknowledged` header (NFR-SE-08). |
| RISK-6 | Coverage ratchet to 80% blocks merges if ratcheted aggressively. | Medium | Low | Per-FR-area PRs (NFR-MT-05) keep increments tractable. |
| RISK-7 | OpenAI SDK streaming behavior changes between SDK versions, breaking NFR-PT-03. | Low | Medium | Pin SDK version in test matrix; record in `docs/perf/baseline.md`. |
| RISK-8 | NFR-PT-03b byte-identity proves flaky due to non-determinism in models. | Medium | Low | If non-deterministic: relax NFR-PT-03b to "audio length within ±1 sample and perceptual hash within threshold"; document the relaxation. |
| RISK-PR-1 *(cycle 2)* | Reference-host TTFB target (fast p95 ≤ 250 ms, balanced ≤ 800 ms) not reachable day one on the default model; soft docs become misleading. | Medium | Low | Soft documentation only (no CI gate); operators measure on their own hardware. Fast preset may need a distilled model checkpoint. |
| RISK-PR-2 *(cycle 2)* | Per-provider `supported_response_formats` declared by inspection vs. measurement; clients see 400 `format_unsupported` for what would work. | Medium | Medium | Measure each provider via a smoke test before merging cycle 2; record matrix here in §4.16. |
| RISK-PR-3 *(cycle 2)* | presets.json hot-reload race: brief world-writable window during `mv` + `chmod`; NFR-SE-09 startup-check doesn't catch runtime races. | Low | Low | Documented trade-off: permission check is startup-only. NFR-SE-10 validating-before-swap mitigates malicious-config substitution. |
| RISK-PR-4 *(cycle 2)* | Postproc overhead exceeds 200 ms/s denoise budget on lower-spec hosts; quality preset wall-clock blows up. | Medium | Low | Soft budget; operator can disable denoise via preset or skip `[denoise]` extra. |
| RISK-PR-5 *(cycle 2)* | S-018 byte-identity breaks: preset resolution introduces divergent code path between rich and OpenAI under default config. | Medium | High — load-bearing portability contract | Resolution centralized in `synthesize_service.py::synthesize_core`; S-018 paired UAT (`tests/test_openai_adapter_parity.py`) runs on every PR; UAT-PR-17 explicitly asserts test-file byte-identity to detect tamper. |

## 9. Consolidated Assumptions

| ID | Assumption | Impact if wrong |
|---|---|---|
| A-1 | All providers can declare a `supports_devices` capability (or a clean device→provider table is acceptable). | RISK-1; FR-HW-04/05 logic uses table fallback. |
| A-2 | `SpeechSynthesizer` can be refactored async-correct in place without breaking provider strategies. | RISK-2; partial async with residual sync wrappers. |
| A-3 | `watchfiles` works reliably in Docker on bind-mounted config dir. | RISK-3; polling fallback needed. |
| A-4 | HTTP trailing headers are usable by typical clients of this API. | FR-EP-05 fallback per Resolution G-3. |
| A-5 | A ≥ 32 GB Apple Silicon host is the primary reference for sizing. | NFR-SC-04 numbers shift on smaller hosts. |
| A-6 | Internal LAN deployment remains the only operating context. | NFR-SE auth deferral untenable if scope shifts. |
| A-7 | Operators measure and record perf baseline in `docs/perf/baseline.md`. | NFR-PF-01 acceptance becomes qualitative. |
| A-8 | Container deploys mount voice map + ref audio as a volume. | NFR-OP-02 partial; rebuilds required for changes. |
| A-9 | TTS providers produce byte-identical output for identical inputs on a warm model (for NFR-PT-03b). | RISK-8; equivalence test relaxed to perceptual. |
| A-PR-1 *(cycle 2, revised per CY2-C-1)* | Cycle-2's `balanced` preset is operator-tunable; cross-cycle byte-identity is NOT promised. Same-cycle horizontal byte-identity (rich-with-balanced ↔ OpenAI server-default) IS preserved (NFR-PT-05). Operators wanting cycle-1 byte-compat are responsible for setting `balanced.defaults` to match cycle-1 defaults; no migration artifact is shipped. | If wrong (operators expect implicit cross-cycle byte-compat): UAT-PR-06 still verifies same-cycle horizontal byte-identity; cross-cycle observability is operator-driven via S-021 scripts. |
| A-PR-2 *(cycle 2)* | `soundfile` supports writing 24-bit WAV (`subtype="PCM_24"`) and FLAC end-to-end on Linux x86_64 + macOS arm64 with stock libsndfile. | NFR-FMT-02 fallback to a different encoder; potential added dependency. |
| A-PR-3 *(cycle 2)* | Each provider's `synthesize_chunks` signature can be inspected to determine accepted knobs without invasive refactor; soft-ignore matrix is achievable via reflection. | FR-PR-09 would need providers to declare a `declared_knobs` set explicitly. |
| A-PR-4 *(cycle 2)* | The `watchfiles` watcher primitive from S-011 generalizes to `presets.json` without rewriting the watcher. | FR-PR-11 would need a separate watcher implementation. |
| A-PR-5 *(cycle 2)* | Reference Apple Silicon (M1 Max) host achieves p95 TTFB ≤ 250 ms on `fast` preset with mlx_audio. | NFR-PR-01 documented target softens; `fast` preset may require a distilled model checkpoint. Operator-measurable via S-021. |

## 10. Consolidated Open Questions

| ID | Question | Decision needed by | Impact |
|---|---|---|---|
| OQ-1 | 80% coverage from day one vs. ratchet from current level? | Start of FR-QG implementation | CI config timing |
| OQ-2 | Voice list (`GET /v1/voices`) pagination / tags? | Before FR-VM-05 implementation | Endpoint schema |
| ~~OQ-3~~ | **RESOLVED:** voice CRUD with pluggable backends; multipart-only on create/update. See §4.4 FR-VS. | — | — |
| OQ-4 | OpenAI SDK version pinned for compat tests? | Before NFR-PT-03 UAT setup | Test fixture stability |
| OQ-5 | Docker image: CPU/MPS only, or also CUDA variant? | Before FR-QG-04 / NFR-OP-02 | Image build strategy and CI matrix |
| OQ-6 | License audit (`pip-licenses`) — required output or permitted? | End of cycle | NFR-CP-02 scope |
| OQ-7 | Perf baseline file location and ownership? | Start of FR area work | NFR-PF-01 maintainability |

**Cycle-2 OQs are ALL RESOLVED.** Trail of resolutions:

| ID | Resolution | Source |
|---|---|---|
| ~~CY2-OQ-1~~ | Denoise feature-flagged via `[denoise]` extra. | FR-PP-05; BA Round 1 |
| ~~CY2-OQ-2~~ | Quality preset's default `response_format` is `flac`. | FR-FMT-05; BA Round 1 |
| ~~CY2-OQ-3~~ | Per-provider `supported_response_formats: set` capability declaration. | FR-FMT-02 / NFR-PT-06; BA Round 1 |
| ~~CY2-OQ-4~~ | Hot-reload via `watchfiles` + polling fallback; in-flight snapshot at request-start. | FR-PR-11 / NFR-PR-04; BA Round 2 |
| ~~CY2-OQ-5~~ | `config/presets.json` validated via Pydantic `PresetConfig` model (`extra="forbid"`). | FR-PR-02; BA Round 2 |
| ~~CY2-OQ-6~~ | Per-preset SLOs are soft documentation only; no CI gate. | NFR-PR-01 / NFR-PP-01 / NFR-FMT-01; TW Round 1 |
| ~~CY2-OQ-7~~ | Custom operator presets usable; NOT enumerated in `/v1/models` or OpenAPI. | FR-PR-12; BA Round 2 |
| ~~CY2-OQ-8~~ | Cycle-1 callers behavior preserved when operators tune `balanced` defaults; no migration artifact shipped. | A-PR-1 (revised) / NFR-OP-07; BA Round 3 / TW Round 3 |
| ~~CY2-OQ-9~~ | Provider-incompatible preset knobs are soft-ignored + reported via `X-Preset-Ignored-Knobs`. | FR-PR-09; BA Round 3 |
| ~~CY2-OQ-10~~ | Preset resolution lives in `services/synthesize_service.py` producing frozen `EffectiveSynthesisConfig`. | FR-PR-06; BA Round 3 |

## 11. Roadmap (out of scope, captured for follow-up cycles)

Each item carries a brief dependency note on this cycle's parity work. Detailed value/effort/risk scoring lives in `docs/specs/improvement-analysis.md`.

| Roadmap item | Endpoint / surface | Depends on (this cycle) |
|---|---|---|
| OpenAI-compat voice management adapter | `/v1/audio/voices/*` (currently 501) | OpenAI publishing a stable voice contract; FR-VS CRUD |
| Formal signed-consent records | `POST /v1/audio/voice_consents/*` | FR-VS CRUD voice store; auth |
| STT — transcription / translation | `/v1/audio/transcriptions`, `/v1/audio/translations` | FR-HW provider registry pattern; new STT provider class |
| Chat completions (TTS-flavored) | `/v1/chat/completions`, `/v1/chat/models` | Possibly out-of-charter for a TTS service |
| Realtime bidirectional | `/v1/realtime/*` (WebSocket) | FR-CC cancellation primitives; chunk-level streaming infrastructure |
| Prometheus `/metrics` | `GET /metrics` | FR-OB structured logging + request ids (counters reachable per NFR-OB-05) |
| Content-addressable audio cache | implicit | FR-CA model cache pattern; hash over normalized text + voice + params |
| Token/sentence-level streaming | rich endpoint extension | FR-EP streaming groundwork |
| Parallel chunk synthesis | internal | FR-CC concurrency model |
| SSML / prosody markup | rich endpoint field | text preprocessing extension |
| ~~FLAC + 24-bit WAV encoding~~ | `response_format` values | **PULLED INTO CYCLE 2** — see §4.16 FR-FMT |
| MP3 / Opus encoding | `response_format` extension | encoder integration; lossy codecs (Opus needs `pyopus`/ffmpeg) deferred |
| In-process rate limiting | middleware | request id context; token bucket |
| Voice preview endpoint | `GET /v1/voices/{id}/preview` | FR-VM listing |
| Multi-replica deploy | infrastructure | external voice map + model cache; out of scope per NFR-SC-01 |
| Auth/AuthZ | middleware | gated by deployment context change |
| Formal voice consent enforcement | request schema + storage | gated by Roadmap voice enrollment |

## 12. Traceability Matrix

Per-area FR ↔ NFR ↔ UAT mapping.

| Functional area | FR IDs | NFR IDs | UAT IDs |
|---|---|---|---|
| 4.1 Auto-detection | FR-HW-01..07 | NFR-OP-01 | UAT-HW-01..06 |
| 4.2 Rich endpoint | FR-EP-01..05 | NFR-PF-01..03, NFR-OB-03, NFR-MT-04 | UAT-EP-01..07 |
| 4.3 OpenAI adapter | FR-OA-01..04 | NFR-PT-03, NFR-PT-03b | UAT-OA-01..05 |
| 4.4 Voice CRUD & storage | FR-VS-01..12 | NFR-SE-01..03, NFR-PV-01..05, NFR-CP-01, NFR-ST-01..04 | UAT-VS-01..12 |
| 4.5 Voice seed ingestion | FR-VM-01..05 | NFR-OP-05 | UAT-VM-01..05 |
| 4.6 Concurrency | FR-CC-01..05 | NFR-PF-02/04, NFR-SC-01..03, NFR-RL-03 | UAT-CC-01..04 |
| 4.7 Model cache | FR-CA-01..04 | NFR-SC-04 | UAT-CA-01..03 |
| 4.8 Lifecycle | FR-HL-01..05 | NFR-RL-01/05, NFR-OP-01 | UAT-HL-01..05 |
| 4.9 Observability | FR-OB-01..04 | NFR-OB-01..04, NFR-PV-02..03 | UAT-OB-01..04 |
| 4.10 Config | FR-CF-01..03 | NFR-OP-03 | UAT-CF-01..04 |
| 4.11 Error model | FR-ER-01..04 | NFR-SE-04 | UAT-ER-01..02, UAT-OB-04 |
| 4.12 Quality gates | FR-QG-01..04 | NFR-MT-01..05, NFR-SE-05..06, NFR-PT-01..02 | UAT-QG-01..05 |
| 4.13 Documentation | FR-DC-01..03 | NFR-MT-06, NFR-CP-01 | UAT-DC-01..03 |
| **4.14 Presets** *(cycle 2)* | FR-PR-01..13 | NFR-PR-01..04, NFR-SE-09..10, NFR-OP-06..07, NFR-PT-05 | UAT-PR-01..17 |
| **4.15 Post-processing** *(cycle 2)* | FR-PP-01..08 | NFR-PP-01..03, NFR-CP-03 | UAT-PP-01..07 |
| **4.16 Format extension** *(cycle 2)* | FR-FMT-01..07 | NFR-FMT-01..03, NFR-PT-06 | UAT-FMT-01..06 |

## 13. Success Criteria

The cycle is complete when:

- All 13 functional areas (§4) are implemented and their UAT cases pass.
- Hardware auto-detection picks device and provider with documented behavior, validated by unit tests with monkeypatched torch availability.
- The rich endpoint and the OpenAI adapter pass the paired byte-identity test (UAT-OA-05) on a warm model, or the relaxed perceptual test if RISK-8 materializes.
- TTS-specific strengths (voice cloning via map, semantic chunking, per-chunk RMS normalization, multilingual text expansion, streaming, OpenAI envelope, fail-fast config) remain working from a user-facing perspective; no UAT regression.
- CI is green: ruff clean, `mypy --strict` clean, `pytest --cov-fail-under=80`, `pip-audit` clean, `docker build` succeeds.
- README, diagrams, and OpenAPI are updated; the biometric notice is in place.
- The Roadmap (§11) and detailed analysis (`improvement-analysis.md`) exist and are referenced from README so follow-up cycles can pick up without rediscovery.

### Cycle-2 Success Criteria *(additive to cycle-1)*

The cycle-2 increment is complete when:
- All 3 new functional areas (§4.14, §4.15, §4.16) are implemented and their UAT cases pass: UAT-PR-01..17, UAT-PP-01..07, UAT-FMT-01..06 (30 cases total).
- `config/presets.json` exists with three built-in presets (`fast`, `balanced`, `quality`) — operator-tunable; the file is validated at startup against the `PresetConfig` Pydantic model and against file-permission posture (NFR-SE-09).
- `SynthesizeRequest` accepts `preset: str` and `response_format ∈ {wav, wav24, flac}`; precedence resolution lives in `services/synthesize_service.py::synthesize_core` and yields a frozen `EffectiveSynthesisConfig`.
- `services/audio_postprocess.py` exists; pipeline order denoise → silence_trim → rms_normalize is documented in the module + verified by UAT-PP-03; denoise is feature-flagged via `[denoise]` extra.
- `POST /v1/audio/speech` byte-identity contract preserved: `tests/test_openai_adapter_parity.py` (S-018 paired UAT) passes byte-identically AND its source file is byte-identical to its cycle-1 form (UAT-PR-17 / NFR-PT-05).
- Each `TTSProviderStrategy` declares `supported_response_formats: set[Literal["wav","wav24","flac"]]` (mypy-strict enforced).
- CI green on all gates including the 30 new UAT cases.
- README + diagrams + OpenAPI updated for presets / postproc / format-ext (per FR-DC + cycle-2 documentation tasks in the next sprint plan).
- All 17 new NFRs (§11b in writer-nfr.md) are codified in source documents and at least the MUST-priority ones have implementation hooks.

**Explicit non-deliverables (cycle 2):**
- No migration tooling / no `presets.json.cycle1-compat` reference file (NFR-OP-07).
- No CI-enforced per-preset SLO assertions (NFR-PR-01 et al. are documentation only).
- No denoise dependency in the default install (only via `[denoise]` extra).
- No mid-stream preset switching, no per-request preset override on `/v1/audio/speech`, no custom presets in `/v1/models` enumeration.