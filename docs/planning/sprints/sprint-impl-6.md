# Sprint 6 — Implementation Notes

Per-story implementation notes assembled by the sprint-coordinator after each story
completes in its isolated worktree. Companion to `sprint-6.md`.

## Summary

| Story | Type | Status | Worktree branch |
|---|---|---|---|
| S-019 | Technical | READY-FOR-REVIEW | sprint-6-S-019 (merged) |
| S-020 | Technical | READY-FOR-REVIEW | sprint-6-S-020 (merged) |
| S-021 | Technical | READY-FOR-REVIEW | sprint-6-S-021 (merged) |
| S-026 | Technical | PLANNED | sprint-6-S-026 (pending — Step 2) |

Sprint 6 status: Step 1 complete (S-019/S-020/S-021 merged, 380 tests passing); Step 2 (S-026 dedup refactor) pending.

---


# S-019 (sprint-6 branch merged)


**Branch:** sprint-6-S-019 (worktree `.worktrees/sprint-6/S-019`)
**Status:** READY-FOR-REVIEW
**Date:** 2026-05-19

## Summary

Sprint-5 left the docs/diagrams/OpenAPI surface stale (the rich endpoint,
voice CRUD, voice store, seed ingestion, hardware auto-selection, and the
typed error taxonomy all post-dated the existing docs). S-019 refreshes
every doc surface against current `master` and adds a programmatic
cross-reference test so future env-var / error-code additions can't drift
silently.

No source changes.

## Deliverables per task

### T1 — README.md refresh

Rewrote `README.md` (≈ 380 lines, was 283). New / expanded sections:

- **Hardware Auto-Detection (S-006)** — capability matrix with
  `supports_devices` per provider + override validation rules.
- **Full env-var inventory** — every var consumed by
  `Settings.__post_init__` (26+ entries), grouped by domain (app
  identity, provider routing, STT, limits/runtime, device, voice store,
  seed map, test bypass).
- **Rich endpoint** — `POST /v1/tts/synthesize` request schema +
  streaming/trailers semantics.
- **Response header inventory** — full table per SRS §5 C-2 including
  trailer behaviour for the streaming variant.
- **Voice CRUD endpoints** — multipart contract, ID rules, content-type
  + magic-bytes validation (NFR-SE-01..02), consent attestation.
- **Seed voice ingestion (S-011)** — `TTS_VOICE_MAP_FILE`, watchfiles
  ≤ 2 s reload, polling fallback knob.
- **Storage-backend matrix** — FS / Postgres / S3 with required extras.
- **Error taxonomy table** — every `(type, code)` pair from
  `errors.ERROR_CODES`.
- **Voice biometric notice** — verbatim from SRS / NFR-CP-01 and
  NFR-PV-04 paragraphs (quoted as the compliance/privacy notice).
- **Sizing recommendations** — three host classes resolving SRS §5 C-1.
- **Performance baseline** — link to `docs/perf/baseline.md`.
- **Examples** — rich, OpenAI-compatible, and voice CRUD POST.
- **Project layout + quality gates + project documents**.

### T2 — Class diagrams

- **`docs/diagrams/class/voice-store.md`** (NEW). Captures
  `VoiceMetadataRepository` + `VoiceBlobRepository` Protocols, the four
  backend classes (`FsJsonMetadataRepository`, `FsBlobRepository`,
  `PostgresMetadataRepository`, `S3BlobRepository`), `VoiceRecord`, the
  three error classes, and both producers (seed ingestor + CRUD router).
- **`overview.md`** (REWRITTEN). Reflects the post-S-017 unified pipeline:
  both `routers/synthesize.py` and `routers/audio.py` delegate to
  `services/synthesize_service.synthesize_core` (BR-9). Records the
  rich-only header strip in `routers/audio._RICH_ONLY_HEADERS`.
- **`config-and-schemas.md`** (REWRITTEN). Inventories every `Settings`
  field, `SynthesizeRequest` + `SpeechRequest` + `VoiceCreate/Update/
  Response/Summary/ListResponse`, the `ERROR_CODES` constant, and the
  envelope/handler split.
