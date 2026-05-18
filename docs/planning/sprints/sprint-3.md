# Sprint 3 — Voice store: repositories, optional backends, CRUD endpoints, seed ingestion

**Status:** PLANNED
**Planned:** 2026-05-18
**Stories:** S-022 (Step 1) → S-023 + S-024 + S-025 (Step 2 parallel) → S-011 (Step 3)
**Cycle:** llm-tts-api improvement cycle, Sprint 3 of N
**Source docs:** `docs/specs/software-spec.md`, `docs/specs/analyst-frs.md`, `docs/specs/writer-nfr.md`, `docs/planning/journal.md`

---

## Objective

Land the OQ-3-derived voice store end-to-end:

- **Repository Protocols + FS defaults** (S-022) for both metadata and audio blobs, so the default deploy needs zero external services.
- **Optional Postgres metadata backend** (S-023) behind a `[postgres]` extra.
- **Optional S3 blob backend** (S-024) behind a `[s3]` extra.
- **REST CRUD under `/v1/tts/voices/*`** (S-025) with the multipart-upload contract from FR-VS-04..09 and `consent_acknowledged` enforcement.
- **Idempotent seed ingestion** (S-011) — `voice_map.json` entries get upserted into the store at startup and on file change without clobbering CRUD-created voices.

By end of sprint, the service can be deployed against zero external dependencies (FS defaults) OR with `[postgres]` + `[s3]` extras for production. Operators can manage voices via REST or seed file. Sprint 4's rich endpoint (S-013) can resolve voices from `app.state.voice_metadata_repo` and stream audio from `app.state.voice_blob_repo`.

## Provability

Sprint 3 proves itself when:

- `POST /v1/tts/voices` with valid multipart payload returns 201 and creates a record on the FS default backend.
- Missing `consent_acknowledged=true` → 400 `validation_error.consent_required`.
- Duplicate id → 409 `validation_error.voice_id_exists`.
- Path-traversal id (`../etc/passwd`) → 400; no filesystem escape (NFR-SE-03).
- `pip install .` (no extras) starts the service against FS backends. `pip install .[postgres]` enables `PostgresMetadataRepository`; `TTS_VOICE_METADATA_BACKEND=postgres` without the extra fails startup with `config_error.missing_extra`.
- `voice_map.json` ingestion on empty store populates 3 voices with `source=seed`; restart with existing CRUD voices in the store leaves them untouched and idempotently adds new seeds.
- Invalid seed edit logs `provider_error.voice_seed_ingest_failed` and preserves the previous store state.
- `GET /v1/tts/voices` returns the list without exposing file paths or blob URIs.
- All CI gates green: ruff, mypy --strict, pytest --cov-fail-under=83, pip-audit.

## Constraints carried from SRS / NFR

- **No new runtime deps by default** (NFR-ST-01). Postgres and S3 must be optional extras.
- **Path safety on FS backend** (NFR-SE-03): voice id pattern `[a-z0-9_-]{1,64}`, no client-supplied path components reach the filesystem.
- **Bounded payload retention** (NFR-PV-01): voice blobs ARE persisted (operator content with a clear lifecycle); synthesis-time temp files cleaned via FR-VS-10.
- **Consent attestation enforced** (NFR-CP-01): create operations require `consent_acknowledged=true` in metadata.

---

## Execution Order

```
┌── Step 1 ─────────────────────────────────────────────────────────────┐
│  S-022 — VoiceMetadataRepository + VoiceBlobRepository Protocols      │
│          + FsJsonMetadataRepository + FsBlobRepository defaults       │
└────────────────────────────────────────────────────────────────────────┘
                                  ↓
┌── Step 2 (3 parallel; all consume S-022) ─────────────────────────────┐
│  S-023 — PostgresMetadataRepository (opt-in via `[postgres]` extra)   │
│  S-024 — S3BlobRepository           (opt-in via `[s3]` extra)         │
│  S-025 — Voice CRUD endpoints under /v1/tts/voices/*                  │
└────────────────────────────────────────────────────────────────────────┘
                                  ↓
┌── Step 3 (1; consumer of S-022 + S-025) ──────────────────────────────┐
│  S-011 — Idempotent voice_map.json → store seed ingestion             │
└────────────────────────────────────────────────────────────────────────┘
```

