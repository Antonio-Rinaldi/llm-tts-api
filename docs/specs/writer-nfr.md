# llm-tts-api — Non-Functional Requirements Specification

**Status:** Draft
**Date:** 2026-05-17
**Source request:** `docs/specs/requests/improvement-request.md`
**Companion docs:** `analyst-frs.md`, `analyst-UAT.md`

Priority legend: **MUST** (blocking), **SHOULD** (strongly desired), **COULD** (nice-to-have).
ID convention: `NFR-<area>-NN`.

---

## 1. System Quality Profile

| Attribute | Profile |
|---|---|
| System type | API/service (FastAPI), single-process, ML-backed (TTS inference) |
| Deployment context | Internal LAN / trusted network only; no public exposure in this cycle |
| Primary host | Apple Silicon, ≥ 32 GB unified memory |
| Container target | Linux x86_64 (CUDA optional) via Dockerfile |
| Expected load | Single-user / few internal callers; ≤ 4 concurrent in flight typical |
| Availability target | Best-effort; container restart policy recovers from crashes; no SLA |
| Data sensitivity | Synthesis text: low/medium. Voice records (audio + transcript): high (voice biometric) |
| Compliance | Voice biometric posture documented; formal consent flow deferred to Roadmap |

---

## 2. Performance (NFR-PF)

### NFR-PF-01 (MUST) — No latency regression on the primary path
Before the cycle ships, a one-time baseline measurement of `/v1/audio/speech` (current code) on the reference Apple Silicon host MUST be recorded for a representative input (e.g. 500 chars Italian text, default voice `alloy`). The new code path (via `/v1/tts/synthesize`) MUST NOT regress p50 or p95 latency by more than **+10%** for the same input/voice/host.
*Acceptance:* baseline numbers and post-change numbers recorded in `docs/perf/baseline.md`; a `pytest -m perf` smoke test asserts the new code completes within the +10% budget on the dev box.

### NFR-PF-02 (MUST) — Event-loop responsiveness under inference
While a synthesis is in flight, `GET /health` MUST respond in ≤ 50 ms p95. Validated by UAT-CC-02.
*Rationale:* enforces FR-CC-02 (`anyio.to_thread`) at the system level.

### NFR-PF-03 (SHOULD) — Streaming first-byte latency
For streamed requests on the rich endpoint, time-to-first-audio-byte SHOULD be ≤ time-to-first-chunk-complete on the same input (i.e. streaming SHOULD actually flush early, not buffer the first chunk fully). Validated subjectively via UAT-EP-02 plus a perf assertion that first-byte arrives before total-duration / 2.

### NFR-PF-04 (SHOULD) — Concurrent throughput
With `TTS_MAX_CONCURRENT_REQUESTS=2` and an artificially-slowed provider (1s per chunk), 4 parallel requests SHOULD complete within ~2 × the single-request wall-clock time (±20%). Validated by UAT-CC-01.

### NFR-PF-05 (COULD) — Per-chunk normalization cost budget
Per-chunk RMS normalization SHOULD not exceed 5% of the synthesis wall-clock time. Measured as a debug log line, not a CI gate.

---

## 3. Scalability (NFR-SC)

### NFR-SC-01 (MUST) — Single-process bound
The service MUST operate correctly as a single process. No requirement, design, or test MAY assume multi-replica deployment in this cycle. (Multi-replica is a Roadmap consideration; see NFR-EV-01.)

### NFR-SC-02 (MUST) — Configurable concurrency ceiling
`TTS_MAX_CONCURRENT_REQUESTS` MUST be honored as a hard ceiling. Operators on ≥ 32 GB Apple Silicon SHOULD be able to set this to **2** without regressions.

### NFR-SC-03 (SHOULD) — Queue depth sized for typical fan-out
Default `TTS_MAX_QUEUE_DEPTH=8` MUST absorb burst arrivals from a small number of upstream callers without dropping under the expected load profile (≤ 4 concurrent typical).

### NFR-SC-04 (MUST) — Model cache footprint predictability
Memory footprint at `TTS_MODEL_CACHE_SIZE=1` (default) MUST be predictable: one loaded model + working set ≤ 60% of available RAM on a 32 GB host for any currently-supported model in the registry.

---

## 4. Reliability & Availability (NFR-RL)

### NFR-RL-01 (MUST) — Graceful shutdown drain
On SIGTERM, the service MUST drain in-flight requests up to `TTS_SHUTDOWN_DRAIN_SECONDS` (default 30 s) before force-exit. New requests during drain MUST receive `503 capacity_error.service_unavailable`.
*Trace:* FR-HL-04.

### NFR-RL-02 (MUST) — Recovery via container restart
The service is allowed to crash on unrecoverable errors. The expectation is that a container orchestrator (or local supervisor) restarts the process. No HA / failover requirement applies in this cycle.

### NFR-RL-03 (MUST) — No cascading failure from a single request
A failure in one synthesis (provider exception, OOM in one model, corrupt ref_audio) MUST NOT corrupt shared state (model cache, voice map, semaphores) or terminate the process. Validated via UAT-VC-04 and provider-error UAT cases.

### NFR-RL-04 (SHOULD) — Crash diagnostics
On an unhandled exception that terminates a request, the last full traceback MUST appear in logs. Container logs are the canonical persistence layer; no separate on-disk crash dump required in this cycle.