- **`providers.md`** (APPENDED). Added the auto-selection capability
  matrix (provider × device × override semantics) at the bottom; the
  existing strategy diagram is unchanged.

### T3 — Sequence diagrams

- **`startup.md`** (REWRITTEN). New lifespan flow including
  `build_default_dependencies`, S-006 `select_provider`, seed ingestion
  `ingest_once` + `watch_and_ingest`, the `LLM_TTS_API_TEST_NO_LIFESPAN`
  bypass branch, the `_drain_concurrency` shutdown step.
- **`health-and-ready.md`** (REWRITTEN). Lock-free `/health` reading
  `app.state` defensively + reason-aware `/ready` (`warming_up` /
  `draining`).
- **`create-speech.md`** (REWRITTEN). Now diagrams the post-S-017 thin
  translator: `_translate_openai_request` → `synthesize_core` → strip
  `_RICH_ONLY_HEADERS`.
- **`synthesize-rich.md`** (NEW). Buffered + streamed variants with
  `queue_full` pre-check and `_TrailerStreamingResponse` (trailers
  honored on `TE: trailers`, omitted otherwise per G-3).
- **`voice-crud.md`** (NEW). Five flows (POST / GET list / GET one / GET
  audio / PUT / DELETE) including POST rollback semantics and PUT
  atomic-replace order.
- **`voice-seed-ingestion.md`** (NEW). Startup ingest + watchfiles
  hot-reload + shutdown cancel; documents the FR-VM-05 empty-map valid
  case and the polling fallback knob.

### T4 — OpenAPI spec

Rewrote `docs/openapi/openapi.yaml` (≈ 540 lines). New coverage:

- `/v1/tts/synthesize` with full `SynthesizeRequest` schema and the rich
  response-header set.
- `/v1/tts/voices` (GET list, POST multipart create) — `VoiceCreate`,
  `VoiceListResponse`.
- `/v1/tts/voices/{voice_id}` (GET, PUT, DELETE) — `VoiceResponse`,
  `VoiceUpdate`, `VoiceNotFound` response.
- `/v1/tts/voices/{voice_id}/audio` (GET) — blob download with
  `X-Voice-Id` / `X-Voice-Source` / `X-Content-Sha256` headers.
- Rewrote `ErrorDetail` to match the typed taxonomy
  (`validation_error` / `voice_error` / `provider_error` /
  `capacity_error` / `internal_error`).
- Kept all 501-stubbed surfaces (chat, realtime, transcriptions,
  translations, voice_consents) and `/v1/audio/voices` placeholder.
- Added explicit response references for `CapacityError` (429 / 503 /
  504) and `ProviderError` (502).
- New `VoiceId` path parameter with `^[a-z0-9_-]{1,64}$` pattern.
- `HealthResponse` schema reflects the actual `/health` body fields.

YAML syntax validated via `python -c "import yaml;
yaml.safe_load(open('docs/openapi/openapi.yaml'))"` — passes.

### T5 — `tests/test_docs_inventory.py` (NEW)

Three assertions, all pure unit:

(a) `test_every_settings_env_var_appears_in_readme` — walks `config.py`
    AST for `os.environ.get/getenv` literal-string lookups AND helper
    calls (`self._load_int("X", ...)`, `self._load_enum("X", ...)`,
    `self._load_optional_timeout("X")`, `self._load_preload_models("X")`)
    AND `default_env=/allowed_env=` kwargs. Discovers 32 env-var names;
    asserts each appears verbatim in `README.md`. Includes sanity asserts
    on representative names so the walker can't silently drift.

(b) `test_every_error_taxonomy_pair_appears_in_readme` — imports
    `ERROR_CODES` directly from `errors.py` and asserts every `(type,
    code)` pair shows up in the README. Reports specific missing pairs
    on failure for debuggability.

(c) `test_every_router_prefix_appears_in_openapi` — sets
    `LLM_TTS_API_TEST_NO_LIFESPAN=1`, builds the FastAPI app, walks
    `app.routes`, and confirms each route path is documented in the
    OpenAPI `paths:` map (either exact match or a prefix-family
    sibling, which handles `{voice_id}` templated routes).