**Service-boundary enforcement**: S-022 publishes the two Protocols (`VoiceMetadataRepository`, `VoiceBlobRepository`) plus the FS default impls. S-023 + S-024 implement the Protocols (alternate impls). S-025 + S-011 consume the Protocols via `app.state.voice_metadata_repo` / `app.state.voice_blob_repo`. Producer S-022 MUST land before any consumer (S-023..S-025, S-011). Within Step 2, S-023/S-024/S-025 are mutually independent (their changes are mostly net-new files; only minor wiring in `dependencies.py` overlaps).

---

## Stories & Atomic Tasks

### S-022 — Voice repository protocols + FS default

**Type:** Technical
**Status:** DONE
**Depends on:** S-003 (DONE), S-012 (DONE)
**Refs:** FR-VS-01..04, FR-VS-10..11, NFR-SE-03, NFR-ST-01, NFR-ST-03, NFR-PV-01, NFR-PV-05
**Why selected:** Foundation for the entire voice store; every other Sprint 3 story consumes its Protocols.

**Acceptance criteria** (from journal):
- Both `VoiceMetadataRepository` and `VoiceBlobRepository` Protocols are defined with full CRUD operation surfaces.
- Default `FsJsonMetadataRepository` + `FsBlobRepository` backends pass unit tests for create/get/list/update/delete + atomicity.
- Path-safety test: malformed voice ids never escape `TTS_VOICE_STORE_DIR`.
- Concurrent reads + write: no corruption; serial write order via `asyncio.Lock`.
- Base install (no extras) imports and runs default backends; no Postgres/S3 imports leak into the default path.

**Atomic tasks:**

| Task | Purpose |
|---|---|
| S-022.T1 | Define `VoiceMetadataRepository` Protocol with `list / get / create / update / delete / exists`. Voice record schema is the `VoiceRecord` dataclass (id, transcript, language, number_lang, target_db, temperature, top_p, max_sentences_per_chunk, consent_acknowledged, source, created_at, updated_at). |
| S-022.T2 | Define `VoiceBlobRepository` Protocol with `put / get / delete / exists`. Blobs are `bytes`; `get` returns bytes (or a stream — implementation choice, single shape across backends). |
| S-022.T3 | Implement `FsJsonMetadataRepository` under `TTS_VOICE_STORE_DIR` (default `var/voices/`). Atomic writes via `tempfile.NamedTemporaryFile` + `os.replace`. In-process `asyncio.Lock` on write paths. |
| S-022.T4 | Implement `FsBlobRepository` (`<TTS_VOICE_STORE_DIR>/<id>.wav`). Tempfile + rename for puts. Path validation: voice id must match `^[a-z0-9_-]{1,64}$`; rejection of `..` / `/` / absolute paths. |
| S-022.T5 | Add `TTS_VOICE_STORE_DIR` to `Settings` (default `var/voices/`). Wire both repos into `app.state.voice_metadata_repo` / `app.state.voice_blob_repo` via lifespan (`build_default_dependencies`). |
| S-022.T6 | Tests covering Protocol-level CRUD, atomicity, path-safety regex rejection cases, and "base install does not import postgres/s3" (subprocess invocation or import inspection). |

---

### S-023 — Postgres metadata backend (optional extra)

**Type:** Technical
**Status:** DONE
**Depends on:** S-022 (Step 1)
**Refs:** FR-VS-01, NFR-ST-02
**Why selected:** Production-realistic metadata backend behind an optional extra; the SRS scopes it in explicitly.

**Acceptance criteria:**
- `pip install .` does NOT install `psycopg` / `sqlalchemy`.
- `pip install .[postgres]` enables the backend.
- Protocol-level tests pass against a Postgres-backed instance (service container in CI, or `@pytest.mark.integration` and skipped without a DSN).
- Without the extra, startup with `TTS_VOICE_METADATA_BACKEND=postgres` fails with `config_error.missing_extra`.

**Atomic tasks:**

