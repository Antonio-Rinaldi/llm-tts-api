# llm-tts-api — Software Requirements Specification

**Status:** Draft
**Date:** 2026-05-17
**Cycle:** Engineering parity with llm-image-api + selected feature work; **not a rewrite**.
**Reference codebase (quality bar):** `/Volumes/Coding/Projects/Applications/epub/llm-image-api`

**Source documents** (this SRS supersedes for high-level navigation; source docs remain authoritative for detail):
- Request: `docs/specs/requests/improvement-request.md`
- Functional: `docs/specs/analyst-frs.md`
- Acceptance tests: `docs/specs/analyst-UAT.md`
- Non-functional: `docs/specs/writer-nfr.md`
- Forward-looking analysis: `docs/specs/improvement-analysis.md`

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
| MP3/Opus/Flac encoding | `response_format` values | encoder integration |
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

## 13. Success Criteria

The cycle is complete when:

- All 13 functional areas (§4) are implemented and their UAT cases pass.
- Hardware auto-detection picks device and provider with documented behavior, validated by unit tests with monkeypatched torch availability.
- The rich endpoint and the OpenAI adapter pass the paired byte-identity test (UAT-OA-05) on a warm model, or the relaxed perceptual test if RISK-8 materializes.
- TTS-specific strengths (voice cloning via map, semantic chunking, per-chunk RMS normalization, multilingual text expansion, streaming, OpenAI envelope, fail-fast config) remain working from a user-facing perspective; no UAT regression.
- CI is green: ruff clean, `mypy --strict` clean, `pytest --cov-fail-under=80`, `pip-audit` clean, `docker build` succeeds.
- README, diagrams, and OpenAPI are updated; the biometric notice is in place.
- The Roadmap (§11) and detailed analysis (`improvement-analysis.md`) exist and are referenced from README so follow-up cycles can pick up without rediscovery.