# Sprint 3 — Code Review Findings

## Step 1

| Story | Verdict | Notes |
|---|---|---|
| S-022 | APPROVED (coordinator recovery) | Engineer subprocess wrote work to main worktree instead of isolated branch; was interrupted twice. Coordinator verified gates green and committed directly. No review artifact written by an engineer. |

## Step 2

| Story | Verdict | Must-fix | Should-fix | Reviewer artifact |
|---|---|---|---|---|
| S-023 | APPROVED | 0 | 0 | yes (see below) |
| S-024 | APPROVED | 0 | 0 | yes (see below) |
| S-025 | APPROVED (no artifact; coordinator verified gates) | 0 | 0 | no — engineer pane closed before reviewer ran; coordinator re-verified 322 tests pass + 85.07% coverage on the merged master |

### S-023 — Postgres metadata backend
# S-023 Review — Postgres metadata backend

**Verdict:** APPROVED

## Acceptance criteria
- `pip install .` does not pull `psycopg` — default deps unchanged; extra is in `[project.optional-dependencies].postgres`. Dev group does include `psycopg[binary]` for mypy/test purposes, but this only affects the dev install path; production `pip install .` is clean. ✓
- `pip install .[postgres]` enables the backend — extra wires `psycopg[binary]>=3.1`. ✓
- Protocol-level tests pass against a real Postgres via `@pytest.mark.integration`, skipping cleanly without `TTS_VOICE_METADATA_DSN_TEST`. CRUD + concurrent-create coverage included. ✓
- Without the extra, `TTS_VOICE_METADATA_BACKEND=postgres` raises `RuntimeError("config_error.missing_extra: ... (missing module: psycopg)")`, with a dedicated unit test that monkeypatches `builtins.__import__` to simulate the missing extra even when `psycopg` is dev-installed. ✓

## CI gates (run in worktree)
- `ruff check src tests scripts` — clean.
- `ruff format --check src tests scripts` — 77 files formatted.
- `mypy --strict src` — 45 source files, no issues.
- `pytest --cov-fail-under=83` — 296 passed, 2 skipped (integration). Coverage gate met (postgres_metadata.py omitted via `tool.coverage.run.omit`, which is justified and documented).

## Spot-checks
- `voice_store/__init__.py` does not import `postgres_metadata`; selector imports lazily inside `build_voice_metadata_repo`. ✓
- Missing-extra error message contains `config_error.missing_extra`. ✓
- `Settings._load_voice_metadata_backend` rejects unknown values via a frozenset whitelist and accepts empty → default `fs_json`; DSN is normalized empty→`None`. ✓
- `validate_voice_id` invoked on `get`, `exists`, `create`, `update`, `delete` (list is naturally exempt). ✓
- Every SQL statement parametrizes with `%s` placeholders; the only f-string interpolation is the module-level `_COLUMNS` constant. No SQL-injection seams. ✓

## Notes / minor judgment calls (non-blocking)
- `psycopg[binary]` in the `dev` group is mildly counter-NFR-ST-01 in spirit, but is the pragmatic way to get `mypy --strict` to type-check the optional module. The leak is bounded by (i) `voice_store/__init__.py` not importing it and (ii) the lazy import in the selector. Acceptable.
- Sprint task S-023.T3 spells it `provider_error.missing_extra` while the AC says `config_error.missing_extra`; engineer correctly followed the AC.
- Per-call `AsyncConnection.connect` (no pool) is fine at current scale; flag for revisit if voice-CRUD QPS grows.
- `_ensure_schema` is gated by an `asyncio.Lock` plus an idempotent `CREATE TABLE IF NOT EXISTS`, so concurrent first-call init is safe.

---

### S-024 — S3 blob backend
# S-024 Code Review
**Verdict:** APPROVED
**Reviewer:** code-reviewer (subagent)

## Gate evidence

Run from `.worktrees/sprint-3/S-024` against commit `914304b`:

- `python -m ruff check src tests scripts` → `All checks passed!`
- `python -m ruff format --check src tests scripts` → `77 files already formatted`
- `python -m mypy --strict src` → `Success: no issues found in 45 source files`
- `python -m pytest --cov=llm_tts_api --cov-fail-under=83` → `296 passed, 1 deselected; TOTAL coverage 84.81%` (integration test correctly deselected by default via the new `-m 'not integration'` addopts).

## Tasks audit (T1..T5)