| Task | Purpose |
|---|---|
| S-023.T1 | Add `[project.optional-dependencies]` (PEP 735 `[dependency-groups]` extra `postgres`) with `psycopg[binary]` or `sqlalchemy[asyncio]` + `psycopg`. |
| S-023.T2 | Implement `PostgresMetadataRepository` in `src/llm_tts_api/services/voice_store/postgres_metadata.py` (or similar). Same Protocol from S-022. Idempotent `CREATE TABLE IF NOT EXISTS` at startup; UUID PK or unique-constraint on voice_id. |
| S-023.T3 | Backend selector in `dependencies.py`: read `TTS_VOICE_METADATA_BACKEND` (`fs_json|postgres`, default `fs_json`); read `TTS_VOICE_METADATA_DSN` when `postgres`. Selecting `postgres` without the extra → `provider_error.missing_extra` with named missing module. |
| S-023.T4 | Integration test marked `@pytest.mark.integration` exercising the same Protocol-level test surface as `FsJsonMetadataRepository`. Skips cleanly when no DSN. |
| S-023.T5 | README env-var inventory update (deferred to S-019 across sprints; just confirm the doc-pointer comment in impl notes). |

---

### S-024 — S3 blob backend (optional extra)

**Type:** Technical
**Status:** DONE
**Depends on:** S-022 (Step 1)
**Refs:** FR-VS-02, NFR-ST-02
**Why selected:** Production-realistic blob backend behind an optional extra.

**Acceptance criteria:**
- `pip install .` does NOT install `boto3` / `aiobotocore`.
- `pip install .[s3]` enables the backend.
- Protocol-level tests pass against MinIO or AWS S3 (`@pytest.mark.integration`).
- Without the extra, startup with `TTS_VOICE_BLOB_BACKEND=s3` fails with `config_error.missing_extra`.

**Atomic tasks:**

| Task | Purpose |
|---|---|
| S-024.T1 | Add `[s3]` extra to `pyproject.toml` with `aiobotocore` (preferred for asyncio) or `boto3` + `botocore`. |
| S-024.T2 | Implement `S3BlobRepository` in `src/llm_tts_api/services/voice_store/s3_blob.py`. Same Protocol from S-022. Idempotent bucket existence check at startup; clear error if bucket missing/unreachable. |
| S-024.T3 | Selector in `dependencies.py`: `TTS_VOICE_BLOB_BACKEND` (`fs|s3`, default `fs`); `TTS_VOICE_BLOB_S3_ENDPOINT`, `TTS_VOICE_BLOB_S3_BUCKET`, `TTS_VOICE_BLOB_S3_REGION` + standard AWS env credentials. |
| S-024.T4 | Integration test marked `@pytest.mark.integration` against MinIO or AWS. |
| S-024.T5 | README + Settings docstring updates for the four new env vars. |

---

### S-025 — Voice CRUD endpoints

**Type:** User
**Status:** DONE
**Depends on:** S-022 (Step 1), S-009 (DONE)
**Refs:** FR-VS-04..09, FR-VS-12, NFR-SE-01..02, NFR-CP-01
**Why selected:** First user-facing surface of the voice store; gates Sprint 4's rich endpoint.

**Acceptance criteria:**
- `POST /v1/tts/voices` (multipart: audio + metadata JSON) → 201 with full record (no path/URI fields).
- Missing `consent_acknowledged=true` → 400 `validation_error.consent_required`.
- Duplicate id → 409 `validation_error.voice_id_exists`.
- Oversized / wrong-content-type audio → 400 `validation_error.ref_audio_invalid`.
- Path-traversal id → 400 `validation_error`.
- `GET /v1/tts/voices` (list, no audio), `GET /v1/tts/voices/{id}` (metadata only), `GET /v1/tts/voices/{id}/audio` (audio body).
- `PUT /v1/tts/voices/{id}` replaces metadata + optionally blob, atomically.
- `DELETE /v1/tts/voices/{id}` removes both.

**Atomic tasks:**

| Task | Purpose |
|---|---|
| S-025.T1 | Pydantic request/response models in `src/llm_tts_api/schemas/voices.py` with `model_config = ConfigDict(extra="forbid")`. |
| S-025.T2 | Router in `src/llm_tts_api/routers/voices.py` mounted at `/v1/tts/voices`. Handlers consume `app.state.voice_metadata_repo` + `app.state.voice_blob_repo`. |
| S-025.T3 | Multipart parser for `POST`/`PUT` (FastAPI `UploadFile` + JSON metadata part); size cap from `TTS_REFAUDIO_MAX_BYTES`; content-type allow-list + magic-bytes inspection. |
| S-025.T4 | Consent attestation enforcement (FR-VS-05 / NFR-CP-01): refuse create without `consent_acknowledged=true`, store the bool with the record. |
| S-025.T5 | Wire the router into `main.py:create_app`; expose `/v1/audio/voices/*` as reserved 501-stub (per SRS §4.4 OpenAI-compat reservation). |
| S-025.T6 | Tests: UAT-VS-01..12 (create/duplicate/oversized/corrupt/path-traversal/get-meta/get-audio/update/delete/missing-blob/inline-resolution/missing-extra). |