## Decisions / notes

- **Biometric notice quoting.** Per the task, used the SRS NFR-CP-01 +
  NFR-PV-04 wording verbatim. The two paragraphs are quoted together
  in the README's "Voice biometric notice" section under a single
  blockquote. No paraphrase was needed.
- **Diagram split.** Mirrored llm-image-api's per-component split:
  one concept per file, narrative + participants + Mermaid + notes.
  Did not collapse into a single mega-diagram.
- **OpenAPI shape.** Adopted llm-image-api's pattern: one
  `openapi.yaml` (no per-endpoint split) with components reused across
  paths. Did not introduce a new per-surface layout.
- **Test scope.** The inventory test is intentionally lightweight: pure
  unit, no fixtures, AST-based discovery so it self-updates when new
  env vars land. Sanity asserts protect against silent walker drift.

## Gates

```
uv run ruff check .                  → All checks passed
uv run ruff format --check .         → 92 files already formatted
uv run mypy --strict src/            → Success: no issues found in 52 source files
uv run pytest                        → 378 passed, 2 skipped, 3 deselected, 1 xfailed in 5.24s
uv run pip-audit                     → No known vulnerabilities found
```

Test delta: **375 → 378 passed** (+3 from T5 inventory tests).

---

# S-020 (sprint-6 branch merged)


## Scope delivered

All four tasks of S-020 in sprint-6:

- **T1**: `Dockerfile` (default, CPU/MPS-friendly) refreshed from scratch as a
  multi-stage build (builder → runtime). Base pinned by multi-arch manifest
  digest `python:3.13-slim@sha256:dc1546ee…1232f`. Non-root `app:app` (uid/gid
  1000). Compiler toolchain (`build-essential`, `libsndfile1-dev`) lives only
  in the builder stage; runtime gets `libsndfile1` + `tini` + `curl` for the
  healthcheck. Defaults wire `TTS_VOICE_MAP_FILE=/app/config/voice_map.container.json`,
  `TTS_VOICE_STORE_DIR=/var/lib/llm-tts-api/voices`, and
  `TTS_SHUTDOWN_DRAIN_SECONDS=30`. Both paths are declared `VOLUME`s.
  `HEALTHCHECK` calls `/health`; `EXPOSE 8010`. Entrypoint is
  `tini -- uvicorn …:8010 --no-use-colors` so SIGTERM is forwarded to the
  lifespan shutdown handler (S-010 `_drain_concurrency` in
  `src/llm_tts_api/main.py:78`) and the drain window is honoured before exit.

- **T2**: `Dockerfile.cuda` (GPU variant) added. Base pinned by digest
  `nvidia/cuda:12.9.1-cudnn-runtime-ubuntu24.04@sha256:d02c4310…0802`.
  Builder installs CUDA torch from `https://download.pytorch.org/whl/cu121`
  first, then the project, so the resolver settles on the GPU wheel. Same
  env-var / volume / healthcheck / drain contract as the default image plus
  `TTS_DEVICE=cuda` and `NVIDIA_VISIBLE_DEVICES=all`, so `detect_device()`
  (S-005) selects GPU without host overrides. If no CUDA-capable provider is
  available the runtime exits via the existing
  `provider_error.no_viable_provider` path (S-006 — no new code needed; the
  image just defaults the variable).

- **T3**: `.github/workflows/docker.yml` — matrix build of both variants on
  push + PR. Each variant: `docker build` with GHA layer cache; smoke test
  (`docker run -d`, poll `/health` for 60 s, `docker stop -t (drain+5)`,
  assert exit code 0 and elapsed ≤ stop budget). The CUDA job overrides
  `TTS_DEVICE=cpu` so the smoke runs on GitHub-hosted runners (no GPU)
  without altering the boot contract — real GPU validation is operator-
  driven on a CUDA host. Images are tagged `:ci` / `:ci-cuda` and never
  pushed to a registry.

