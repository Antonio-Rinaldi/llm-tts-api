# llm-tts-api — Improvement Request

**Date:** 2026-05-17
**Reference quality bar:** `/Volumes/Coding/Projects/Applications/epub/llm-image-api`
**Target codebase:** `/Volumes/Coding/Projects/Applications/epub/llm-tts-api`
**Mode:** Incremental hardening + selected new features. **Not a rewrite.**

---

## 1. Background

`llm-tts-api` is a well-architected TTS service (FastAPI + pluggable provider registry, MLX-audio default, Voxtral and vLLM-Omni alternates) with several distinctive strengths: voice cloning via `ref_audio`/`ref_text`, semantic chunking, per-chunk RMS normalization, multilingual number/date expansion, streaming WAV responses, OpenAI-compatible error envelope, strict startup config validation.

Its sibling project `llm-image-api` has reached a more mature engineering baseline: automatic hardware/device detection with dtype selection, async-correct concurrency, lifespan-managed singletons via `app.state`, lock-free `/health` vs `/ready` split, LRU model cache, structured error codes, strict mypy + ruff CI, 80% coverage gate, hot-reload config catalog.

The user wants `llm-tts-api` to reach the same engineering baseline while preserving its TTS-specific features, and additionally explore feature improvements.

## 2. Goals

### G1 — Close the engineering quality gap with llm-image-api (PRIMARY)
Bring infrastructure, observability, testing, packaging, and concurrency patterns to parity with `llm-image-api`. This is the top priority if trade-offs arise.

### G2 — Introduce automatic hardware/architecture detection
Detect device (`mps` / `cuda` / `cpu`) and dtype at startup, and use that signal to select **both** the inference device **and** the most appropriate TTS provider automatically. With auto-detection in place, there is no need for a hardcoded provider default — the env var `TTS_PROVIDER` becomes an override, not a default.

Auto-detection rules (initial):
- Apple Silicon (`mps`) → MLX-audio family (Qwen/Voxtral)
- NVIDIA CUDA → CUDA-capable provider (vLLM-Omni or torch-based)
- CPU only → degraded CPU-compatible provider (or a clear startup error if none is viable)

### G3 — Architectural pattern: rich endpoints + OpenAI thin adapters
Introduce a new, richer endpoint surface that exposes the service's full capabilities (semantic chunking knobs, normalization controls, voice cloning parameters, streaming chunks with metadata, etc.). The existing OpenAI-shaped endpoints (`/v1/audio/speech`, etc.) remain stable for client compatibility but are reimplemented as **thin translators** that call the richer endpoints under the hood.

### G4 — Preserve TTS-specific strengths
The following must survive unchanged in spirit (refactored only where they benefit from G1/G2/G3 patterns):
- Voice cloning with `ref_audio_path` + `ref_text`
- Semantic chunking (`split_text_semantic`) with per-voice `max_sentences_per_chunk`
- Per-chunk and final RMS normalization (`normalize_wav_rms`) with per-voice `target_db`
- Pluggable provider registry (MLX-audio, Voxtral, vLLM-Omni)
- Provider-agnostic voice argument mapping (`voice_args.py`)
- Multilingual number/date expansion (`text_preprocessing.py`)
- Streaming audio responses (`stream=True`)
- OpenAI-compatible structured error envelope
- Strict, fail-fast startup config validation

### G5 — Surface feature improvements as analysis output
Beyond G1–G4, produce a prioritized list of feature improvements and new capabilities the service could grow into. Deliver as a roadmap section, not as in-cycle implementation work.

## 3. Explicit parity targets (gap-analysis checklist)

Drawn from the survey of `llm-image-api`:

1. Hardware auto-detection module (`engine/device.py` equivalent) — MPS → CUDA → CPU with env override.
2. Device-aware dtype selection with env override.
3. Async-correct concurrency: replace `threading.Semaphore` blocking the event loop with `asyncio.Semaphore`; run sync provider calls via `anyio.to_thread`; per-engine `asyncio.Lock` for single-worker serialization where required.
4. LRU model/pipeline cache with configurable size; validate model id and file existence before cache mutation.
5. Lifespan-managed singletons via `app.state.*` (replace ad-hoc `@lru_cache` on factories where it leaks).
6. Startup warmup with timeout; warmup failures fail readiness, not liveness.
7. Lock-free `/health` (always 200, returns device/dtype/version/queue_depth) vs `/ready` (503 during warmup or shutdown drain).
8. Graceful shutdown drain with timeout.
9. Structured error envelope with typed error codes (extend the existing OpenAI envelope with internal codes).
10. Request/correlation IDs in all log lines (middleware-injected); structured logging format.
11. Response metadata via headers (`X-Request-ID`, `X-Provider`, `X-Device`, `X-Chunks`, `X-Duration-Ms`, etc.) for the rich endpoints; OpenAI adapters keep the OpenAI shape.
12. Inference timeout enforcement (`asyncio.wait_for`); queue-full returns 503 with structured code.
13. Pydantic `extra="forbid"` on all request models; explicit response models.
14. Strict mypy across `src/`; ruff (E, F, I, UP, B, SIM) in CI.
15. 80% coverage threshold enforced in CI; tests for streaming, concurrency-limit enforcement, normalization end-to-end, provider error handling, large-input chunking boundaries.
16. CI workflow mirroring `llm-image-api`: `uv sync --frozen --group dev`, ruff check + format check, mypy, pytest with coverage threshold, `pip-audit`.
17. Config hot-reload for voice map (`watchfiles`-driven, similar to llm-image-api's config catalog) so new voices can be added without restart.
18. Memory/resource sanity check at startup (psutil) — soft warning, not a hard gate.
19. Examples and docs updated; class/sequence diagrams refreshed where structure changes.

## 4. Non-functional emphasis

- **Latency:** preserve current speech-endpoint latency on Apple Silicon; do not regress.
- **Throughput:** concurrency model must allow the configured `TTS_MAX_CONCURRENT_REQUESTS` to actually run concurrently (current `threading.Semaphore` on the event loop limits this).
- **Resource safety:** no event-loop blocking; graceful drain on SIGTERM (container deploy is first-class).
- **Observability:** every request traceable via X-Request-ID end-to-end; failures categorized by typed code.
- **Security:** no auth added in this cycle (open local service), but request-size limits, content-type strictness, and path validation for any new voice-upload-adjacent code paths must be hardened.
- **Portability:** Apple Silicon is primary; CUDA and CPU paths must be exercised by CI smoke (or at minimum unit-mocked detection tests).

## 5. Constraints

- **No new external services** (no Redis, S3, Prometheus push gateway, message broker).
- **Container deploy stays a first-class target** — Dockerfile must remain functional and updated.
- **OpenAI client compatibility** must not break — existing `/v1/audio/speech` request/response stays compatible (becomes a thin adapter, see G3).
- **MLX-audio remains the primary path on Apple Silicon** — performance must not regress.
- **Incremental** — no rewrite; refactor in-place behind existing module boundaries where possible.

## 6. Explicitly OUT of scope (kept as roadmap)

The following currently-stubbed endpoints stay 501 in this cycle and are documented as a forward-looking roadmap, not implementation work:
- STT: `/v1/audio/transcriptions`, `/v1/audio/translations`
- Voice enrollment: `/v1/audio/voices`, `/v1/audio/voice_consents/*`
- Chat: `/v1/chat/completions`, `/v1/chat/models`
- Realtime WebSocket: `/v1/realtime/*`

The SRS must include a **Roadmap** section that captures these (with rough sequencing and dependencies) so they can be picked up in a follow-up cycle without rediscovery.

## 7. Forward-looking analysis (G5 deliverable)

In addition to the formal SRS, produce a prioritized list of feature improvements. Candidates to evaluate (non-exhaustive):
- Content-addressable audio cache (hash of normalized text + voice + params → cached WAV) to skip re-synthesis of identical requests.
- True async TTS providers where the underlying library supports it.
- Token/sentence-level streaming (return audio chunks as they synthesize rather than after each full chunk).
- Per-client rate limiting / quota (without external services — in-process token bucket).
- Prometheus-style `/metrics` endpoint (in-process, no push gateway).
- Voice map hot-reload (also listed in parity — overlap is intentional).
- Voice preview endpoint (synth a fixed sample sentence per voice for UI catalog).
- SSML or markup support for prosody control.
- Better large-input handling: parallel chunk synthesis with order-preserving join.
- Optional output formats (mp3, opus) via in-memory encoding.
- Health endpoint that reports last-N synthesis latencies (p50/p95) for ops visibility.
- Test fixtures that exercise CUDA and CPU code paths via monkeypatched torch availability (mirror llm-image-api's `test_device.py`).

For each, the analysis should state: value, effort, risk, and dependency on the parity work.

## 8. Success criteria

The improvement cycle is complete when:
- All G1 parity items in §3 are implemented and verifiable.
- G2 hardware auto-detection picks device and provider with documented fallback behavior and is covered by tests.
- G3 rich endpoints exist and OpenAI adapters are thin translators with no duplicate business logic.
- G4 features still work identically from a user-facing perspective (UAT coverage required).
- CI pipeline is green with ≥80% coverage, strict mypy, ruff clean, pip-audit clean.
- The roadmap section (§6 and §7) is captured in the SRS for follow-up cycles.