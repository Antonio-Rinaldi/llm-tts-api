# llm-tts-api — Forward-Looking Improvement Analysis

**Status:** Draft
**Date:** 2026-05-17
**Companion to:** `software-spec.md` (§11 Roadmap)

## Purpose

This document is the **G5 deliverable** of the improvement request: a prioritized list of feature improvements and new capabilities `llm-tts-api` could grow into **after** the parity cycle completes. The SRS already captures what's IN-cycle; this document scores what could come NEXT.

## Scoring framework

Each candidate is scored on four axes:

- **Value** (1–5): user/operator/business value. 5 = transformative; 1 = nice-to-have.
- **Effort** (1–5): implementation cost. 5 = multi-sprint; 1 = day's work.
- **Risk** (1–5): technical or organizational risk. 5 = could destabilize the service; 1 = isolated.
- **Depends on parity work?**: explicit dependency on FR/NFR delivered this cycle.

Priority tier is derived from `(Value − Risk) − (Effort × 0.4)` rounded into bands (P0 highest, P3 lowest), then adjusted by judgement notes.

---

## Tier P0 — Highest leverage, recommend next cycle

> **Note:** Voice enrollment + minimal consent attestation has been **pulled into this cycle** (per OQ-3 decision). Formal signed-consent records remain a Roadmap item — see Tier P2 below.

### 1. Prometheus `/metrics` endpoint
- **Surface:** `GET /metrics` (Prometheus text format, in-process).
- **Value:** 4 — operator visibility (request rates, latency histograms, queue depth, model load count, error counts by code).
- **Effort:** 2 — single dependency (`prometheus-client`), in-process counters/histograms wired into existing middleware.
- **Risk:** 1 — additive, no behavioral change.
- **Depends on:** NFR-OB structured logging (request id context); NFR-OB-05 notes counters must be reachable.
- **Why P0:** Best effort/value ratio in the entire list. Closes a real operability gap left by this cycle.

### 2. Per-client (or global) rate limiting
- **Surface:** middleware; in-process token bucket; configurable per-route limits.
- **Value:** 4 — defense-in-depth for the eventual move beyond LAN-only deploy; mitigates RISK-4 (inline upload DoS).
- **Effort:** 2 — single-process token bucket; no external services.
- **Risk:** 2 — false-positive rejections under burst patterns; needs careful default.
- **Depends on:** NFR-OB request-id context.
- **Why P0:** Cheap, defensive, and unlocks deployment beyond LAN.

---

## Tier P1 — High value, plan within 2 cycles

### 4. Content-addressable audio cache
- **Surface:** internal layer between rich endpoint and provider; cache key = hash(normalized_text + voice_ref + provider + model + params).
- **Value:** 4 — skip re-synthesis of identical requests; meaningful win for audiobook-generator-style callers that retry/reprocess.
- **Effort:** 3 — needs an in-process LRU bytes cache (configurable size + TTL), cache-key canonicalization (text normalization must be deterministic), cache-hit/miss telemetry.
- **Risk:** 2 — must invalidate on voice map changes for affected voices; staleness bugs possible.
- **Depends on:** FR-CA model cache pattern; FR-VM voice map lifecycle (invalidation hooks).
- **Edge:** No external services constraint means in-process only.

### 5. STT — `/v1/audio/transcriptions` and `/v1/audio/translations`
- **Surface:** mirror OpenAI shape; new STT provider class.
- **Value:** 4 — completes the speech round-trip; turns the service into a general audio API.
- **Effort:** 5 — new model class, new providers (e.g. Whisper-MLX), new test coverage, OpenAPI extension.
- **Risk:** 3 — STT models are large; sharing the LRU model cache with TTS contends for memory.
- **Depends on:** FR-HW provider registry pattern; NFR-SC-04 sizing assumptions revisit.
- **Note:** If pursued, evaluate splitting model cache into per-domain caches (TTS-cache + STT-cache).

### 6. MP3 / Opus / FLAC output formats
- **Surface:** `response_format` accepts new values on the rich endpoint and OpenAI adapter.
- **Value:** 3 — smaller payloads, browser compatibility, OpenAI parity (their SDK supports them).
- **Effort:** 2 — wrap an encoder (e.g. `pydub`/`ffmpeg`/`soundfile`-with-codecs); CI matrix gains an ffmpeg requirement.
- **Risk:** 1 — additive; existing WAV path unchanged.
- **Depends on:** none new — leverages FR-EP-02 `response_format` field.

### 7. Parallel chunk synthesis with order-preserving join
- **Surface:** internal; controlled by a `parallel_chunks: bool` knob (default false this tier).
- **Value:** 3 — large speedup for long passages on hosts with multi-stream GPU/CPU capacity; concrete win for audiobook generation.
- **Effort:** 3 — chunk synthesis becomes `asyncio.gather`-able; per-chunk normalization stays per-chunk; join preserves order.
- **Risk:** 3 — interacts with FR-CC concurrency ceiling and per-engine locks; risk of dropping the device into queueing.
- **Depends on:** FR-CC async model; FR-CA model cache safety under concurrent reads.

---

## Tier P2 — Useful, opportunistic

### 8. Voice preview endpoint
- **Surface:** `GET /v1/voices/{id}/preview` returns a short canned-sentence sample for catalog/UI use.
- **Value:** 3 — UI-builder ergonomics; helps users pick a voice without trial requests.
- **Effort:** 2 — backed by audio cache (item 4); first call synthesizes, subsequent calls hit cache.
- **Risk:** 1 — read-only.
- **Depends on:** ideally item 4; works without it but burns inference per call.

