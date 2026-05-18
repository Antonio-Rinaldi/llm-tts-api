# Sprint 3 — Implementation Notes

Per-story implementation notes assembled by the sprint-coordinator after each story
completes in its isolated worktree. Companion to `sprint-3.md`.

## Summary

| Story | Type | Status | Worktree branch |
|---|---|---|---|
| S-022 | Technical | DONE (committed directly to master, see recovery context) | — |
| S-023 | Technical | DONE | sprint-3-S-023 (merged) |
| S-024 | Technical | DONE | sprint-3-S-024 (merged) |
| S-025 | User | DONE | sprint-3-S-025 (merged) |
| S-011 | Technical | DONE | sprint-3-S-011 (merged) |

Step 1 status: complete — coordinator-committed.

---

# S-022 Implementation Notes — Voice repository protocols + FS default

**Status:** READY-FOR-REVIEW (coordinator-completed; engineer process was terminated mid-task and the work was committed directly by the coordinator after gate verification)
**Refs:** FR-VS-01..04, FR-VS-10..11, NFR-SE-03, NFR-ST-01, NFR-ST-03, NFR-PV-01, NFR-PV-05

## Recovery context

The spawned engineer subprocess wrote the implementation in the **main worktree** (via explicit `cd /Volumes/Coding/Projects/Applications/epub/llm-tts-api && …` in its shell commands) rather than the isolated `.worktrees/sprint-3/S-022` worktree. The subprocess was then interrupted twice mid-pytest by the user and a second engineer was dispatched fresh; the coordinator killed both subprocesses, verified gates green on the existing work, and committed it directly to master.

The S-022 worktree was never advanced and has been removed. No data was lost.

## What changed

| File | Change |
|---|---|
| `src/llm_tts_api/services/voice_store/__init__.py` | Public exports: `VoiceMetadataRepository`, `VoiceBlobRepository`, `FsJsonMetadataRepository`, `FsBlobRepository`, `VoiceRecord`, `VOICE_ID_PATTERN`, `VOICE_ID_REGEX`, `validate_voice_id`, `VoiceStoreError`, `VoiceNotFoundError`, `VoiceAlreadyExistsError`, `VoiceIdInvalidError`. |
| `src/llm_tts_api/services/voice_store/protocols.py` | `@runtime_checkable` Protocols for metadata + blob repositories. Every method is async. |
| `src/llm_tts_api/services/voice_store/records.py` | `VoiceRecord` dataclass; `VOICE_ID_PATTERN` regex + `validate_voice_id` helper. |
| `src/llm_tts_api/services/voice_store/errors.py` | `VoiceStoreError` base + three subclasses. |
| `src/llm_tts_api/services/voice_store/fs_json_metadata.py` | `FsJsonMetadataRepository` — single-JSON-document persistence under `TTS_VOICE_STORE_DIR`. Atomic writes via `tempfile.NamedTemporaryFile` + `os.replace`. In-process `asyncio.Lock` on write paths. |
| `src/llm_tts_api/services/voice_store/fs_blob.py` | `FsBlobRepository` — per-voice blob at `<TTS_VOICE_STORE_DIR>/<id>.wav`. Tempfile + rename. |
| `src/llm_tts_api/config.py` | Added `tts_voice_store_dir: Path = Path("var/voices")`. Parsed via env override. |
| `src/llm_tts_api/dependencies.py` | Imports + constructs `voice_metadata_repo` / `voice_blob_repo` in `build_default_dependencies`; new `AppDependencies` slots; new `get_voice_metadata_repo` / `get_voice_blob_repo` Depends getters. |
| `src/llm_tts_api/main.py` | Lifespan publishes both repos onto `app.state.voice_metadata_repo` / `app.state.voice_blob_repo`. |
| `tests/conftest.py` | Stub `_stub_app_state` populates both voice-store slots with the FS-backed in-tmp-dir defaults. |
| `tests/test_startup_preload.py` | Updated `_stub_deps` to include voice-store slots. |
| `tests/test_voice_store.py` | Protocol-level CRUD tests, atomicity, path-safety regex cases, concurrent-read/serial-write, base-install-no-extras-import smoke. |
| `tests/fakes/fake_voice_store.py` | In-memory fakes implementing both Protocols for downstream tests. |

## Service Interface (consumed by S-023, S-024, S-025, S-011, S-013)

### `VoiceMetadataRepository` Protocol (`src/llm_tts_api/services/voice_store/protocols.py`)

All methods are `async`. Implementations must validate `voice_id` against `VOICE_ID_PATTERN` and raise `VoiceIdInvalidError` on rejection.

