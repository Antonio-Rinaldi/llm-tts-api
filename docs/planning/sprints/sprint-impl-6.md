# Sprint 6 — Implementation Notes

Per-story implementation notes assembled by the sprint-coordinator after each story
completes in its isolated worktree. Companion to `sprint-6.md`.

## Summary

| Story | Type | Status | Worktree branch |
|---|---|---|---|
| S-019 | Technical | READY-FOR-REVIEW | sprint-6-S-019 (merged) |
| S-020 | Technical | READY-FOR-REVIEW | sprint-6-S-020 (merged) |
| S-021 | Technical | READY-FOR-REVIEW | sprint-6-S-021 (merged) |
| S-026 | Technical | READY-FOR-REVIEW | sprint-6-S-026 (merged) |

Sprint 6 status: All stories READY-FOR-REVIEW; pending story + sprint reviews.

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

# S-026 (sprint-6 branch merged — CYCLE-FINAL STORY)

**Branch:** sprint-6-S-026 (merged)
**Status:** READY-FOR-REVIEW


Story: S-026 (sprint-6, Step 2 — terminal).
Refs: NFR-MT-01..04, BR-9, NFR-PT-03.
Baseline (Step-1-end, this worktree): **6323 LOC** in `src/llm_tts_api/` (sum of `wc -l` over `*.py`).

## T1 — Duplication inventory (recorded BEFORE any code change)

Five candidates from the sprint-6 plan were investigated. Each is classified as REAL (consolidation lands) / AS-FOUND (already consolidated by an earlier sprint — no action) / OUT-OF-SCOPE (touching it would break a frozen test or change behavior).

### (a) X-* response header inventory — REAL
- **Where:** `services/synthesize_service.py:402..410` (streaming branch) and `services/synthesize_service.py:451..461` (buffered branch) construct the same 7-key header dict (X-Request-ID, X-Provider, X-Model, X-Device, X-Dtype, X-Voice-Source, X-Voice-Id), with the buffered branch adding X-Chunks + X-Total-Duration-Ms.
- **Mirror in adapter:** `routers/audio.py:51..62` repeats the 8 "rich-only" key names as `_RICH_ONLY_HEADERS` to strip them on the OpenAI path. Today the two sets are kept in sync by reviewer eyeballs.
- **Plan:** single `_build_synthesis_headers(...)` helper + `_RICH_ONLY_HEADER_KEYS` module-level tuple in `synthesize_service.py`; `routers/audio.py` imports the tuple so the stripped set is mechanically derived from the populated set.

### (b) Error envelope construction — MOSTLY-AS-FOUND, one REAL site
- **Already centralized:** `errors.py` exposes factories (`invalid_request`, `voice_error`, `provider_error`, `capacity_error`, `internal_error`, `not_implemented`). Every router/service call site uses these factories — single envelope shape across the API.
- **The one exception:** `routers/voices.py:222..231` constructs `OpenAIHTTPException(OpenAIError(...))` directly for the `409 voice_id_exists` case because `invalid_request()` hard-codes status 400.
- **Plan:** add `status_code` parameter to `invalid_request` (default 400, fully back-compat) and collapse the 9-line raw construction at the one site into a single `raise invalid_request(...)`.

### (c) Voice-id regex + path validation — AS-FOUND
- `services/voice_store/records.py` exposes `validate_voice_id()` (defines the `[a-z0-9_-]{1,64}` regex once and raises `VoiceIdInvalidError`).
- All repository implementations call this helper: `fs_blob.py` (via the metadata-repo path) and `s3_blob.py:27,77`, `postgres_metadata.py:27,109,121,128,157,190`, `seed_ingestion.py:36`.
- **No work.** This was consolidated in Sprint 4 (S-014). The sprint plan flagged it as a likely candidate but the inventory shows it is already a single helper. Documenting as-found.

### (d) Provider allow-list extraction — AS-FOUND
- `Settings.tts_model_allowed_for_provider(provider) -> list[str]` exists at `config.py:515..521` and is the single accessor used by `dependencies.py:141,156`, `services/synthesize_service.py:95`, `services/tts_service.py` (via `_ensure_model_allowed`).
- `services/model_registry.py:17..20` iterates the raw per-provider lists once — but that is a different use case (the "union across providers" needed for `/v1/models`), not duplication.
- **No work.** Consolidated in Sprint 1 (S-005) and refined in S-012. Documenting as-found.

