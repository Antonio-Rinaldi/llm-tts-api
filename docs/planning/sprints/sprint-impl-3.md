# Sprint 3 — Implementation Notes

Per-story implementation notes assembled by the sprint-coordinator after each story
completes in its isolated worktree. Companion to `sprint-3.md`.

## Summary

| Story | Type | Status | Worktree branch |
|---|---|---|---|
| S-022 | Technical | DONE (committed directly to master, see recovery context) | — |
| S-023 | Technical | PLANNED (Step 2) | sprint-3-S-023 (pending) |
| S-024 | Technical | PLANNED (Step 2) | sprint-3-S-024 (pending) |
| S-025 | User | PLANNED (Step 2) | sprint-3-S-025 (pending) |
| S-011 | Technical | PLANNED (Step 3) | sprint-3-S-011 (pending) |

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