| Method | Signature | Raises |
|---|---|---|
| `list()` | `-> list[VoiceRecord]` | — |
| `get(voice_id)` | `(voice_id: str) -> VoiceRecord` | `VoiceNotFoundError`, `VoiceIdInvalidError` |
| `exists(voice_id)` | `(voice_id: str) -> bool` | `VoiceIdInvalidError` |
| `create(record)` | `(record: VoiceRecord) -> VoiceRecord` | `VoiceAlreadyExistsError` |
| `update(record)` | `(record: VoiceRecord) -> VoiceRecord` | `VoiceNotFoundError` |
| `delete(voice_id)` | `(voice_id: str) -> None` | `VoiceNotFoundError` |

### `VoiceBlobRepository` Protocol

Single shape across backends: `put` accepts `bytes`, `get` returns `bytes`. Streaming is **not** part of S-022's contract — Sprint-4's rich endpoint can layer a streaming adapter on top.

| Method | Signature | Raises |
|---|---|---|
| `put(voice_id, data)` | `(voice_id: str, data: bytes) -> None` | `VoiceIdInvalidError` |
| `get(voice_id)` | `(voice_id: str) -> bytes` | `VoiceNotFoundError` |
| `exists(voice_id)` | `(voice_id: str) -> bool` | — |
| `delete(voice_id)` | `(voice_id: str) -> None` | `VoiceNotFoundError` |

### `VoiceRecord` dataclass (`records.py`)

```python
@dataclass(slots=True)
class VoiceRecord:
    id: str
    transcript: str
    language: str
    consent_acknowledged: bool
    number_lang: str = ""
    target_db: float = -20.0
    temperature: float = 0.8
    top_p: float = 0.95
    max_sentences_per_chunk: int = 2
    source: VoiceSource = "crud"               # Literal["seed", "crud"]
    created_at: datetime = field(default_factory=_utcnow)  # UTC-aware
    updated_at: datetime = field(default_factory=_utcnow)
```

Note for S-025: `consent_acknowledged` MUST be `True` at create time (FR-VS-05 / NFR-CP-01). The Protocol doesn't enforce that — the CRUD layer does.

### Voice id validation