### (e) Test fixtures `_seed_voice` / `_stub_app_state` — REAL (test-only)
- `_seed_voice` is defined in three test files: `tests/test_openai_adapter.py:54`, `tests/test_openai_adapter_parity.py:54` (FROZEN by S-018 byte-identity gate — must not edit), `tests/test_synthesize.py:39`. The three copies are nearly identical (`test_synthesize.py`'s copy takes more knobs; the others are simplified clones).
- `_stub_app_state` is one function in `tests/conftest.py:85`; it is not duplicated.
- **Plan:** add a single `tests/fakes/seed_voice.py` exposing both a "minimal" and "full" form. `test_openai_adapter.py` and `test_synthesize.py` import from it. `test_openai_adapter_parity.py` is NOT touched (S-018 byte-identity gate).
- Note: test-fixture dedup does not count toward the production LOC reduction target — it's a maintainability win only.

### Other duplication noticed during inventory (NOT listed in sprint plan, but real)

#### (f) `_raise_not_implemented` helper — REAL
- Defined identically in three router files: `routers/audio.py:65..67`, `routers/realtime.py:8..10`, `routers/chat.py:8..10`. Each is `def _raise_not_implemented(endpoint: str) -> None: raise not_implemented(f"Endpoint '{endpoint}' is not implemented yet")`.
- **Plan:** add `raise_not_implemented(endpoint)` to `errors.py` and have the three routers import it. Three local copies removed.

## T7 — Behavior invariants (pre-flight)

- `tests/test_openai_adapter_parity.py` MUST NOT be modified (S-018 byte-identity).
- `docs/openapi/openapi.yaml` MUST stay byte-identical (no new endpoints, no schema changes).
- All existing 380 tests must still pass; 2 skipped + 1 xfailed unchanged.
- No new third-party dependencies.

LOC target: ≥3% production LOC reduction (≈190 lines from 6323). The plan's risk row explicitly permits documenting the result as "as-found" if the codebase is already lean — which (c) and (d) confirm is partially the case.

---

## Refactor log

### T2 — Header inventory consolidated
- Added `_synthesis_headers(...)` helper in `services/synthesize_service.py`. Both call sites in `synthesize_core` (streaming + buffered branches) now call this single helper.
- The header *names* on the OpenAI strip-list (`_RICH_ONLY_HEADERS` in `routers/audio.py`) stayed inline as a frozenset literal. **Reason:** the UAT-OA-03 static test (`tests/test_openai_adapter.py::test_audio_router_imports_synthesize_core_only`) asserts that any import from a `*synthesize*` module must alias exactly `synthesize_core`. Importing a second symbol (`_RICH_ONLY_HEADER_KEYS`) would fail that pin. The byte-level safety net is the S-018 paired UAT — if the two sets diverge, the parity test fails. Documented this constraint inline in `routers/audio.py`.

### T3 — Error envelope helpers — one site collapsed
- `errors.invalid_request` gained an optional `status_code: int = 400` parameter (fully back-compat — all existing call sites unaffected).
- The `voices.py` 409 `voice_id_exists` case now uses `invalid_request(..., status_code=409)` instead of raw `OpenAIHTTPException(OpenAIError(...))` construction. Envelope shape is byte-identical (same dataclass path).
- New `errors.raise_not_implemented(endpoint)` helper centralizes the three local `_raise_not_implemented` copies that previously lived in `routers/audio.py`, `routers/chat.py`, and `routers/realtime.py`. The three local helpers are deleted; routers now import and call the shared helper directly.

### T3 (bonus) — `voices.py` ref-audio error sites
- The four nearly-identical `invalid_request(..., param="audio", code="ref_audio_invalid")` calls in `_read_audio_validated` now go through a tiny local `_ref_audio_invalid(message)` shim. Saves 4 multi-line calls → 4 one-liners; behavior identical.

### T4 — Voice-id validation — AS-FOUND
- No code change. `services/voice_store/records.validate_voice_id()` is already the single helper; every blob/metadata repository implementation imports and calls it. Confirmed by `grep`-ing `validate_voice_id` across `src/llm_tts_api/services/voice_store/`.

### T5 — Allow-list accessor — AS-FOUND
- No code change. `Settings.tts_model_allowed_for_provider(provider)` is already the single accessor (Sprint 1 / S-005). `services/model_registry.py`'s iteration of raw per-provider lists is a different concern (union for `/v1/models`), not duplication.

### T6 — Test-fixture dedup
- Added `tests/fakes/seed_voice.py` with one `seed_voice(...)` helper. Two non-frozen test modules (`test_openai_adapter.py`, `test_synthesize.py`) now import this and dropped their local copies (and the now-unused `io` / `wave` / `VoiceRecord` imports for `test_synthesize.py`).
- `tests/test_openai_adapter_parity.py` is NOT touched — frozen by the S-018 byte-identity gate per the cycle invariants.
- Net test-LOC reduction ≈ 30 lines (doesn't count toward the production threshold).

### T2bis — Bonus: shared `_build_synthesis_request` helper
- The per-chunk `SynthesisRequest(...)` constructor was duplicated identically inside `_stream_synthesis_chunks` and `_run_synthesis` (~12 lines each). Factored into `_build_synthesis_request(...)`. Both call sites are now 6-line invocations.

## T7 — Final gates & measurements

### Production LOC
| Phase           | `wc -l src/llm_tts_api/**/*.py` |
|-----------------|--------------------------------|
| Baseline (step 1 end) | **6323** |
| Post-refactor   | **6336** |
| Delta           | **+13** (+0.21%) |

**The 3% reduction target was not met.** The codebase was already lean after Sprints 1–5: candidates (c) and (d) — the two largest "iterate-the-same-thing in three places" patterns from the sprint plan's suspect list — were already consolidated in earlier sprints (S-014 for voice-id validation, S-005/S-012 for the allow-list accessor). The remaining real duplications (header dicts, three `_raise_not_implemented` helpers, four `ref_audio_invalid` calls, the per-chunk request builder) saved fewer lines than the helper signatures + docstrings they introduced.

Per the sprint plan's explicit risk row ("if total LOC saving < 3%, the inventory is the deliverable and the threshold is documented as 'as-found'"), this is the documented outcome. The **maintainability win** is real: future changes to the header inventory, error envelope shape, or per-chunk request fields land in exactly one place each.

### Behavior gates
- **S-018 byte-identity paired UAT (`tests/test_openai_adapter_parity.py`):** UNTOUCHED — `git diff tests/test_openai_adapter_parity.py` is empty. Test still passes.
- **`docs/openapi/openapi.yaml`:** UNTOUCHED — `git diff docs/openapi/openapi.yaml` is empty (byte-identical).
- **Test suite:** `380 passed, 2 skipped, 3 deselected, 1 xfailed` — identical to the step-1-end baseline.
- **`uv run ruff check .`:** all checks passed.
- **`uv run ruff format --check .`:** 94 files already formatted.
- **`uv run mypy --strict src/`:** no issues, 52 source files.
- **`uv run pip-audit`:** no known vulnerabilities.
- **No new dependencies:** `pyproject.toml` untouched.

### Per-consolidation byte-identity rationale
- Header dict consolidation: helper returns a dict with the SAME keys/values as the previous inline construction; trailers (X-Chunks / X-Total-Duration-Ms) only injected when `chunks`/`duration_ms` are non-None — matches old conditional inclusion exactly.
- `voice_id_exists` envelope: `invalid_request(..., status_code=409, code="voice_id_exists", param="id", message=...)` ultimately constructs `OpenAIError(message, "validation_error", "voice_id_exists", "id")` → same `OpenAIHTTPException(409, error)` as the previous raw form.
- `_build_synthesis_request`: returns identical `SynthesisRequest(model_name=..., chunks=[chunk_text], voice=..., voice_name=..., response_format=..., generation=GenerationOptions(...))` — field-for-field equivalent to the inline form.
- `raise_not_implemented(endpoint)`: calls `not_implemented(f"Endpoint '{endpoint}' is not implemented yet")` — character-identical message to the three former local copies.

---