### NFR-RL-05 (MUST) — Readiness reflects actual capability
`GET /ready` MUST return `503` whenever the service cannot currently serve a synthesis (warming up, draining, voice map invalid, no model loadable). It MUST NOT return `200` and then fail the first request.
*Trace:* FR-HL-02.

---

## 5. Security (NFR-SE)

The service is deployed on an internal LAN. The threat model is **accidental misuse and resource exhaustion**, not hostile attack. Auth/AuthN/AuthZ are intentionally deferred (Roadmap).

### NFR-SE-01 (MUST) — Input size hard caps enforced
- Text input ≤ `TTS_MAX_INPUT_CHARS` (default 4096, min 256 enforced at config validation).
- Voice-CRUD audio upload ≤ `TTS_REFAUDIO_MAX_BYTES` (default 10 MiB).
- HTTP request body cap enforced by FastAPI / uvicorn (configurable).

### NFR-SE-02 (MUST) — Content-type allow-list on voice-CRUD audio uploads
Audio uploaded via `POST /v1/tts/voices` or `PUT /v1/tts/voices/{id}` MUST be validated against an allow-list (`audio/wav`, `audio/x-wav`, `audio/flac`, `audio/mpeg`) AND magic-bytes inspection. Header alone is insufficient.
*Trace:* FR-VS-05.

### NFR-SE-03 (MUST) — Path safety on the filesystem blob backend
For the `fs` blob backend, voice file paths MUST be derived from a validated voice id (slug pattern `[a-z0-9_-]{1,64}`) under `TTS_VOICE_STORE_DIR`. No client-supplied path component may flow into a filesystem path. Synthesis-time temp files MUST be created with `tempfile.NamedTemporaryFile` (or equivalent) and deleted in `finally`.
*Trace:* FR-VS-04, FR-VS-10, FR-VS-11.

### NFR-SE-04 (MUST) — No payload echo in errors
Error envelopes MUST NOT include the full synthesis input text, uploaded audio bytes, transcripts, or local file paths / blob URIs. Validated via UAT-ER-02.

### NFR-SE-05 (MUST) — Dependency hygiene gate
`pip-audit` MUST run in CI and fail on advisories above a documented severity threshold (default: any high-severity advisory).
*Trace:* FR-QG-01.

### NFR-SE-06 (SHOULD) — Container image hygiene
The Docker image SHOULD run as a non-root user, contain no `build-essential` in the final stage (move to a builder stage), and pin a specific Python base image digest in CI.

### NFR-SE-07 (SHOULD) — CORS posture
Default CORS configuration MUST be **closed** (no `*` origin). When operators need cross-origin access, they MUST opt in via an env var with an explicit origin list.

### NFR-SE-08 (COULD) — Defense-in-depth on voice-CRUD create
Beyond the `consent_acknowledged=true` field enforced at FR-VS-05, the service MAY require a matching request header (e.g. `X-Voice-Consent-Acknowledged: true`) on `POST /v1/tts/voices` and `PUT /v1/tts/voices/{id}` (when the audio part is replaced) as a defense-in-depth posture. This makes accidental consent-skipping by misconfigured clients harder. Formal signed-consent records remain Roadmap.

---

## 6. Privacy & Data Handling (NFR-PV)

### NFR-PV-01 (MUST) — Bounded payload retention
- Synthesis **input text** MUST NOT be persisted beyond the request lifecycle.
- **Generated audio** MUST NOT be persisted; streamed/returned, then discarded.
- **Voice records** (metadata + audio blob) ARE persisted by design — they are the operator's / user's curated content with a clear lifecycle (CRUD, delete is supported). They are NOT considered request-scoped payloads.
- Synthesis-time temp files derived from a voice blob MUST be cleaned per FR-VS-10.

### NFR-PV-02 (MUST) — Log redaction by default
At `INFO` level and above, logs MUST contain only: `request_id`, request shape metadata (text length, `voice_id`, `voice_source` ∈ `{seed, crud}`, provider, model, chunk count, duration). Logs MUST NOT contain raw synthesis input text, uploaded audio bytes, transcripts, or blob paths/URIs.

### NFR-PV-03 (SHOULD) — DEBUG-level snippets bounded
At `DEBUG` level, the service MAY log truncated text snippets (≤ 80 chars, suffix-truncated, no audio bytes ever). Operators are responsible for not running `DEBUG` in environments with sensitive payloads.

### NFR-PV-04 (MUST) — Voice biometric documentation
The README and `docs/architecture.md` MUST contain a section explicitly stating that:
1. Voice records (audio + metadata) processed by the voice-CRUD endpoints constitute **biometric data**.
2. The service **does** persist these records in the configured store backend; deletion is supported (FR-VS-09) and is the operator's responsibility for data-subject requests.
3. The minimal `consent_acknowledged` attestation enforced at FR-VS-05 / NFR-CP-01 is **not** a substitute for upstream consent capture in the operator's jurisdiction — formal signed-consent records remain a Roadmap item.

This is documentation + minimal enforcement, not a compliance guarantee.

### NFR-PV-05 (SHOULD) — Seed-file ref_audio files treated as configuration
Reference audio files referenced by `voice_map.json` are part of operator-provided seed configuration. The seed-ingestion mechanism (FR-VM) **copies** them into the voice store at startup; from that point on, the in-store blob is governed by NFR-PV-01 (bounded retention via CRUD) and the original seed file is no longer load-bearing for serving requests. Existence and readability of seed files are validated at ingestion time.