- **T4**: `.dockerignore` excludes `.git/`, `.worktrees/`, `tests/`,
  `docs/`, `docs/planning/`, `.pending/`, `var/`, `voices/`,
  `resources/`, local env files, caches, build artefacts, OS junk.

## Verification

Local quality gates (run from this worktree):

```
uv run ruff check src/ tests/ scripts/        # All checks passed
uv run ruff format --check src/ tests/ scripts/   # 91 files already formatted
uv run mypy src/                              # no issues in 52 source files
uv run pytest                                 # 375 passed, 2 skipped, 1 xfailed
uv run pip-audit --skip-editable              # No known vulnerabilities
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/docker.yml'))"
                                              # YAML parses, jobs=['build-and-smoke']
```

`docker build` was NOT run locally — the Docker daemon is not running on
this host, so the actual image build + smoke run is verified in CI by the
new workflow. The CI job IS the gate (UAT-QG-05); both Dockerfiles have
been checked for syntax and structure manually.

**Image size** will be recorded by the workflow's "Record image size" step
on first successful CI run; the placeholder line in the sprint plan ("record
in PR") is fulfilled by that workflow output, which is intentionally always
emitted (`if: always()`) so the value is visible even on smoke failure.

## Notable design decisions

- **Port 8010**, not the `run()` default of 8000. Rationale: the existing
  `scripts/perf_baseline.py` and the original Dockerfile both use 8010 as
  the documented container port. Changing it would silently break operator
  workflows.
- **Single venv copied between stages** (`/opt/venv`) rather than copying
  site-packages from the system interpreter. Keeps the runtime stage's
  apt footprint minimal and makes the path-pinning (`PATH=/opt/venv/bin:…`)
  exhaustive for both interpreter lookup and console-script discovery.
- **`tini` as PID 1** so SIGTERM is forwarded unchanged to uvicorn — the
  S-010 lifespan handler depends on receiving the signal cleanly to start
  `_drain_concurrency`. Without `tini`, uvicorn-under-bare-sh would lose
  the signal in some shells and the drain would never run.
- **`HEALTHCHECK` start-period** is 60 s on the default image and 120 s on
  the CUDA image — the latter loads CUDA libs at first import which can
  add tens of seconds to cold start. Matches the SRS warmup window.
- **No registry push** — the CI workflow only `load`s into the local
  daemon; tagging-and-pushing is a separate concern (would be a future
  release-pipeline story, not S-020).

## Files touched

- `Dockerfile` — rewritten (multi-stage, non-root, pinned digest, tini,
  healthcheck, volumes, env defaults, drain wiring).
- `Dockerfile.cuda` — new.
- `.dockerignore` — new.
- `.github/workflows/docker.yml` — new; does not modify the existing
  `.github/workflows/ci.yml`.

---

# S-021 (sprint-6 branch merged)


**Status:** READY-FOR-REVIEW
**Sprint:** 6
**Tasks landed:** T1, T2, T3, T4, T5 (per-task notes below)
**Branch:** `sprint-6-S-021` (worktree `.worktrees/sprint-6/S-021`)

## Scope split: operator-driven vs. CI-driven

The story has two flavors of "perf validation":

1. **Strict measurement** — absolute p50/p95/p99 against a real provider on
   Apple Silicon. This is operator-driven, not a unit-test concern. The
   numbers go into `docs/perf/baseline.md`.
2. **Methodology gate** — an in-suite smoke that pins the *shape* of the
   NFR-PF-02 (`/health` responsiveness) and UAT-CC-01 (concurrent
   throughput) invariants against the in-process `FakeTTSProvider`. This
   lives in `tests/test_perf_regression.py` and runs on every commit so
   the perf scenarios stay exercised even when nobody re-runs the
   operator script.

This story lands the **methodology gate + tooling + doc structure**. The
operator-driven absolute numbers stay as `_pending_` rows in
`docs/perf/baseline.md` — Sprint 6 plan §7 Risk row anticipates this and
makes S-021 inherit the obligation rather than block on it.

## T1 + T2 — endpoint coverage in `scripts/perf_baseline.py`

- Added `--endpoint {openai,rich}` flag (default `openai`, matching the
  S-002 anchor exactly so Sprint-1 row stays comparable when captured).
- `--endpoint rich` drives `POST /v1/tts/synthesize` with the same JSON
  body (`SynthesizeRequest` and `SpeechRequest` share `input`/`voice`/
  `model`/`response_format`); both paths funnel into `synthesize_core`
  per S-017, so the wall-clock difference is measurement noise unless one
  of the thin router wrappers regresses.
- Markdown output row now carries the endpoint column so operator pastes
  can distinguish T1 vs T2 measurements in the same baseline.md table.

## T3 — `/health` responsiveness under load (NFR-PF-02 / UAT-CC-02)

`tests/test_perf_regression.py::test_health_p95_under_inflight_synthesis`
mirrors the existing `test_concurrency.py` setup but with:

- 20 `/health` samples (vs 5) so the p95 percentile has more headroom.
- Relaxed 200 ms ceiling instead of the 50 ms NFR budget. The strict
  number is a real-hardware claim; the smoke runs inside TestClient
  which adds non-trivial overhead on slow CI runners. The point is to
  fail loud if `/health` becomes synchronously blocked behind the
  synthesis worker thread, not to certify a number.

## T4 — concurrent throughput within ±20% (UAT-CC-01)

`test_concurrent_throughput_within_band` runs 4 parallel synthesis
requests with `TTS_MAX_CONCURRENT_REQUESTS=2` and asserts wall-clock
lands within a ±20% lower bound + a generous 2.5× upper bound of the
expected `2 * delay`. The lower bound catches "concurrency cap broken
upward" (too many requests admitted); the upper bound catches
"concurrency collapsed to serial" — the regression class we actually
worry about. Different model names per request avoid the per-(provider,
model) lock serializing the requests itself.

## T5 — `docs/perf/baseline.md` post-cycle row

- Split the **Measurements** section into "Sprint 1 anchor (S-002)" and
  "Sprint 6 post-cycle measurement (S-021 T5)" subsections so the
  history is unambiguous.
- Both sections carry `_pending_` operator rows — Sprint-1 was never
  captured and Sprint-6 is the cycle-end re-measurement. The doc tells
  the operator exactly which commands to run.
- New "Methodology drift since S-002" paragraph records the only change
  (the `--endpoint` flag, defaulting to `openai` for back-compat).
- New "In-suite smoke (S-021)" section calls out the relaxed bounds and
  what the smoke does vs does not certify.

## Methodology drift vs S-002

- `scripts/perf_baseline.py` gained `--endpoint`; default unchanged
  (`openai` ⇒ `/v1/audio/speech`). Sprint-1 row stays comparable.
- Reference input, voice, warmup discipline, sample size unchanged.
- Markdown table got an "Endpoint" column — the Sprint-1 row will need
  the column inserted as `openai` retroactively when captured.

## Files touched

- `scripts/perf_baseline.py` — `--endpoint` flag, endpoint path table,
  endpoint column in the printed row.
- `tests/test_perf_regression.py` — NEW; smoke for T3 + T4 invariants.
- `docs/perf/baseline.md` — split Measurements section into Sprint-1 +
  Sprint-6 subsections; methodology-drift note; in-suite smoke note.

## Gates

- `uv run ruff check .` — clean.
- `uv run ruff format --check .` — clean.
- `uv run mypy --strict src/` — clean (52 source files).
- `uv run pytest` — 377 passed, 2 skipped, 3 deselected, 1 xfailed
  (xfail is the pre-existing TestClient-streaming-buffer xfail from
  Sprint 5, called out as expected interaction in Sprint 6 plan §7
  Risk row).
- `uv run pip-audit` — no known vulnerabilities.

## Out-of-scope (left for operator)

- Capturing the actual Sprint-1 anchor row on Apple Silicon hardware.
- Capturing the Sprint-6 post-cycle rows (rich + openai) on the same
  warm-model session.
- Computing the +10% regression verdict from those rows.

The doc structure + script flag + in-suite smoke are sufficient to make
this operator action mechanical; without them the operator would also
have to design the methodology, which was the actual cycle-close risk.

---