---

### S-011 — Voice seed ingestion (legacy JSON → store)

**Type:** Technical
**Status:** PLANNED
**Depends on:** S-022 (Step 1), S-025 (Step 2)
**Refs:** FR-VM-01..05, NFR-OP-05, RISK-3
**Why selected:** Closes the operator-facing seed-file workflow; lets existing deploys keep using `voice_map.json` without losing it on the voice-store transition.

**Acceptance criteria:**
- Empty-store startup ingests every `voice_map.json` entry with `source="seed"`.
- Restart with existing voices in the store: CRUD-created voices untouched; new seeds added.
- File change re-ingests within 2 s (`watchfiles`).
- Invalid seed edit (missing ref_audio file, bad schema) → store unchanged + `provider_error.voice_seed_ingest_failed` log.
- Unset `TTS_VOICE_MAP_FILE` or missing file → service starts cleanly with empty store.

**Atomic tasks:**

| Task | Purpose |
|---|---|
| S-011.T1 | Seed ingestion module in `src/llm_tts_api/services/voice_store/seed_ingestion.py`. Idempotent: upsert only if `voice_metadata_repo.exists(id)` is False. |
| S-011.T2 | Watchfiles-based reload at lifespan startup; polling fallback for Docker bind-mount environments (per RISK-3). |
| S-011.T3 | Atomic-per-pass validation: parse + validate the entire file before any write; abort on first failure. |
| S-011.T4 | Wire into `build_default_dependencies` after both voice repos are constructed. |
| S-011.T5 | Tests: UAT-VM-01..05 (empty-store population, restart idempotency, hot-reload, invalid-edit preservation, unset seed file). Run UAT-VM-03 inside the container image in CI for the watchfiles-in-Docker check. |

---

## Sprint-wide testing & verification

- All five Sprint-2 CI gates remain green: ruff (check + format), `mypy --strict src/`, `pytest --cov-fail-under=83`, `pip-audit`.
- Coverage target: hold ≥85% (the cycle floor stays 83% — should-fix should not regress below the current 87.60% by more than a percentage point or two).
- Optional-extras: a smoke check that `pip install .` (no extras) does NOT import `psycopg`/`boto3`/`aiobotocore` at module-import time.

## Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Engineers leave Step 2 branches uncommitted (Sprint 2 Step 1 pattern) | Medium | Medium | Engineer prompts now include the explicit commit-before-signal boilerplate per `SKILL.md` Step 2.5; coordinator verifies each branch advanced with `git log master..sprint-3-S-NNN` before merging. |
| Step 2 conflicts on `dependencies.py` + `Settings.__post_init__` (all three add wiring) | Medium | Low | Same playbook as Sprint 2 Step 1; conflicts are additive. S-022 establishes the seam shapes so Step 2 only adds backend selectors. |
| Postgres / S3 integration tests skip without infra → backend correctness not validated in CI | Medium | Low | Mark integration; document that local devs with MinIO + Postgres can run `pytest -m integration`. CI smoke just checks importability and the missing-extra failure path. |
| `watchfiles` unreliable in Docker (RISK-3) | Medium | Medium | S-011.T2 ships a polling fallback; UAT-VM-03 runs inside container. |

## Stories NOT in this sprint

- **S-013 rich endpoint**: depends on the voice store; Sprint 4.
- **S-015 streaming, S-016 cancellation**: depend on S-013.
- **S-017 OpenAI adapter, S-018 byte-identity**: depend on S-013.
- **S-019 docs, S-020 Dockerfile, S-021 perf validation**: end-of-cycle polish.

## Definition of Done (Sprint 3)

- All five stories' acceptance criteria met.
- All CI gates green on `master` after merge.
- `app.state.voice_metadata_repo` and `app.state.voice_blob_repo` slots populated by lifespan and consumed by S-025 + S-011.
- `pip install .` (no extras) starts the service against FS backends. `pip install .[postgres,s3]` enables both alternates.
- `voice_map.json` ingestion runs at startup + on file change; idempotent; never destroys CRUD-created voices.
- `/v1/tts/voices/*` CRUD endpoints exercised by UAT-VS-01..12.
- Sprint review document at `docs/planning/sprints/sprint-review-3.md`.