---

## 6b. Storage Backends (NFR-ST)

### NFR-ST-01 (MUST) — Default deploy needs no external services
Default backends `fs_json` (metadata) + `fs` (blob) MUST work with the base `pip install .` — **no** runtime dependency on Postgres, S3, or any network service. This preserves the "no new external services" deploy posture (NFR-SC-01).

### NFR-ST-02 (MUST) — Optional backends are optional dependencies
`PostgresMetadataRepository` MUST be importable only when the `[postgres]` extra is installed (e.g. `pip install .[postgres]`). `S3BlobRepository` MUST be importable only when the `[s3]` extra is installed. Selecting an optional backend via env without the extra installed MUST fail startup with a clear error (`config_error.missing_extra`).

### NFR-ST-03 (MUST) — Repository operations are atomic and concurrency-safe
- `FsJsonMetadataRepository`: writes via tempfile + `os.replace` for atomicity; an in-process `asyncio.Lock` guards write paths; reads are lock-free (read the file once per operation or maintain an in-memory snapshot refreshed on writes).
- `FsBlobRepository`: same tempfile + rename pattern for puts; deletes are best-effort with retry; concurrent reads of an existing blob succeed without locking.
- `PostgresMetadataRepository` and `S3BlobRepository`: rely on the backend's native transactional / strongly-consistent semantics; client retries on transient errors per backend SDK guidance.

### NFR-ST-04 (SHOULD) — Backend health surfaced in `/ready`
`/ready` MUST verify metadata and blob backends are reachable during warmup. Failure to reach either at startup → readiness 503 with reason `voice_store_unavailable`. Once warmup succeeds, transient backend failures during a request return `provider_error.voice_store_unavailable` for that request but MUST NOT toggle readiness back to 503 (avoid flap).
*Trace:* FR-VS-01..02, FR-HL-02.

---

## 7. Observability (NFR-OB)

### NFR-OB-01 (MUST) — Request correlation end-to-end
Every log line emitted while serving a request MUST carry the request's `X-Request-ID`. The id MUST appear in the response headers regardless of outcome.
*Trace:* FR-OB-01.

### NFR-OB-02 (MUST) — Structured logging baseline
Default log format: human-readable with consistent fields (`ts | level | logger | request_id | message | extras`). Opt-in JSON format via `APP_LOG_FORMAT=json` MUST emit one valid JSON object per line with the same fields.
*Trace:* FR-OB-02.

### NFR-OB-03 (MUST) — Response-header metadata
Every successful response MUST set `X-Request-ID`, `X-Provider`, `X-Model`, `X-Device`, `X-Dtype` and (when known at response start) `X-Chunks` and `X-Total-Duration-Ms`. Every error response MUST set `X-Request-ID` and `X-Error-Code`.

### NFR-OB-04 (SHOULD) — Health-endpoint signal richness
`GET /health` MUST include a `version` field derived from package metadata, current `device`, `dtype`, `provider`, list of `model_loaded`, `queue_depth`, `concurrent_active`. This is sufficient for local operator diagnostics in lieu of `/metrics`.

### NFR-OB-05 (COULD) — Prometheus `/metrics` endpoint
An in-process Prometheus text-format `/metrics` endpoint is OUT of scope but listed in the Roadmap. Nothing in the codebase MUST preclude adding it later (e.g. counters must be reachable from a future metrics module).

---

## 8. Maintainability (NFR-MT)

### NFR-MT-01 (MUST) — CI quality gate
CI MUST run: `ruff check`, `ruff format --check`, `mypy --strict src/`, `pytest --cov` with `--cov-fail-under=80`, `pip-audit`. All MUST pass before merge.
*Trace:* FR-QG-01.

### NFR-MT-02 (MUST) — Coverage policy
Test coverage MUST be ≥ 80% of `src/`. Decision deferred (OQ-4) on whether 80% applies from day one or ratchets up; either way the **final state at end of cycle** MUST be ≥ 80%.

### NFR-MT-03 (MUST) — Type discipline
`src/` MUST pass `mypy --strict`. Public engine/service interfaces MUST be `Protocol`-typed (no untyped `Any` callables). `py.typed` marker MUST ship with the package.
*Trace:* FR-QG-03.

### NFR-MT-04 (MUST) — Pydantic strictness
All request models MUST set `model_config = ConfigDict(extra="forbid")`. Response models MUST be explicit Pydantic models, not `dict[str, Any]`.

### NFR-MT-05 (SHOULD) — Atomic commits / atomic PRs per FR area
Implementation work SHOULD be split so that one FR area (HW, EP, VC, etc.) is a single review unit. No cross-area mega-PRs.

### NFR-MT-06 (SHOULD) — Diagram freshness
Class and sequence diagrams in `docs/diagrams/` MUST be updated in the same PR that changes the structures they depict. Stale diagrams are a review blocker.
*Trace:* FR-DC-02.

---

## 9. Operability (NFR-OP)

### NFR-OP-01 (MUST) — Fail-fast startup
Configuration errors (missing required env var, invalid voice map, no viable provider) MUST cause startup to exit non-zero with a clear log message before any GPU/CPU model load begins. No retry loops.
*Trace:* FR-HW-05, FR-VM-01, FR-CF-01.

