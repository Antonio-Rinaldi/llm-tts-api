# Performance Baseline — llm-tts-api

**Purpose:** anchor for NFR-PF-01 (no latency regression vs. baseline; +10% budget on p50 and p95). Captured against the **pre-refactor** code path before Sprint 1's structural changes land in functional code paths. Re-measured by S-021 at end of cycle.

## Methodology

1. Start the service against a real MLX-audio provider with a representative voice in the voice map:
   ```bash
   uv run uvicorn llm_tts_api.main:app --host 127.0.0.1 --port 8010
   ```
2. In another terminal, run the measurement script:
   ```bash
   uv run python scripts/perf_baseline.py \
       --url http://127.0.0.1:8010 \
       --voice alloy \
       --model Qwen/Qwen3-TTS-12Hz-0.6B-Base \
       --runs 11 \
       --warmup 1 \
       --input tests/perf/fixtures/baseline_input.txt
   ```
3. Paste the printed Markdown row into the **Measurements** table below.
4. Commit the change with the run's git SHA referenced.

**Reference input:** `tests/perf/fixtures/baseline_input.txt` — Italian narrative prose, ~700 characters, ~5 sentences. Chosen to exercise semantic chunking and per-chunk normalization.

**Warmup:** one untimed run discards model-load cost so the measured runs reflect steady-state inference.

**Sample size:** 11 measured runs is the minimum for a stable p95 (one sample falls in the 95th percentile by construction). Run more if variance is high.

**Hardware:** the canonical baseline host is Apple Silicon (≥ 32 GB unified memory per NFR §1 / A-5). Numbers from other hosts may be recorded but are not the regression anchor.

## Measurements

The S-021 regression gate compares end-of-cycle numbers to the first row below. Append new rows on each re-measurement; never overwrite history.

| Commit SHA | Host | Voice | Input | Runs | p50 | p95 | min | max |
|---|---|---|---|---|---|---|---|---|
| _pending — run on Apple Silicon and paste here_ | — | alloy | 700 chars | 11 | — | — | — | — |

## Regression policy (NFR-PF-01)

- **+10% budget** on both p50 and p95 between the baseline row above and any later measurement against the same input + voice + warm model.
- A regression beyond budget blocks the cycle's success criteria.
- If hardware changes mid-cycle, capture a fresh baseline on the new host and document the substitution explicitly.

## Notes

- The script is intentionally minimal (stdlib `urllib`) so it has no extra deps and can run from any environment that can reach the service URL.
- `Content-Length` (one-shot WAV) latency is reported, not time-to-first-byte. S-015 streaming will add a separate measurement.
- The script drains the full response body so the measurement reflects synthesis completion, not header arrival.