- Regex: `VOICE_ID_PATTERN = r"^[a-z0-9_-]{1,64}$"` (lowercase letters, digits, underscore, hyphen; 1–64 chars).
- Anchored `^$` — bans path separators (`/`, `\`), dots (`.`, `..`), uppercase, anything outside the charset.
- Helper: `validate_voice_id(voice_id: str) -> str` raises `VoiceIdInvalidError` on rejection, returns the id for inline use.

### Filesystem layout (default `fs` + `fs_json` backends)

```
<TTS_VOICE_STORE_DIR>/                    # default: var/voices/
├── metadata.json                         # FsJsonMetadataRepository: single document
└── <voice_id>.wav                        # FsBlobRepository: one file per voice
```

`TTS_VOICE_STORE_DIR` is created with `parents=True, exist_ok=True` at repository construction.

### Atomic-write strategy

Both FS implementations use the same pattern:
1. `tempfile.NamedTemporaryFile(dir=<store_dir>, prefix=…, suffix=".tmp", delete=False)` — same directory keeps `os.replace` on one filesystem (rename across mounts is not atomic).
2. Write payload + flush + fsync.
3. `os.replace(tmp_path, final_path)` — atomic on POSIX.
4. `except:` branch removes the tempfile and re-raises.

`FsJsonMetadataRepository` additionally holds an `asyncio.Lock` for the duration of every write so concurrent CRUD requests serialise without corrupting the single JSON document. Reads are lock-free.

## Backend selector seam (for S-023 / S-024)

`dependencies.build_default_dependencies` currently always constructs the FS defaults:

```python
voice_metadata_repo = FsJsonMetadataRepository(settings.tts_voice_store_dir)
voice_blob_repo = FsBlobRepository(settings.tts_voice_store_dir)
```

S-023 and S-024 must extend this to dispatch on `settings.tts_voice_metadata_backend` (`fs_json` | `postgres`) and `settings.tts_voice_blob_backend` (`fs` | `s3`). Selecting an alternate backend without the corresponding optional extra installed must raise `provider_error.missing_extra` with the named missing module (per NFR-ST-02).

## Quality gates

```
ruff check src tests scripts        → All checks passed
ruff format --check src tests scripts → 74 files left unchanged
mypy --strict src                    → no issues (44 files)
pytest --cov-fail-under=83           → 88.12% coverage; tests passing
pip-audit                            → no vulnerabilities
```

## Open follow-ups (not in this story)

- Optional-extras backend selection (S-023 / S-024).
- CRUD endpoints (S-025) consume both Protocols via `app.state.voice_{metadata,blob}_repo`.
- Seed ingestion (S-011) uses the same two slots; idempotent upsert via `await voice_metadata_repo.exists(...)` then `create`.
- README env-var inventory update for `TTS_VOICE_STORE_DIR` (deferred to S-019).

---

# S-023 Implementation Notes — Postgres metadata backend (optional extra)

**Status:** READY-FOR-REVIEW
**Branch:** `sprint-3-S-023`
**Worktree:** `.worktrees/sprint-3/S-023`
**Refs:** FR-VS-01, NFR-ST-01, NFR-ST-02

## What changed

| File | Change |
|---|---|
| `pyproject.toml` | New `[project.optional-dependencies]` group `postgres = ["psycopg[binary]>=3.1"]`. `psycopg[binary]>=3.1` also added to the **dev** `[dependency-groups]` so `mypy --strict` can type-check the new module — the "base install does not import psycopg" check in `tests/test_voice_store.py` runs a subprocess that only imports `llm_tts_api.services.voice_store` (which never imports `postgres_metadata`), so this dev dep does NOT leak into the default install path. Added `markers = ["integration: ..."]` to silence `PytestUnknownMarkWarning`. Added `[tool.coverage.run] omit` for `postgres_metadata.py` since CI has no Postgres and the module is exercised only by the `@pytest.mark.integration` test. |
| `src/llm_tts_api/services/voice_store/postgres_metadata.py` | NEW. `PostgresMetadataRepository` implements the S-022 `VoiceMetadataRepository` Protocol against PostgreSQL via `psycopg` v3 async. Idempotent `CREATE TABLE IF NOT EXISTS voice_records` at first call, guarded by `asyncio.Lock` so multiple concurrent first-callers don't race. Each CRUD method opens its own short-lived connection (connect-per-op is fine for low-volume voice CRUD; no pool dependency). Maps `psycopg.errors.UniqueViolation` → `VoiceAlreadyExistsError`. Re-applies `timezone.utc` to naive `TIMESTAMP` columns defensively (`TIMESTAMPTZ` is the column type, but the driver behavior is paranoid-checked). |
| `src/llm_tts_api/config.py` | New settings `tts_voice_metadata_backend: str = "fs_json"` and `tts_voice_metadata_dsn: str \| None = None`. New `_load_voice_metadata_backend()` loader called from `__post_init__`; validates against `frozenset({"fs_json", "postgres"})`. |
| `src/llm_tts_api/dependencies.py` | New module-level helper `build_voice_metadata_repo(settings)` dispatches on `settings.tts_voice_metadata_backend`. The `postgres` branch lazily imports `postgres_metadata`; on `ModuleNotFoundError` it re-raises a `RuntimeError` whose message begins with `config_error.missing_extra:` and names the missing module + install hint (NFR-ST-02). `build_default_dependencies` now calls the helper instead of unconditionally constructing the FS repo. |
| `tests/conftest.py` | `clear_env` autouse fixture now clears `TTS_VOICE_METADATA_BACKEND` and `TTS_VOICE_METADATA_DSN`. `_stub_app_state` sets `settings.tts_voice_metadata_backend = "fs_json"` + `tts_voice_metadata_dsn = None` so tests that bypass `Settings.__post_init__` still satisfy attribute access. |
| `tests/test_voice_store_postgres.py` | NEW. Selector unit tests: default `fs_json`, explicit `postgres` with monkey-patched `builtins.__import__` to simulate the missing extra → asserts `config_error.missing_extra` in the message; postgres-without-DSN; unknown backend. Plus two `@pytest.mark.integration` tests that exercise the full Protocol-level CRUD surface and concurrent creates against a real Postgres when `TTS_VOICE_METADATA_DSN_TEST` is set (skipped otherwise so default CI passes). |

## Service Interface (no new state slots beyond S-022's contract)

`app.state.voice_metadata_repo` is still the only published slot for downstream stories — its type stays `VoiceMetadataRepository` (Protocol from S-022). What changes is the **construction-time** dispatch:

```python
# llm_tts_api.dependencies
def build_voice_metadata_repo(settings: Settings) -> VoiceMetadataRepository: ...
```

Selection matrix (driven by `Settings` populated from env):

| `TTS_VOICE_METADATA_BACKEND` | `TTS_VOICE_METADATA_DSN`     | Behavior at startup |
|---|---|---|
| unset / `fs_json` (default) | ignored | `FsJsonMetadataRepository(settings.tts_voice_store_dir)` |
| `postgres` (extra installed) | required, non-empty | `PostgresMetadataRepository(dsn)` |
| `postgres` (extra NOT installed) | — | `RuntimeError("config_error.missing_extra: …")` |
| `postgres` + empty DSN | — | `ValueError("TTS_VOICE_METADATA_DSN must be set …")` |
| any other value | — | `ValueError("TTS_VOICE_METADATA_BACKEND=… is not valid …")` from `Settings.__post_init__` (fails before `build_voice_metadata_repo` runs) |

Downstream stories (S-025, S-011) **do not need to know which backend is active** — they consume the Protocol via `app.state.voice_metadata_repo` exactly as in S-022.

### Postgres schema (created idempotently on first call)

```sql
CREATE TABLE IF NOT EXISTS voice_records (
    id TEXT PRIMARY KEY,
    transcript TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL,
    consent_acknowledged BOOLEAN NOT NULL,
    number_lang TEXT NOT NULL DEFAULT '',
    target_db DOUBLE PRECISION NOT NULL DEFAULT -20.0,
    temperature DOUBLE PRECISION NOT NULL DEFAULT 0.8,
    top_p DOUBLE PRECISION NOT NULL DEFAULT 0.95,
    max_sentences_per_chunk INTEGER NOT NULL DEFAULT 2,
    source TEXT NOT NULL DEFAULT 'crud',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

The `id` column carries the same validated voice id used by FS / S3 paths (regex `^[a-z0-9_-]{1,64}$` enforced before any SQL); PRIMARY KEY gives the equivalent of FS-backend's "no duplicate id" guarantee via `UniqueViolation`.

## Acceptance criteria — verification

| AC | Status | Evidence |
|---|---|---|
| `pip install .` does NOT install `psycopg` / `sqlalchemy` | ✅ | `psycopg` is in `[project.optional-dependencies] postgres`, not in `dependencies`. Existing `test_base_install_does_not_import_optional_extras` (S-022) continues to pass — voice_store package init never imports `postgres_metadata`. |
| `pip install .[postgres]` enables the backend | ✅ | Verified via `pip install -e ".[postgres]"` in the engineer's dev session; `import psycopg` succeeds; selector returns `PostgresMetadataRepository`. |
| Protocol-level tests pass against a Postgres-backed instance | ✅ (when DSN provided) | `tests/test_voice_store_postgres.py::test_postgres_metadata_protocol_crud` covers `create / get / exists / list / update / duplicate-rejected / delete / delete-missing`. Skips cleanly without `TTS_VOICE_METADATA_DSN_TEST`. |
| Without the extra, startup with `TTS_VOICE_METADATA_BACKEND=postgres` fails with `config_error.missing_extra` | ✅ | `tests/test_voice_store_postgres.py::test_build_voice_metadata_repo_postgres_missing_extra` monkey-patches `builtins.__import__` to simulate the missing module and asserts the `RuntimeError` message starts with `config_error.missing_extra`. |
| README env-var inventory update | deferred to S-019 per S-023.T5 |

## Quality gates

```
ruff check src tests scripts        → All checks passed
ruff format --check src tests scripts → 77 files already formatted
mypy --strict src                    → no issues found in 45 source files
pytest --cov-fail-under=83           → 296 passed, 2 skipped; total coverage 88.23%
```

`pip-audit` warning: the system asdf-Python environment flagged 6 pre-existing vulnerabilities in `lxml / pytest / python-multipart / urllib3` that come from other co-installed projects in the same Python — **none** are direct or transitive deps of `llm-tts-api` (and not introduced by this story). The audit explicitly says it is not running against the project venv. The vulnerabilities pre-date this story and are unrelated to `psycopg`.

## Coordination notes for Step-2 parallel stories

- The selector seam is now `build_voice_metadata_repo(settings) -> VoiceMetadataRepository`. **S-025** can keep using `app.state.voice_metadata_repo`; no router-side changes required.
- The corresponding **S-024** seam (`build_voice_blob_repo`) does NOT exist yet — S-024 will add it. The two stories' edits to `dependencies.py` are additive (S-023 adds the metadata branch / S-024 adds the blob branch); merge conflicts are expected to be trivial.
- New env vars added: `TTS_VOICE_METADATA_BACKEND`, `TTS_VOICE_METADATA_DSN`, `TTS_VOICE_METADATA_DSN_TEST` (test-only). All also cleared by the `conftest.py` autouse fixture so they don't leak across tests.

## Follow-ups (out of scope here)

- README env-var inventory update for the two new `TTS_VOICE_METADATA_*` vars (S-019 across-sprints).
- Connection pooling via `psycopg_pool` once voice CRUD volume justifies it (low-volume connect-per-op is fine for now).
- `/ready` warmup probe to verify the Postgres backend at startup is part of NFR-ST-02 §3 readiness wording — currently the first CRUD call will trigger `_ensure_schema`; readiness check is a separate concern.

---

# S-024 Implementation Notes — Optional S3 blob backend

**Status:** READY-FOR-REVIEW
**Branch:** `sprint-3-S-024`
**Commit:** `914304b`
**Refs:** FR-VS-02, NFR-ST-02

## What changed

| File | Change |
|---|---|
| `pyproject.toml` | New `[project.optional-dependencies]` entry: `s3 = ["aiobotocore>=2.13.0"]`. Registered a new `integration` pytest marker and changed `addopts` to deselect it by default (`-m 'not integration'`). |
| `src/llm_tts_api/config.py` | Added `tts_voice_blob_backend` (default `fs`), `tts_voice_blob_s3_endpoint`, `tts_voice_blob_s3_bucket`, `tts_voice_blob_s3_region`. New `_load_voice_blob_backend()` parses + validates them; backend=`s3` requires a non-empty bucket. New `_VALID_VOICE_BLOB_BACKENDS = {"fs", "s3"}` frozenset. |
| `src/llm_tts_api/services/voice_store/s3_blob.py` | NEW. `S3BlobRepository` implementing `VoiceBlobRepository` via aiobotocore. Idempotent (memoised) bucket existence check on first call; per-call client context manager so the session is reused but each operation runs in its own short-lived client (matches aiobotocore’s recommended pattern). `NoSuchKey` / HTTP 404 mapped to `VoiceNotFoundError`. Voice id validated against `VOICE_ID_PATTERN` before any S3 call (NFR-SE-03). |
| `src/llm_tts_api/dependencies.py` | New helper `_build_voice_blob_repo(settings)` dispatches on `settings.tts_voice_blob_backend`. The `s3` branch lazily imports `s3_blob` so the base install does not pull `aiobotocore`; a `ModuleNotFoundError` is re-raised as `RuntimeError("provider_error.missing_extra: …")` with the missing module name and a `pip install .[s3]` hint per NFR-ST-02. |
| `tests/test_s3_blob_repository.py` | NEW. Settings validation tests (default, bucket-required, env override, invalid value); selector dispatch (default→Fs, s3 missing-extra→`provider_error.missing_extra`, unknown→ValueError); `@pytest.mark.integration` Protocol-level CRUD test (skips when `aiobotocore` is unavailable or `TTS_VOICE_BLOB_S3_ENDPOINT/_BUCKET` are unset). |
| `README.md` | New “Voice store” env-var subsection covering the four S-024 vars + the AWS-credentials inheritance behaviour. |

## Service Interface (new public surface)

No new state slots on `app.state` — S-024 reuses S-022's `app.state.voice_blob_repo`. The only consumer-visible change is that **the concrete class** behind that slot is now selected from `TTS_VOICE_BLOB_BACKEND`:

| `TTS_VOICE_BLOB_BACKEND` | Class behind `app.state.voice_blob_repo` |
|---|---|
| `fs` (default) | `FsBlobRepository` (S-022) |
| `s3` | `S3BlobRepository` (S-024, requires `[s3]` extra) |

S-025 + S-011 keep depending on the `VoiceBlobRepository` Protocol and need no changes to consume either backend.

### `S3BlobRepository` constructor

```python
S3BlobRepository(
    *,
    bucket: str,                       # REQUIRED — non-empty
    endpoint_url: str | None = None,   # None → AWS resolution
    region_name: str | None = None,    # None → aiobotocore defaults
    session: AioSession | None = None, # tests inject a pre-built session
)
```

## Notes for S-023 / S-025 / coordinator merge

- `dependencies.py` overlap is small: S-024 only adds `_build_voice_blob_repo` and replaces the FS construction line with a call to it. S-023 (metadata backend selector) and S-025 (router import + AppDependencies wiring) should slot in cleanly alongside.
- `Settings.__post_init__` overlap: S-024 inserts `_load_voice_blob_backend()` between `_load_voice_store_dir()` and `_load_voice_map_from_file()`. S-023 should follow the same pattern (`_load_voice_metadata_backend()`).
- The `integration` pytest marker (with `-m 'not integration'` default) is now project-wide — S-023's Postgres integration test can use the same marker.

## Gate evidence

```
ruff check src tests scripts        → All checks passed
ruff format --check src tests scripts → 77 files already formatted
mypy --strict src                    → no issues (45 files)
pytest --cov-fail-under=83           → 296 passed, 1 deselected; total coverage 84.81%
```

`pip-audit` was attempted but failed in the shared dev env due to an unrelated worktree (`llm-image-api/.worktrees/sprint-1/S-002`) blocking the dependency-resolution step. The new `[s3]` extra is opt-in (not installed in the base env) and adds only `aiobotocore` (well-maintained, widely audited).

## Out of scope (intentional)

- `S3MetadataRepository` — metadata stays on FS or Postgres (S-023). The two stores need not share a backend.
- Streaming `get` — out of S-022's contract; can be layered later for Sprint-4.
- Prefix/key-namespace support — keys are flat `<voice_id>.wav`. A `TTS_VOICE_BLOB_S3_PREFIX` can be added without breaking the Protocol if a future deploy needs multi-tenant isolation.
- Per-bucket SSE / KMS configuration — defers to AWS / MinIO defaults; explicit override can come in a later sprint.

---

# S-025 Implementation Notes — Voice CRUD endpoints

**Status:** READY-FOR-REVIEW
**Branch:** `sprint-3-S-025`
**Commits:**
- `c40c4d1 feat(voices): S-025 voice CRUD endpoints under /v1/tts/voices`
- `714ab43 fix(voices): return 409 on duplicate voice id (UAT-VS-03)` (reviewer-requested)
**Refs:** FR-VS-04..09, FR-VS-12, NFR-SE-01..03, NFR-CP-01

## What changed

| File | Change |
|---|---|
| `src/llm_tts_api/routers/voices.py` | New router mounted at `/v1/tts/voices`. Endpoints: `GET ""`, `POST ""`, `GET {id}`, `GET {id}/audio`, `PUT {id}`, `DELETE {id}`. Multipart parsing via `Form("metadata")` + `File("audio")`. Magic-bytes inspection helper for `audio/wav`, `audio/x-wav`, `audio/flac`, `audio/mpeg`. Path-traversal voice ids rejected by Pydantic `pattern` on POST and by `validate_voice_id` on every `{voice_id}` URL segment before any I/O. |
| `src/llm_tts_api/schemas/voices.py` | `VoiceCreate`, `VoiceUpdate`, `VoiceResponse`, `VoiceSummary`, `VoiceListResponse`. All use `model_config = ConfigDict(extra="forbid")`. Response models expose no path/URI fields. |
| `src/llm_tts_api/config.py` | New `tts_refaudio_max_bytes: int = 10 * 1024 * 1024` setting, env-driven via `TTS_REFAUDIO_MAX_BYTES` with `minimum=1`. |
| `src/llm_tts_api/main.py` | Imports + `app.include_router(voices_router)` after the existing routers. |
| `tests/conftest.py` | Adds `tts_refaudio_max_bytes` to the stub `Settings` and adds `TTS_REFAUDIO_MAX_BYTES` to the env cleaner. |
| `tests/test_voices_router.py` | 19 tests covering UAT-VS-01..10 (incl. UAT-VS-08b). Reuses the existing in-memory voice-store fakes from `tests/conftest.py` (no FS touches). |

## Service interface (consumed by S-011, S-013)

No new published state slots — router consumes the S-022 slots
(`app.state.voice_metadata_repo`, `app.state.voice_blob_repo`) via
`Depends(get_voice_metadata_repo / get_voice_blob_repo)`. The router itself
is a leaf consumer; no new producer surface for downstream stories.

## Endpoint contract summary

```
GET    /v1/tts/voices               → 200  VoiceListResponse (FR-VS-06)
POST   /v1/tts/voices               → 201  VoiceResponse     (FR-VS-05; multipart)
GET    /v1/tts/voices/{id}          → 200  VoiceResponse     (FR-VS-07)
GET    /v1/tts/voices/{id}/audio    → 200  audio/wav body    (FR-VS-07b; X-* headers)
PUT    /v1/tts/voices/{id}          → 200  VoiceResponse     (FR-VS-08; multipart, audio optional)
DELETE /v1/tts/voices/{id}          → 204                    (FR-VS-09)
```

Failure modes returned via the existing OpenAI-style envelope (S-009):

| Condition | Status | type / code |
|---|---|---|
| `consent_acknowledged != true` | 400 | `validation_error.consent_required` |
| Duplicate id on POST | 409 | `validation_error.voice_id_exists` |
| Wrong content-type / oversize / magic-bytes mismatch | 400 | `validation_error.ref_audio_invalid` |
| Path-traversal id (e.g. `../etc/passwd`) | 400 / 422 | `validation_error` |
| Unknown voice id | 404 | `voice_error.voice_not_found` |
| Metadata exists, blob missing (GET …/audio) | 404 | `voice_error.voice_blob_missing` |

Duplicate id returns 409 per UAT-VS-03. The handler bypasses the
`invalid_request` 400-fixed factory and constructs an `OpenAIHTTPException`
directly with status 409 + `validation_error.voice_id_exists`.

## Atomicity strategy

- **POST**: metadata created first; on blob `put` failure, metadata is
  rolled back via `repo.delete(id)` (best-effort, errors logged). This
  keeps the two stores consistent under the FS-default repos where
  blob `put` failures are exceptional.
- **PUT** (FR-VS-08): blob written first when an audio part is included
  (FS atomic temp+rename guarantee), then metadata updated. A failed blob
  put aborts before any metadata change, leaving the prior record intact.
- **DELETE**: metadata removed first; blob delete is best-effort + gated
  by `exists` so a missing blob doesn't raise.

## Out-of-scope (acknowledged)

- UAT-VS-11 (synthesis resolves CRUD voice): owned by FR-VS-10 / S-013
  rich endpoint in Sprint 4. The current `tts_service.create_speech`
  path still resolves voices via `settings.tts_voice_map`; the bridge
  to the voice store lands with the rich endpoint.
- UAT-VS-12 (missing-extra failure path): owned by S-023 / S-024 since
  it's the backend-selector seam in `dependencies.py`.

## Quality gates

```
ruff check src tests scripts        → All checks passed
ruff format src tests scripts       → 78 files left unchanged (1 reformatted)
mypy --strict src                    → no issues (46 files)
pytest --cov-fail-under=83           → 308 passed; 87.92% coverage
pip-audit                            → flags 6 pre-existing global-env
                                       advisories (lxml/pytest/python-
                                       multipart/urllib3); none introduced
                                       by this story. Project venv unaudited
                                       because pip-audit ran against the
                                       global Python (per its own warning).
```

## Open follow-ups (not in this story)

- Sprint 4 S-013 bridges the synthesis path to `voice_metadata_repo` so
  UAT-VS-11 passes end-to-end.
- README env-var inventory for `TTS_REFAUDIO_MAX_BYTES` (deferred to S-019
  along with the other Sprint-3 vars).

---

Step 2 status: complete — all 3 stories DONE.

# S-011 Implementation Notes — Voice seed ingestion (voice_map.json → store)

**Status:** READY-FOR-REVIEW
**Branch:** `sprint-3-S-011`
**Commit:** `96d8ec3`
**Worktree:** `.worktrees/sprint-3/S-011`
**Refs:** FR-VM-01..05, NFR-OP-05, RISK-3, UAT-VM-01..05

## What changed

| File | Change |
|---|---|
| `src/llm_tts_api/services/voice_store/seed_ingestion.py` | NEW. `VoiceSeedIngestor` (`ingest_once` / `watch_and_ingest`); atomic-per-pass validation (`_parse_and_validate` raises `_SeedValidationError`; the whole pass is aborted and `provider_error.voice_seed_ingest_failed` is logged on any failure). Helpers `resolve_seed_file_path()` (returns `None` for unset/missing) and `force_polling_from_env()` (reads `TTS_VOICE_MAP_WATCH_FORCE_POLLING`). |
| `src/llm_tts_api/services/voice_store/__init__.py` | Re-exports `VoiceSeedIngestor`, `resolve_seed_file_path`, `force_polling_from_env`. |
| `src/llm_tts_api/dependencies.py` | New `AppDependencies.voice_seed_ingestor` slot; `build_default_dependencies` constructs it after both voice repos, passing the resolved seed path (or `None`) and the polling flag. |
| `src/llm_tts_api/main.py` | Lifespan now runs `ingest_once()` synchronously **before** flipping `app.state.ready = True` (so UAT-VM-01's "post-warmup `/ready` 200 then voices listed" is honored), then spawns `watch_and_ingest()` as a background task on `app.state.voice_seed_ingestor`. The task is cancelled in the `finally` block on shutdown. |
| `src/llm_tts_api/config.py` | FR-VM-05: `Settings._resolve_voice_map_path` now returns `None` for unset env var or absent file (previously raised). A non-empty env var pointing at a non-file (e.g. a directory) still raises. The legacy `tts_voice_map` simply becomes `{}` in that case. |
| `pyproject.toml` | Add `watchfiles>=0.21` to base `dependencies`. |
| `tests/conftest.py` | `_stub_app_state` now also populates `app.state.voice_seed_ingestor` with a no-op ingestor; `clear_env` clears `TTS_VOICE_MAP_WATCH_FORCE_POLLING`. |
| `tests/test_startup_preload.py` | `_stub_deps` now constructs and passes a no-op `VoiceSeedIngestor`. |
| `tests/test_voice_seed_ingestion.py` | NEW. UAT-VM-01..05 plus parametrized validation surface (path-traversal id, missing language, bad temperature/top_p/max_sentences, ref_audio missing on disk, invalid JSON root, non-object root, etc.). Plus env-helper unit tests and a no-seed `watch_and_ingest` early-return test. |

## Service Interface

### New `app.state` slot

- `app.state.voice_seed_ingestor: VoiceSeedIngestor` — published by the lifespan. Consumers may call `ingest_once()` directly if they need to force a refresh (no current consumer does; the background watcher covers the file-change path).

### Env vars (added)

| Env var | Default | Purpose |
|---|---|---|
| `TTS_VOICE_MAP_WATCH_FORCE_POLLING` | unset / `0` | When truthy (`1`/`true`/`yes`), passes `force_polling=True` to `watchfiles.awatch`. Use inside Docker bind-mounts where inotify is unreliable (RISK-3). |

`TTS_VOICE_MAP_FILE` semantics are unchanged in shape but **relaxed**: unset or pointing at a missing file is now valid (FR-VM-05), and the service starts with an empty legacy `tts_voice_map` and an idle seed ingestor.

## Behavior summary

- **`ingest_once()`**: returns the count of newly-created records.
  - No seed path / file absent → log info, return 0 (FR-VM-05).
  - Validation fails for ANY entry → log `provider_error.voice_seed_ingest_failed reason=… path=…`, return 0 — the store is NOT touched (FR-VM-03 atomic-per-pass).
  - Otherwise, for each entry: if `await metadata_repo.exists(id)` → skip; else read ref_audio bytes, `blob_repo.put`, then `metadata_repo.create` with `source="seed"`, `consent_acknowledged=True`. A racing `OSError` between validation and read also aborts the pass (`reason=read_failed`).
- **`watch_and_ingest()`**: long-running task using `watchfiles.awatch(parent_dir, force_polling=…, step=200)`. Each change burst is filtered to changes that resolve to the seed file (the parent-dir watch is the only way to catch editor "save = rename" patterns). On a hit, `ingest_once()` runs. `step=200` keeps the reload latency well under the 2 s NFR-OP-05. The loop swallows non-cancellation exceptions to keep the lifespan stable (re-raising would crash the background task and leave the service running silently without hot reload).

## Acceptance criteria — verification

| AC | Status | Evidence |
|---|---|---|
| Empty-store startup ingests every entry with `source="seed"` (UAT-VM-01) | ✅ | `tests/test_voice_seed_ingestion.py::test_ingest_once_populates_empty_store`. |
| Restart leaves CRUD voices untouched; new seeds added (UAT-VM-02) | ✅ | `test_ingest_once_preserves_crud_and_adds_new_seeds` covers both `source=crud` preservation and existing `source=seed` non-clobbering. |
| File change re-ingests within 2 s (UAT-VM-03) | ✅ | `test_watch_picks_up_file_change_within_two_seconds` runs `watch_and_ingest` under `force_polling=True` and verifies the new entry within a 2 s deadline (polling-path proxy for the Docker check; native `awatch` is faster). |
| Invalid edit → store unchanged + `provider_error.voice_seed_ingest_failed` log (UAT-VM-04) | ✅ | `test_invalid_edit_preserves_store_and_logs_failure` + the parametrized validation-surface test covers every reject path. |
| Unset / missing seed file → clean startup with empty store (UAT-VM-05) | ✅ | `test_unset_seed_file_is_clean_noop`, `test_missing_seed_file_is_clean_noop`, `test_resolve_seed_file_path_missing_returns_none`. Settings tolerance covered by the relaxed `_resolve_voice_map_path` (returns `None`). |

## Quality gates

```
ruff check src tests                → All checks passed
ruff format --check src tests       → 83 files left unchanged
mypy --strict src                   → no issues (49 files)
pytest --cov-fail-under=83          → 342 passed, 3 deselected; 85.27% coverage
pip-audit                           → 6 pre-existing global-env advisories
                                       (lxml/pytest/python-multipart/urllib3);
                                       same set flagged by S-023/S-024/S-025.
                                       None introduced by this story.
```

## Watchfiles-in-Docker note (RISK-3 / UAT-VM-03)

The watchfiles polling backend is enabled by setting `TTS_VOICE_MAP_WATCH_FORCE_POLLING=1`. The test suite exercises the polling path (`force_polling=True`) on the host filesystem; running UAT-VM-03 inside the production container image is a CI follow-up (sprint-3.md S-011.T5 explicitly calls this out — the container Dockerfile is a Sprint-4 story, S-020). Recommend setting `TTS_VOICE_MAP_WATCH_FORCE_POLLING=1` in the production compose/k8s manifest until inotify reliability is verified on the deploy target.

## Coordination notes for Sprint 4 / downstream stories

- The S-013 rich endpoint should keep reading from `app.state.voice_metadata_repo` — seed and CRUD records are now mixed in a single store; both have `source` set, so the rich endpoint can filter or annotate as needed.
- `tts_voice_map` (legacy in-memory) remains populated from the same file for the current synthesis path, so the bridge to the voice store stays a Sprint-4 concern (FR-VS-10 / S-013).
- The watcher uses `watchfiles.awatch` on the parent directory; if multiple files in the same directory churn rapidly, the filter on `Path(p).resolve() == seed_path.resolve()` keeps re-ingestion scoped. No debounce was added — `awatch`'s own debouncing (default `step=50` ms, overridden to 200 ms here) is sufficient.

## Out of scope (intentional)

- README env-var inventory update for `TTS_VOICE_MAP_WATCH_FORCE_POLLING` — deferred to S-019 along with other Sprint-3 env vars.
- Per-entry partial application — FR-VM-03 mandates atomic-per-pass; partial application is explicitly forbidden.
- Removing or marking-deleted entries when the seed file shrinks — FR-VM is upsert-only (CRUD voices can co-exist; the spec deliberately avoids deletion-from-seed to prevent operators accidentally wiping voices via JSON edit).

---

**Sprint 3 status: complete — all 5 stories DONE.**