### NFR-OP-02 (MUST) — Container-friendly deploy
The Dockerfile MUST produce an image that:
- starts `uvicorn` on the documented port,
- handles SIGTERM correctly (drain per NFR-RL-01),
- exposes `/health` and `/ready` for orchestrator probes,
- reads all configuration from env vars (no rebuild required to change config),
- mounts `voice_map.json` and reference audio as a volume so they can change without a rebuild.

### NFR-OP-03 (MUST) — Configuration via env only
No YAML / TOML config file is introduced. Settings derive exclusively from environment variables (with optional `.env`/`.env.local` files for dev). All new env vars MUST be prefixed `TTS_` or `APP_`.

### NFR-OP-04 (SHOULD) — Memory sanity check
At startup, a `psutil`-based available-memory check SHOULD emit a `WARNING` if below `TTS_MIN_FREE_MEMORY_GB` (default 4). Soft warning only — never blocks startup.
*Trace:* FR-HL-05.

### NFR-OP-05 (MUST) — Voice map hot-reload without restart
Operators MUST be able to add/edit voices in `voice_map.json` and see the changes applied within ~2 s with no service restart, provided the new map validates atomically.
*Trace:* FR-VM-02/03.

---

## 10. Portability & Compatibility (NFR-PT)

### NFR-PT-01 (MUST) — Supported targets
The service MUST run on:
- **macOS / Apple Silicon (native)** — primary dev and reference path; MLX active.
- **Linux x86_64 in a container** — production-style deploy; CUDA active when a compatible GPU is present, else CPU fallback per FR-HW.

The following are explicitly **NOT** supported in this cycle: Windows native, Linux ARM64 in a container, BSDs.

### NFR-PT-02 (MUST) — Python version pinning
Source declares `python>=3.10` for dev compatibility. Container builds and CI MUST exercise the Python version shipped in the Dockerfile (currently 3.13). Any version-specific code paths (match statements etc.) MUST be guarded or explained.

### NFR-PT-03 (MUST) — OpenAI client compatibility preserved
`/v1/audio/speech` request/response shape MUST remain compatible with the `openai` Python SDK such that `client.audio.speech.create(...)` and `client.audio.speech.with_streaming_response.create(...)` continue to work against the service unchanged.
*Trace:* FR-OA-01..03; UAT-OA-01, UAT-OA-02.

### NFR-PT-04 (SHOULD) — Provider plug-ability preserved
The `TTSProviderRegistry` + `TTSProviderStrategy` Protocol surface MUST remain the integration seam for new providers. Refactors to the engine layer MUST NOT couple non-provider concerns into provider code.

---

## 11. Compliance & Legal (NFR-CP)

### NFR-CP-01 (MUST) — Voice biometric notice + minimal consent attestation
README MUST include a clearly-labeled section noting that voice cloning processes biometric data and that operators are responsible for upstream consent in their jurisdiction. In addition, the voice-CRUD create operation MUST enforce a minimal consent attestation: `consent_acknowledged=true` MUST be present in the metadata for `POST /v1/tts/voices` to succeed. The attestation is stored with the record (FR-VS-04). Formal, signed-consent records remain Roadmap.

### NFR-CP-02 (SHOULD) — License inventory
The CI MAY run a license audit (e.g. `pip-licenses`) producing a `docs/licenses.md`. Not a blocking gate in this cycle.

### NFR-CP-03 (MUST) — Denoise output inherits the biometric notice *(cycle 2)*
The optional `[denoise]` extra (FR-PP-05) produces audio that is a refined version of an already-cloned biometric voice. README MUST state that denoise output inherits the NFR-CP-01 biometric notice verbatim; operators MUST NOT treat denoise as a quality-only improvement disjoint from consent obligations.

---

## 11b. Cycle-2 Quality Attributes

> Source: `docs/specs/requests/dual-mode-presets-request.md` (PO scoped) + analyst-frs.md §4.14-4.16 (FR-PR/PP/FMT) + technical-writer challenge rounds 1-3.
> All cycle-2 NFRs in this section. Existing cycle-1 sections (§§2-11) take precedence where they already cover a dimension; cycle-2 NFRs are additive.

### 11b.1 Preset Performance (NFR-PR) — *cycle 2*

#### NFR-PR-01 (SHOULD) — Documented preset TTFB targets
README MUST publish soft TTFB targets per preset, measured on the reference Apple Silicon (M1 Max) host with the default-selected provider:
- `fast`: p95 TTFB ≤ **250 ms**.
- `balanced`: p95 TTFB ≤ **800 ms**.
- `quality`: not TTFB-bound — preset is buffered-only per FR-PP-07. Total wall-clock SLO documented per FR-PP overhead budgets (NFR-PP-01..03).

These targets are **documentation only** (per CY2-OQ-6 resolution). They are NOT asserted by CI tests; operators measure via `scripts/perf_baseline.py` (S-002) extended to the rich endpoint and per-preset (S-021 scope).

#### NFR-PR-02 (MUST) — Preset resolution overhead is bounded
The per-request preset resolution step (FR-PR-06: build `EffectiveSynthesisConfig`) MUST complete in ≤ **1 ms** (p95) on the reference host. Resolution is pure-Python field merge — no I/O, no allocations beyond the dataclass. Codifies that resolution NEVER adds measurable latency to TTFB.

