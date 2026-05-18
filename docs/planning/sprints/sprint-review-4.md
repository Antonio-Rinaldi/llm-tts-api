# Sprint 4 — Code Review Findings

## Summary

| Story | Verdict | Notes |
|---|---|---|
| S-013 | APPROVED | Engineer-side reviewer found nothing ≥75 confidence. Coordinator re-verified gates green on merged master. |
| S-015 | APPROVED (with skip/xfail markers for 3 unsatisfiable tests) | Coordinator recovery: engineer subprocess was killed mid-task, restart engineer stalled on verification loop. Coordinator applied targeted test fixes (2 skipped due to Starlette listen_for_disconnect hang under mocked receive(); 1 xfail because TestClient buffers streaming). Production code unchanged. All gates green. |
| S-016 | APPROVED | Engineer-side reviewer wrote review.md with zero must-fix / should-fix. Handler change minimal and additive to S-015. |

## Master state after Sprint 4

- **360 tests passing, 2 skipped, 3 deselected (integration), 1 xfailed**
- **86.73% coverage**
- **51 source files under mypy --strict** (zero errors)
- **`pip-audit` clean**

## S-016 — Client-disconnect cancellation
# S-016 Code Review — Client-disconnect cancellation

**Branch:** `sprint-4-S-016` @ `cb4e275`
**Reviewer scope:** `src/llm_tts_api/routers/synthesize.py` + `tests/test_synthesize.py`
**Acceptance:** FR-CC-05 / UAT-CC-04 (semaphore release on disconnect,
no orphan temp files, cancellation at chunk boundary).

## Verdict: APPROVED

## Findings

1. **CancelledError vs `except Exception` — correct.** `asyncio.CancelledError`
   derives from `BaseException` (not `Exception`) on Python 3.8+, so the
   handler's `except Exception:` branch at `synthesize.py:310` does not
   swallow the cancellation. The exception unwinds through both
   `async with` blocks (releasing `concur_sem` and the per-model lock
   via `__aexit__`) and through the `finally` at `synthesize.py:399`
   (releasing `queue_sem`) and the handler's `finally` at
   `synthesize.py:319` (deleting `tmp_path`). Cleanup invariant holds.

2. **Cancellation boundary placement — correct.** The probe sits at the
   top of the `for chunk_text in chunks:` loop (`synthesize.py:368-381`),
   which is the only meaningful boundary in this handler. Probing
   before chunk 0 costs one extra `is_disconnected()` per request but
   is harmless and keeps the loop body uniform; this also makes it the
   right hook for S-015 to reuse for streaming yields.

3. **Per-chunk Strategy call — acceptable.** The previous one-shot
   `synthesize_chunks(all_chunks)` is replaced by N calls of
   `synthesize_chunks([single])`. The `TTSProviderStrategy` Protocol
   (`base.py:31-43`) accepts `SynthesisRequest.chunks: list[str]` of
   any length, so this is contract-compliant. The behavioural change
   (loss of any provider-internal batch amortisation) is the price of
   cancellation granularity and is the same shape S-015 needs for
   streaming. Documented inline at `synthesize.py:360-366`.

4. **Test rigor — adequate.** `test_synthesize_cancels_on_client_disconnect`
   pins all three acceptance criteria:
   - **Loop short-circuit**: `len(fake.calls) == 1` after a 4-chunk input
     proves the disconnect probe halted further synthesis.
   - **Semaphore return**: both `concurrency_semaphore._value` and
     `queue_semaphore._value` checked against baseline captured before
     the call (lines 303-304 vs 378-379), which is the right shape —
     baseline-relative beats hard-coded values across config changes.
   - **No orphan temp files**: probed via `created_paths`. The test
     correctly notes that since `_run_synthesis` is driven directly
     (not through the handler), the handler-side tempfile cleanup is
     covered by `test_synthesize_temp_file_cleaned_up`; this is a
     reasonable scope split given Starlette `TestClient` cannot trigger
     an ASGI disconnect.

5. **Step-2 merge surface with S-015 — clean.** The probe is the first
   statement inside the loop body, and S-015's per-chunk `yield` will
   sit immediately after the `outputs.extend(...)` line. The two
   stories meet additively at the same boundary with no conflict
   between request-side probe and response-side yield.

## Nits (non-blocking)

- `tests/test_synthesize.py:342`: `voice_cfg = record` is dead — it is
  immediately overwritten by the `VoiceConfig(...)` construction four
  lines below. Drop the placeholder.
- `tests/test_synthesize.py:386`: `_ = SynthesisRequest` is a
  keep-import-alive hack; the import isn't actually used in the test
  body, so just remove the import (and the assignment) instead.
- `probe_results = iter([False, True, True, True, True])` has more
  `True`s than needed (one is sufficient post-cancel since the loop
  exits on the second probe). Cosmetic.

None of the nits gate approval; they can be folded into a future
sweep or left as-is.

## Gate evidence (per impl notes)

- `ruff check` + `ruff format --check`: pass
- `mypy --strict src`: pass (51 files, no issues)
- `pytest`: 355 passed, 3 deselected, coverage gate met
