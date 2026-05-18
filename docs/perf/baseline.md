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

## RISK-8 byte-identity relaxation (NFR-PT-03b / SRS §5 G-1)

NFR-PT-03b mandates that an OpenAI-adapter request and the equivalent rich-endpoint request produce byte-identical audio on a warm model (SRS §5 resolution G-1). RISK-8 acknowledges that real TTS providers may be non-deterministic; this section pins the documented relaxation contract referenced from SRS §5 G-1.

**Strict path (gate of the equivalence claim).** `sha256(openai_body) == sha256(rich_body)`. Exercised by `tests/test_openai_adapter_parity.py::test_paired_byte_identity_strict` against the deterministic in-process `FakeTTSProvider` warm-model combo. Lives in the standard unit suite — no integration marker — because the in-process app + FakeTTSProvider runs in milliseconds and model load is already amortized by the rest of the suite (see S-018 impl notes, T4).

**Relaxed fallback (RISK-8 materializes).** If a provider proves non-deterministic in CI, the paired UAT falls back to:

| Bound                 | Tolerance | Rationale |
|-----------------------|-----------|-----------|
| Audio length          | ±1 PCM sample on `wave.getnframes()` of the first chunk WAV | Sample-level jitter from non-deterministic chunk boundaries; one-sample slack absorbs it without admitting an audible difference. |
| Perceptual hash       | Hamming distance ≤ 1 over a 64-bit body fingerprint        | Catches whole-body divergence while permitting bit-level numerical noise. Implementation: `blake2b(body, digest_size=8)` — coarse-grained "did the synthesis go off the rails" check, deliberately cheap so the relaxation path adds no new deps. |

The relaxation thresholds and code path are pinned in `tests/test_openai_adapter_parity.py::test_paired_byte_identity_relaxed_under_risk8` so the fallback contract is itself code-covered, not only documented in prose. On the deterministic FakeTTSProvider both bounds collapse to equality; the test still exercises the relaxation arithmetic to keep the contract live.

**Escalation policy.** If the strict test starts flaking on a provider that was previously deterministic, the response is to switch *that provider's* paired test to the relaxed assertion and record the switch here (provider + commit SHA + date), not to delete the strict test. The strict path stays in CI for at least one deterministic provider/model combo per SRS §5 G-1.