#### NFR-PR-03 (MUST) — Hot-reload latency
`config/presets.json` hot-reload MUST apply within **≤ 2 s** of file write (matching NFR-OP-05 voice-map hot-reload). On polling-fallback hosts the SLO becomes ≤ `TTS_VOICE_MAP_POLL_INTERVAL_SECONDS` + 1 s (reuses the existing polling knob).

#### NFR-PR-04 (MUST) — In-flight request snapshot
A request's preset registry snapshot taken at request-start MUST remain stable for the full duration of the request, even if the on-disk `presets.json` changes mid-flight. Codifies FR-PR-11 as a portability invariant — no test for it directly, but the snapshot mechanism is a hard contract for future contributors.

### 11b.2 Post-processing Performance (NFR-PP) — *cycle 2*

#### NFR-PP-01 (SHOULD) — Per-step postproc overhead (per 1 s of generated audio)
Documented in README; not test-enforced. Measured on the reference Apple Silicon host:
- `rms_normalize`: ≤ **5 ms/s** of generated audio.
- `silence_trim`: ≤ **10 ms/s** of generated audio.
- `denoise` (when `[denoise]` extra is installed): ≤ **200 ms/s** of generated audio.

Aggregate worst case (all three enabled, 10 s audio): ~2.15 s postproc overhead, before format conversion.

#### NFR-PP-02 (MUST) — Buffer lifetime for full-audio in RAM
The post-processing pipeline holds the full assembled WAV body in RAM for the duration of one request (in the `services/audio_postprocess.py` invocation). The buffer MUST be released (no module-level retention) immediately after response encoding completes. NO logging, NO disk write, NO IPC of the audio bytes is permitted in the postproc module. Codifies what is true today; prevents future regression.

#### NFR-PP-03 (MUST) — Streaming downgrade is observable
When FR-PP-07 fires (preset=quality + stream=true → buffered), the response MUST include header `X-Stream-Downgraded: quality-postproc`. Operators and clients can detect the downgrade without inspecting body length or chunk transfer encoding. NOT optional — required for portability of clients that branch on streaming vs buffered.

### 11b.3 Format Extension Performance (NFR-FMT) — *cycle 2*

#### NFR-FMT-01 (SHOULD) — Format-conversion cost budget (per 1 s of generated audio)
Documented in README; not test-enforced. Measured on the reference Apple Silicon host via `soundfile`:
- `wav` → `wav` (passthrough): **0 ms** (no-op).
- `wav` → `wav24`: ≤ **10 ms/s** of audio (dtype upcast + re-write).
- `wav` → `flac`: ≤ **50 ms/s** of audio (libsndfile compression).

#### NFR-FMT-02 (MUST) — Supported encoder portability
`wav` / `wav24` / `flac` MUST work end-to-end on:
- Linux x86_64 (the cycle-1 deploy target).
- macOS arm64 (Apple Silicon — the reference dev host).

CI MUST exercise all three formats on both platforms (extending the Sprint-6 docker CI). Windows / Linux arm64 are explicitly OUT of the cycle-2 portability matrix (matches cycle-1 NFR-PT-01).

#### NFR-FMT-03 (MUST) — Content-Type correctness
Response `Content-Type` MUST match the resolved `response_format`: `audio/wav` for `wav` and `wav24`; `audio/flac` for `flac`. Codifies FR-FMT-07 as a portability contract — clients that route by Content-Type don't break.

### 11b.4 Security (NFR-SE) — *cycle 2 additions*

#### NFR-SE-09 (MUST) — presets.json file-permission posture *(cycle 2)*
At startup, `config/presets.json` MUST be:
- **Owned by the service user** (uid match); AND
- **NOT world-writable** (mode bits exclude `0o002`).

If either check fails, the service MUST refuse to start with `config_error.presets_unsafe_permissions` and log the offending mode/owner. Defense-in-depth: an attacker who can write `presets.json` can pin a malicious `(provider, model)` or normalize-db value. Symmetric with the same check applied to `voice_map.json` (NOT yet in cycle 1 — flagged as a roadmap addition).

#### NFR-SE-10 (MUST) — Hot-reload validates BEFORE swap *(cycle 2)*
On any `presets.json` change notification (watchfiles or polling), the reloader MUST:
1. Parse + validate the new file against the `PresetConfig` Pydantic model.
2. **Only if validation succeeds**, atomically swap the in-memory registry.
3. If validation fails, log WARN with the error details, keep the previous (last-known-good) registry in memory, do NOT cause the service to crash.