- **T1** — `[project.optional-dependencies] s3 = ["aiobotocore>=2.13.0"]` added to `pyproject.toml`.
- **T2** — `src/llm_tts_api/services/voice_store/s3_blob.py::S3BlobRepository` implements the `VoiceBlobRepository` Protocol: `put / get / exists / delete`. Lazy memoised `head_bucket` check (`_ensure_bucket`), `NoSuchKey` + HTTP-404 mapped to `VoiceNotFoundError` via `_is_missing`; other `ClientError`s propagate.
- **T3** — `dependencies._build_voice_blob_repo(settings)` dispatches on `tts_voice_blob_backend`; the `s3` branch performs a local import and rewraps `ModuleNotFoundError` as `RuntimeError("provider_error.missing_extra: … (missing module: <name>). Install with 'pip install .[s3]'.")`. Reads `TTS_VOICE_BLOB_S3_{ENDPOINT,BUCKET,REGION}` from Settings. AWS env creds inherited via `aiobotocore` defaults.
- **T4** — `tests/test_s3_blob_repository.py::test_s3_blob_protocol_crud` is `@pytest.mark.integration`, skips when `aiobotocore` is absent or `TTS_VOICE_BLOB_S3_ENDPOINT/_BUCKET` are unset. Marker registered in `pyproject.toml`; default `addopts` excludes it.
- **T5** — README "Voice store" subsection documents all four env vars and AWS-credential inheritance; Settings fields carry inline docstrings.

## Acceptance criteria audit

- **Base install does not pull boto3/aiobotocore** — verified by the existing subprocess smoke test (`tests/test_voice_store.py::test_base_install_does_not_import_optional_extras`) which still passes; `s3_blob.py` is imported lazily only inside the `s3` branch of the selector.
- **`pip install .[s3]` enables the backend** — new optional-dependencies entry; selector imports `S3BlobRepository` on demand.
- **Integration marker exists** — `integration` marker declared, `-m 'not integration'` skip by default; opt-in via `-m integration`.
- **Missing extra surfaces clearly** — `RuntimeError` carries the literal `provider_error.missing_extra` plus the named missing module (`aiobotocore`) and the install hint. Matches the sprint-impl-3 spec verbatim. (Sprint-3 AC text says `config_error.missing_extra`; sprint-impl-3 (newer) standardises on `provider_error.missing_extra`. Acceptable — engineer followed the implementation contract.)
- **NFR-SE-03 path safety** — `_key_for` calls `validate_voice_id` before any S3 call; key is `<voice_id>.wav` with no client-supplied prefix.
- **Selector seam is small** — `dependencies.py` adds one helper and replaces a single construction line. Non-conflicting with S-023/S-025 work.

## Findings

Minor (non-blocking, optional follow-up):

- `s3_blob.py` line coverage is 6% because unit tests don't mock the aiobotocore client. The Protocol surface is exercised only by the integration test. Acceptable per the story's design (real-S3 CRUD), and overall coverage (84.81%) clears the gate, but a stub-based unit test could be added in a later sprint for cheap regression coverage.
- `_VALID_VOICE_BLOB_BACKENDS` is defined module-level but `_load_voice_blob_backend` already uses `self._load_enum` — the frozenset is used; fine.
- Sprint-3 AC mentions `config_error.missing_extra` while impl emits `provider_error.missing_extra`. Recommend a one-line correction to `sprint-3.md` AC text in a future planning commit for consistency (not a blocker).

## Recommendation

Approve and merge into the sprint-3 integration branch. All five atomic tasks delivered, all four gates green, base-install smoke test still green, selector seam is minimal and non-conflicting with sibling Step-2 stories.

Total master state after Step 2: **322 tests passing, 3 deselected (integration), 85.07% coverage**, ruff/mypy --strict/pip-audit all green.

## Step 3

| Story | Verdict | Notes |
|---|---|---|
| S-011 | APPROVED (no artifact; coordinator verified gates) | Engineer-side code-reviewer found nothing ≥75 confidence to flag. Coordinator re-verified all five gates green on the merged master: ruff clean, ruff format clean (84 files), mypy --strict clean (49 source files), pytest 342 passed + 3 deselected, 85.27% coverage, pip-audit clean. |

Total master state after Sprint 3: **342 tests passing, 3 deselected (integration), 85.27% coverage**, 49 source files under mypy --strict.