### 9. SSML / lightweight prosody markup
- **Surface:** `input` accepts an optional subset of SSML (`<break time="500ms"/>`, `<emphasis>`, `<say-as interpret-as="number">`).
- **Value:** 3 — quality lift for authored content; partial OpenAI compat (they don't expose SSML).
- **Effort:** 4 — parser + provider-specific translation (each engine handles prosody differently); some markup may be untranslatable per provider → spec a "best-effort" doctrine.
- **Risk:** 3 — feature parity across providers will be uneven; user expectations management.
- **Depends on:** none structural; lives in `text_preprocessing.py`.

### 10. Last-N latency observability on `/health`
- **Surface:** `/health` response gains a `recent` block: `{ "p50_ms": ..., "p95_ms": ..., "window_secs": 60 }`.
- **Value:** 2 — gives operators a coarse pulse without `/metrics`.
- **Effort:** 1 — in-process ring buffer.
- **Risk:** 1.
- **Depends on:** none.

### 11. Per-provider sub-cache for STT vs TTS
- **Surface:** internal; partitions LRU model cache by domain.
- **Value:** 2 — relevant only if item 5 (STT) lands; prevents TTS evictions when STT is in use.
- **Effort:** 2.
- **Risk:** 1.
- **Depends on:** item 5.

### 12. Formal signed-consent records
- **Surface:** `POST /v1/audio/voice_consents/*`; cryptographically signed consent artifacts attached to voice records.
- **Value:** 3 — closes the compliance loop fully; needed only if the service moves beyond LAN trust boundary or if legal opens a record.
- **Effort:** 3 — signature scheme decision (PGP? JWT?), storage extension, verification at create time.
- **Risk:** 2 — scheme choice longevity.
- **Depends on:** in-cycle FR-VS CRUD; future auth.

### 13. Docker image: CUDA variant (now done in cycle as OQ-5)
- **Surface:** second Dockerfile (`Dockerfile.cuda`) or build-arg-gated single Dockerfile.
- **Value:** 3 — production deploys on Linux GPU hosts become first-class.
- **Effort:** 2 — base image swap; CUDA torch wheel; CI matrix expansion.
- **Risk:** 2 — large image; CI runtime cost.
- **Depends on:** FR-HW CUDA path validation in CI (this cycle).

---

## Tier P3 — Optional / niche

### 13. Realtime bidirectional WebSocket
- **Surface:** `/v1/realtime/*` (OpenAI realtime-compatible).
- **Value:** 4 (if you have realtime use cases) / 1 (if not).
- **Effort:** 5 — substantial protocol work, session state, audio framing, cancellation, partial-input handling.
- **Risk:** 4 — protocol churn; OpenAI realtime is still evolving.
- **Depends on:** FR-CC cancellation primitives; item 7 (parallel chunks) for any hope of low-latency partial output.
- **Why P3:** Only pursue if a concrete consumer (a voice agent, e.g.) needs it. Otherwise the cost dwarfs the value for an internal LAN service.

### 14. Chat completions `/v1/chat/*`
- **Surface:** OpenAI chat shape, presumably mapping chat → narration TTS.
- **Value:** 2 — possibly out-of-charter for a TTS-only service.
- **Effort:** 3.
- **Risk:** 3 — scope creep; muddies the service's identity.
- **Recommendation:** Defer until there's a concrete user; consider whether this belongs in a sibling chat service instead.

### 15. Multi-replica deploy
- **Surface:** infrastructure + code changes: voice map externalized (shared FS or registry), audio cache externalized (Redis), metrics externalized.
- **Value:** 2 (LAN) / 5 (if scope shifts to public).
- **Effort:** 5 — violates current "no external services" constraint (NFR-SC-01).
- **Risk:** 4 — sweeping architectural shift.
- **Depends on:** essentially everything; this is a re-architecture, not a feature.
- **Recommendation:** Only if scope shifts from internal LAN to public/shared service.

### 16. License audit (`pip-licenses`) in CI
- **Surface:** CI step + `docs/licenses.md`.
- **Value:** 2 — compliance hygiene.
- **Effort:** 1.
- **Risk:** 1.
- **Depends on:** OQ-6 decision.

---

## Summary Tier Table

| Tier | Items | Net theme |
|---|---|---|
| **P0** | `/metrics`; rate limiting | Operability + abuse protection. Voice enrollment now in-cycle (OQ-3). |
| **P1** | Audio cache; STT; MP3/Opus; parallel chunks | Capability expansion; meaningful perf and feature wins. |
| **P2** | Voice preview; SSML; latency on `/health`; per-domain cache; CUDA Docker image | Quality-of-life and ecosystem polish. |
| **P3** | Realtime WS; chat; multi-replica; license audit | Pursue only on concrete trigger or scope change. |

---

## Recommended next-cycle composition

A focused 1-sprint next cycle that builds directly on this one:

1. **Prometheus `/metrics`** (P0 #1) — cheapest operability win.
2. **Rate limiting** (P0 #2) — defense-in-depth; protects the voice-CRUD upload path now that it exists.

Followed by a higher-value next-cycle: **audio cache (P1 #4) + MP3/Opus (P1 #6)** — both pair well with the audiobook-generator use case (same input retried; smaller payloads for distribution).

Further out: **STT (P1 #5)** and **formal signed-consent (P2 #12)** when there's concrete demand.

---

## Items intentionally NOT in scope here

- **Re-platforming away from FastAPI** — no signal this is warranted.
- **GPU model parallelism / quantization beyond what `mlx-audio` already does** — owned by the model vendors, not this service.
- **Streaming end-to-end realtime synthesis** (token-by-token streaming) — partially covered by item 7 (parallel chunks); a full token-stream implementation depends on provider APIs that don't yet exist for the registered providers.