Worst case under attack (operator's adversary writes invalid presets.json): reload skipped, log alerts operator, service continues on prior good config. Zero downtime, zero unsafe state. Codifies an attack-tolerant reload semantic.

### 11b.5 Operability (NFR-OP) — *cycle 2 additions*

#### NFR-OP-06 (MUST) — Per-synthesis preset observability *(cycle 2)*
Each request to `POST /v1/tts/synthesize` (and `POST /v1/audio/speech`) MUST emit ONE INFO-level log line capturing:
- `request_id` (from NFR-OB-01 contextvar)
- `resolved_preset` (the preset name actually applied — default if request omitted preset)
- `ignored_knobs` (comma-separated; empty string when none)
- `postprocess_applied` (comma-separated; empty string when none)
- `response_format` (resolved)
- `stream_downgraded` (boolean; true when FR-PP-07 fired)

Log line is payload-free per NFR-PV-02 (no `input` text, no audio bytes). Operators can `grep resolved_preset=quality` for triage. Future Prometheus `/metrics` work (Roadmap P0) derives counters from these without changing the log shape.

#### NFR-OP-07 (SHOULD) — Migration guidance is NOT a deliverable *(cycle 2)*
Cycle 2 explicitly does NOT promise migration tooling, cycle-1 byte-identity preservation, or a `presets.json.cycle1-compat` reference file. Operators who deployed cycle 1 and upgrade to cycle 2 SHOULD expect different defaults (because `balanced` is now an opinionated preset, not "whatever the old defaults were"). README MAY note this; no shipped artifact.

**Rationale (user decision in TW Round 3):** the cycle is a feature addition, not a maintenance migration. Operators tune to their workload regardless of cycle boundaries.

### 11b.6 Portability & Compatibility (NFR-PT) — *cycle 2 additions*

#### NFR-PT-05 (MUST) — S-018 byte-identity invariant under cycle 2 *(cycle 2)*
The S-018 paired UAT (`tests/test_openai_adapter_parity.py`) — which asserts byte-identical audio output between `POST /v1/tts/synthesize` (resolved with default preset = `balanced`, default config) and `POST /v1/audio/speech` (server-default preset, OpenAI-shape input) — MUST continue to pass byte-identically across all cycle-2 changes. The paired UAT is the load-bearing portability gate; any cycle-2 implementation change that breaks it is a defect, not a feature.

**Implementation guidance:** the rich endpoint with `preset=balanced` (and no explicit field overrides) MUST resolve to the same `EffectiveSynthesisConfig` that the OpenAI adapter resolves to (also with server-default `balanced`). Same provider, same model, same temperature/top_p/etc. resolution outcome.

**Note:** this NFR only constrains rich-with-balanced ↔ OpenAI byte-identity. Cycle-1-to-cycle-2 byte-identity (i.e. cycle-1 default behaviour vs cycle-2 default behaviour) is NOT promised (per NFR-OP-07).

#### NFR-PT-06 (MUST) — Provider capability declaration *(cycle 2)*
Each `TTSProviderStrategy` subclass MUST declare a `supported_response_formats: set[Literal["wav","wav24","flac"]]` capability (FR-FMT-02). The capability set is consulted at startup (preset+provider validation, FR-PR-13) and at request-time (explicit response_format mismatch, FR-FMT-03). Adding a new format in a future cycle requires updating each provider's declaration; missing declarations are a `mypy --strict` error.

---

## 12. Cross-Cutting Trade-offs

| Trade-off | Decision | Rationale |
|---|---|---|
| Single-process simplicity vs. multi-replica scale | Single-process | Internal LAN, ≤ 4 concurrent typical. Multi-replica adds shared-state complexity (voice map, model cache, metrics) without current value. |
| Auth absent vs. auth in scope | Auth absent this cycle | LAN-only deploy; auth is Roadmap. Mitigated by NFR-SE-01 size caps and NFR-SE-07 closed CORS. |
| Configurable inference timeout: default-disabled vs. default-enabled | Default-disabled | User decision (Round 2 of BA). Justified by long-passage TTS use cases. Operators with strict latency SLAs opt in via env. |
| Streaming with per-chunk metadata frames vs. raw byte stream | Raw byte stream + headers | OpenAI SDK compatibility (Round 1 of BA). Metadata via headers, not multipart. Trailing headers used for end-of-stream counts where supported. |
| Multi-warm model cache vs. single-slot | Single-slot (default 1) | Predictable memory (NFR-SC-04) on ≥ 32 GB hosts when running Voxtral-class models. Operators with headroom can opt to N. |
| DEBUG-level text snippets vs. never log payloads | DEBUG-only snippets | Diagnostic richness for triage at the cost of operator responsibility. INFO is payload-free (NFR-PV-02). |
| *(cycle 2)* Soft preset SLOs vs. CI-enforced SLO tests | Soft documentation only | CY2-OQ-6 resolution. CI flake risk + hardware variability vs. discoverability. Operator-driven measurement via `scripts/perf_baseline.py`. |
| *(cycle 2)* Quality preset is buffered-only vs. streaming-with-windowed-postproc | Buffered-only with silent downgrade + `X-Stream-Downgraded` header | FR-PP-07 + NFR-PP-03. Simpler semantics; surfaces the downgrade rather than silently degrading quality. |
| *(cycle 2)* Migration tooling shipped vs. operator-tunes-balanced | Operator-tunes-balanced; no migration artifact shipped | NFR-OP-07. Cycle-2 is a feature addition, not a maintenance event. Avoids permanent legacy-compat machinery in the codebase. |
| *(cycle 2)* Custom presets enumerated in OpenAPI vs. operator-private | Operator-private | FR-PR-12. Client-side typed SDKs see stable types; ops can tune per-deploy without spec churn. |
| *(cycle 2)* presets.json file-permission check at startup vs. trust container security | Startup file-permission check + reject | NFR-SE-09. Defense-in-depth at the cost of dev-host friction (operators must `chmod 0644` + `chown` the file). |
| *(cycle 2)* Denoise default-on (quality) vs. default-off-no-extra | Default-off when `[denoise]` extra absent; logs WARN, no-ops | FR-PP-05. Honors operator's deploy choice; doesn't fail requests due to missing optional dep. |
| *(cycle 2)* Format conversion in service-layer vs. per-provider | Service-layer | FR-FMT-06 + NFR-PT-06. One canonical conversion site; providers stay focused on synthesis. Cost: provider's native format is always-WAV intermediate (small memory tax). |

---

## 13. Risk Register

| ID | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| RISK-1 | Provider capability declaration (`supports_devices`) is harder to retrofit than assumed; auto-selection logic becomes a hardcoded device→provider table. | Medium | Low — falls back to a clean if/elif chain; still meets FR-HW-04. | Spike provider capability API early in cycle; document the fallback. |
| RISK-2 | Async refactor of `SpeechSynthesizer` is more invasive than the "no rewrite" constraint allows, forcing partial async with residual blocking calls. | Medium | Medium — event-loop responsiveness NFR-PF-02 may slip under contention. | Stage refactor behind feature flag; benchmark NFR-PF-02 before/after. |
| RISK-3 | `watchfiles` unreliable inside Docker on bind-mounted volumes; voice map hot-reload silently broken in container. | Medium | Medium — fails operational requirement NFR-OP-05. | Polling fallback path; UAT-VM-03 run inside container, not just dev box. |
| RISK-4 | Voice-CRUD uploads enable storage/DoS exhaustion via many medium-sized uploads (each under the per-file cap but cumulatively heavy on disk or S3 bucket). | Low (LAN) | Medium — service stall, OOM, storage fill. | NFR-SE-01 per-file caps + NFR-SC-02 concurrency ceiling. Per-client rate limiting and per-tenant storage quotas on Roadmap. |
| RISK-5 | Voice biometric documentation without enforcement is judged insufficient by stakeholders/legal once deployed. | Low | High — forces a Roadmap item into-scope mid-cycle. | NFR-CP-01 README notice + NFR-SE-08 optional consent header as defense-in-depth. |
| RISK-6 | Coverage ratchet to 80% takes significantly longer than implementation work for some FR areas (e.g. provider error paths). | Medium | Low — schedule pressure. | Plan coverage work per FR area in NFR-MT-05 atomic PRs. |
| RISK-7 | OpenAI SDK streaming behavior changes between SDK versions, breaking NFR-PT-03. | Low | Medium — silent client breakage. | Pin SDK version in tests; record SDK version in `docs/perf/baseline.md`. |
| RISK-PR-1 *(cycle 2)* | Preset SLOs (NFR-PR-01) published as docs but not measured; operators report widely-differing TTFB on non-reference hardware. | Medium | Low — docs say "measured on M1 Max; YMMV." | README disclaims hardware dependency; operators measure via S-021 scripts. |
| RISK-PR-2 *(cycle 2)* | Per-provider `supported_response_formats` (NFR-PT-06) measured incorrectly day-one; client receives 400 `format_unsupported` for what should work. | Medium | Medium — UX degradation, false negative. | Measure each provider with a smoke test before merge; record matrix in software-spec.md cycle-2 section. |
| RISK-PR-3 *(cycle 2)* | presets.json hot-reload race: a `mv` followed by `chmod` can briefly expose a world-writable state; NFR-SE-09 startup-check may miss runtime races. | Low | Low — startup check is one-shot. | NFR-SE-10 reload validates before swap; permission check at startup only, not on reload (documented trade-off). |
| RISK-PR-4 *(cycle 2)* | Postproc overhead (NFR-PP-01) exceeds the 200ms/s denoise budget on lower-spec hosts; quality preset wall-clock blows up. | Medium | Low — soft doc; operator can disable denoise via preset edit. | Budget is soft-doc; operator can disable denoise per-preset or via `[denoise]` extra opt-out. |
| RISK-PR-5 *(cycle 2)* | S-018 byte-identity (NFR-PT-05) breaks subtly because preset resolution introduces a new code path that diverges between rich and OpenAI under default config. | Medium | High — load-bearing portability contract violated. | S-018 paired UAT is the gate; CI runs it on every PR. Resolution logic centralized in one place per FR-PR-06 to minimize drift. |

---

## 14. Evolution / Roadmap impact (NFR-EV)

### NFR-EV-01 — Codebase MUST NOT preclude future capabilities
The Roadmap items below MUST remain implementable without major rework. The architectural choices in this cycle MUST be re-checked against each:
- OpenAI-compat voice management (`/v1/audio/voices/*` adapter) — depends on OpenAI publishing a stable voice contract + FR-VS CRUD.
- STT endpoints — depend on provider registry pattern (NFR-PT-04).
- Realtime WebSocket — depends on FR-CC cancellation primitives.
- Prometheus `/metrics` — depends on NFR-OB structured logging (counters must be reachable; see NFR-OB-05).
- Audio cache — depends on FR-CA model cache pattern.
- Rate limiting — depends on NFR-OB request-id context.
- Multi-replica deploy — would require pulling voice map and model cache out of process; not in scope.

### NFR-EV-02 — Roadmap doc lives in the SRS, not in code
The Roadmap section produced by the Product Owner MUST live in the final SRS. Per-FR-area implementation notes MAY reference the Roadmap; the codebase MUST NOT contain "TODO Roadmap" comments littering source files.

---

## 15. Assumptions

| ID | Assumption | Impact if wrong |
|---|---|---|
| A-N1 | A ≥ 32 GB Apple Silicon host is the primary reference for sizing assumptions. | NFR-SC-04 numbers shift; smaller hosts may not fit Voxtral-4B at cache size 1. |
| A-N2 | Internal LAN deployment is the only operating context this cycle. | NFR-SE auth deferral becomes untenable if scope shifts to public exposure. |
| A-N3 | Operators are willing and able to measure baseline performance and record it in `docs/perf/baseline.md`. | NFR-PF-01 acceptance becomes unverifiable; falls back to qualitative "no apparent regression". |
| A-N4 | Container deploys can mount the voice map and reference audio as a volume. | NFR-OP-02 partial; voice map changes would require rebuild. |
| A-N5 | All currently-registered providers can be wrapped to expose a `supports_devices` capability. | RISK-1 materializes; auto-selection logic uses a hardcoded device→provider table. |
| A-N6 *(cycle 2)* | Reference Apple Silicon (M1 Max) host with mlx_audio provider achieves p95 TTFB ≤ 250 ms on the `fast` preset and ≤ 800 ms on `balanced`. | NFR-PR-01 documentation needs softening; "fast" preset may require a distilled model checkpoint to hit the target. |
| A-N7 *(cycle 2)* | `soundfile` (already a project dep) reliably encodes wav24 (PCM_24) and FLAC on Linux x86_64 + macOS arm64 with stock libsndfile. | NFR-FMT-02 fallback to a different encoder. |
| A-N8 *(cycle 2)* | Per-provider `synthesize_chunks` accepts the standard knob set (`temperature`, `top_p`, etc.) without invasive provider refactor; soft-ignore is achievable via signature inspection. | RISK-PR-2 — provider may need a `declared_knobs` set as well as `supported_response_formats`. |
| A-N9 *(cycle 2)* | Postproc cost on the reference host fits the per-second budgets in NFR-PP-01 with default settings. | Soft doc only; operators measure on their own hardware via S-021 scripts. |

---

## 16. Open Questions

| ID | Question | Impact scope |
|---|---|---|
| OQ-N1 | Should the 80% coverage gate apply on day one of the cycle or ratchet from the current level? | NFR-MT-02 CI configuration timing. |
| OQ-N2 | Specific SDK version to pin for the OpenAI compatibility test matrix? | NFR-PT-03 test fixture. |
| OQ-N3 | Is the Dockerfile expected to ship a CUDA variant, or only CPU/MPS? | NFR-OP-02 image strategy; NFR-PT-01 testing breadth. |
| OQ-N4 | License audit (`pip-licenses`) — required output or merely permitted? | NFR-CP-02 CI scope. |
| OQ-N5 | Where does the perf baseline file live and who owns updating it? | NFR-PF-01 maintainability over time. |
| ~~CY2-NFR-OQ-1~~ | All cycle-2 NFR OQs RESOLVED in TW challenge rounds 1-3 (TTFB targets, postproc/format budgets, hot-reload SLO, file-permission posture, byte-identity codification, migration scope). See cross-cutting trade-offs table for trail. | — |

---

## 17. Traceability Summary

| NFR area | Primary source | FR linkage |
|---|---|---|
| Performance (NFR-PF) | Round 1 (no regression target) | FR-CC-02, FR-EP-05 |
| Scalability (NFR-SC) | Round 1 (single-user / few callers) | FR-CC-01, FR-CA-01 |
| Reliability (NFR-RL) | Round 2 (container restart, no SLA) | FR-HL-02..04 |
| Security (NFR-SE) | Round 1 (LAN-only) + request §3 inline upload | FR-VC-03, FR-QG-01 |
| Privacy (NFR-PV) | Round 2 (DEBUG-snippet logging; biometric posture) | FR-VC-04, FR-OB-02 |
| Observability (NFR-OB) | request §4 | FR-OB-01..03 |
| Maintainability (NFR-MT) | request §3 items 14–16 | FR-QG-01..03 |
| Operability (NFR-OP) | request §5 (constraints) | FR-HL-01..04, FR-CF-01..02, FR-VM-02 |
| Portability (NFR-PT) | Round 2 (macOS + Linux x86_64 container) | FR-OA-01..03 |
| Compliance (NFR-CP) | Round 2 (biometric documentation only) | — |
| **Preset performance (NFR-PR)** *cycle 2* | TW Round 1 (TTFB targets + hot-reload SLO) | FR-PR-05, FR-PR-06, FR-PR-11 |
| **Post-processing (NFR-PP)** *cycle 2* | TW Round 1 (per-step budgets) + Round 2 (buffer lifetime) | FR-PP-01..08 |
| **Format extension (NFR-FMT)** *cycle 2* | TW Round 1 (conversion cost) + Round 3 (Content-Type, portability matrix) | FR-FMT-01..07 |
| **Cycle-2 Security additions (NFR-SE-09/10)** | TW Round 2 (file permissions + validating reload) | FR-PR-02, FR-PR-11 |
| **Cycle-2 Operability (NFR-OP-06/07)** | TW Round 3 (per-synthesis observability + migration-out-of-scope) | FR-PR-06, FR-PR-09, FR-PP-06, FR-PP-07 |
| **Cycle-2 Portability (NFR-PT-05/06)** | TW Round 3 (S-018 byte-identity NFR + provider capability declaration) | FR-PR-10, FR-FMT-02 |
| **Cycle-2 Compliance (NFR-CP-03)** | TW Round 2 (denoise privacy posture) | FR-PP-05 |
| Evolution (NFR-EV) | request §6 Roadmap | — |