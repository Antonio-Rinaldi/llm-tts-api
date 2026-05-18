# Sprint 5 — Implementation Notes

Per-story implementation notes assembled by the sprint-coordinator after each story
completes in its isolated worktree. Companion to `sprint-5.md`.

## Summary

| Story | Type | Status | Worktree branch |
|---|---|---|---|
| S-017 | User | READY-FOR-REVIEW | sprint-5-S-017 (merged) |
| S-018 | Technical | PLANNED | sprint-5-S-018 (pending) |

Sprint 5 status: Step 1 complete (S-017 merged); Step 2 (S-018) pending.

---

# S-017 — OpenAI adapter as thin translator

**Branch:** `sprint-5-S-017` (merged into master as commit `73bca07`)
**Worktree:** `.worktrees/sprint-5/S-017`


> Sprint: 5
> Story: S-017
> Status: READY-FOR-REVIEW
> Refs: FR-OA-01..04, NFR-PT-03, BR-9, UAT-OA-01..04
> SRS: §4.3, §5 G-1

## Outcome

`POST /v1/audio/speech` is now a thin OpenAI-shaped translator over a
shared service-layer entry point (`synthesize_core`). Both the rich
endpoint (`POST /v1/tts/synthesize`) and the OpenAI adapter funnel
through the same pipeline; there is exactly one synthesis path (BR-9).

- Adapter handler (`routers/audio.py::create_speech`) is 14 source lines
  of translation, well under the UAT-OA-03 ≤30 LOC budget.
- Adapter does not import `SpeechSynthesizer`; does not call into
  `routers/synthesize`. Pinned by an AST static check in
  `tests/test_openai_adapter.py`.
- Rich-endpoint-only response headers are stripped on the OpenAI path
  (user-decided constraint). Streaming is rewrapped in a plain
  `StreamingResponse` so trailer emission cannot leak either.
- Test suite: 360 → 372 passing (12 new in `test_openai_adapter.py`); no
  regressions. mypy --strict + ruff + pip-audit clean.

## Architecture

```
                ┌─────────────────────────────────────┐
POST /v1/audio/  │ routers/audio.py::create_speech    │
speech          ─►│   - _translate_openai_request()   │──┐
                │   - _openai_response() strips X-*  │  │
                └─────────────────────────────────────┘  │
                                                         │
                                                         ▼
                ┌──────────────────────────────────────────────┐
                │ services/synthesize_service.py               │
                │   ::synthesize_core(payload, request, deps…) │
                │   = the single synthesis pipeline (BR-9)     │
                └──────────────────────────────────────────────┘
                                                         ▲
                ┌─────────────────────────────────────┐  │
POST /v1/tts/   │ routers/synthesize.py::synthesize  │──┘
synthesize     ─►│   thin wrapper — resolves Depends │
                └─────────────────────────────────────┘
```

`SpeechSynthesizer` / `TTSService` are kept in
`llm_tts_api.services.tts_service` because:
- The startup preload uses them (`TTSService.__init__` calls
  `provider_strategy.preload(...)`).
- `tests/test_concurrency.py` exercises the queue/cap semantics by
  calling `TTSService.create_speech` directly (it never goes through the
  HTTP layer for UAT-CC-01 / UAT-CC-03 / per-model-lock).
- They are no longer reachable from any router; the runtime synthesis
  path is exclusively through `synthesize_core`.

## Service Interface

**This section is the contract S-018 reads to build the paired UAT.**

`services/synthesize_service.py::synthesize_core` is the single synthesis
entry point. Both handlers must construct a `SynthesizeRequest` and
call this function.

### OpenAI `SpeechRequest` → rich `SynthesizeRequest` mapping (T1)

| OpenAI field        | Rich `SynthesizeRequest` field | Default if absent / notes                                  |
|---------------------|---------------------------------|------------------------------------------------------------|
| `model`             | `model`                         | Passed through. Rich endpoint enforces the per-provider allow-list. |
| `input`             | `input`                         | Passed through. Validated by the rich pipeline (length, non-empty). |
| `voice`             | `voice`                         | Passed through. Resolved against the voice store (`voice_metadata_repo` + `voice_blob_repo`). |
| `provider`          | `provider`                      | Passed through. Non-OpenAI extension; `None` → auto-selection. |
| `response_format`   | `response_format`               | Must be `"wav"`; non-wav rejected upfront with `validation_error.invalid_parameter` (param=`response_format`). |
| `normalize_db`      | `normalize_db`                  | Passed through. Non-OpenAI extension. |
| `instructions`      | —                               | **Ignored.** No rich equivalent in this cycle.             |
| `speed`             | —                               | **Ignored.** No rich equivalent (would map to `temperature` if exposed). |
| `stream_format`     | —                               | **Ignored.** No rich equivalent in this cycle.             |
| `?stream=` (query)  | `stream`                        | Streaming toggle is the existing query parameter — not a body field. |

### Rich-endpoint fields NOT exposed by the OpenAI shape

Defaults (applied by the rich pipeline from the stored `VoiceRecord` /
`Settings` — no per-request override on the OpenAI path):

| Rich field                | Default source                                                  |
|---------------------------|------------------------------------------------------------------|
| `language`                | `VoiceRecord.language`                                          |
| `number_lang`             | `VoiceRecord.number_lang`                                       |
| `temperature`             | `VoiceRecord.temperature`                                       |
| `top_p`                   | `VoiceRecord.top_p`                                             |
| `max_sentences_per_chunk` | `VoiceRecord.max_sentences_per_chunk`                           |

For **byte-identity** (UAT-OA-05 / NFR-PT-03b — S-018), the paired rich
request must replicate the OpenAI request 1:1: the same model, same
voice id, same `response_format="wav"`, and **omit every rich-only
field above** so the same `VoiceRecord` defaults are applied. Provider
auto-selection must produce the same provider, OR both requests must
pass the same explicit `provider`.

### Response shape (OpenAI path)

The OpenAI adapter strips this exact header set before returning:

```
X-Provider, X-Model, X-Device, X-Dtype,
X-Voice-Source, X-Voice-Id,
X-Chunks, X-Total-Duration-Ms
```

`X-Request-ID` is preserved (OpenAI's own contract permits a request
id). On the streaming path the adapter constructs a fresh
`StreamingResponse` from the rich response's `body_iterator`, which also
discards the rich endpoint's HTTP-trailer emission logic — so
`X-Chunks` / `X-Total-Duration-Ms` cannot leak as trailers either.

### Error mapping

The OpenAI adapter does **NOT** translate error envelopes. Every error
(`validation_error`, `voice_error`, `capacity_error`, etc.) is the
rich-endpoint envelope verbatim, per FR-OA-02 "no duplicated error
mapping." Sprint-4 / earlier sprints already standardized on the OpenAI-
compatible envelope, so existing OpenAI SDK clients still parse the
shape (`{"error": {"type", "code", "message", "param", "request_id"}}`).

One observable contract change vs the pre-S-017 adapter: an unmapped
voice now returns `404 voice_error.voice_not_found` (rich envelope)
instead of `400 validation_error` (old TTSService envelope). This is
the price of FR-OA-02 — the test `test_speech_rejects_unmapped_voice`
was updated to assert the new behaviour.

## Task status

| # | Task | Status | Notes |
|---|------|--------|-------|
| T1 | OpenAI → rich mapping table | DONE | Pinned in this doc's "Service Interface" section. |
| T2 | Refactor handler to translate + delegate | DONE | `routers/audio.py::create_speech` 14 LOC; calls `synthesize_core`. |
| T3 | Preserve OpenAI streaming + strip rich headers | DONE | `_openai_response()` strips header set above; streaming rewrapped to drop trailer emission. |
| T4 | `/v1/models` driven by provider registry + allow-lists | DONE | `ModelRegistry.list_models` already enumerates the per-provider allow-lists; new test (`test_models_endpoint_matches_provider_allowlists`) pins the contract. |
| T5 | Tests UAT-OA-01..04 + no-bypass static check | DONE | `tests/test_openai_adapter.py` — 12 tests covering happy path, header strip, streaming, AST-based no-bypass check, 30-LOC budget, `/v1/models` parity. |

## Files changed

- **NEW** `src/llm_tts_api/services/synthesize_service.py` — shared
  synthesis pipeline entry point (`synthesize_core`) plus helpers
  (`_run_synthesis`, `_TrailerStreamingResponse`,
  `_client_advertises_trailers`, `_resolve_voice`,
  `_resolve_provider_and_model`, `_build_voice_config`,
  `_stream_synthesis_chunks`, `_wav_duration_ms`).
- **MODIFIED** `src/llm_tts_api/routers/synthesize.py` — now a thin
  router that re-exports back-compat helpers and delegates to
  `synthesize_core`. ~80 LOC vs ~580 before.
- **MODIFIED** `src/llm_tts_api/routers/audio.py` — `create_speech`
  rewritten as a thin translator. Helpers
  `_translate_openai_request` + `_openai_response` enforce the
  OpenAI-identical response contract.
- **NEW** `tests/test_openai_adapter.py` — UAT-OA-01..04 coverage.
- **MODIFIED** `tests/test_audio_speech.py` — updated
  `test_speech_rejects_unmapped_voice` (new envelope) and
  `test_speech_forwards_clone_voice_config_to_mlx_provider` (the rich
  pipeline writes the blob to a per-request tempfile before the
  provider call).
- **MODIFIED** `tests/test_synthesize.py` — tempfile monkeypatch path
  re-targeted at `services.synthesize_service.tempfile`.
- **MODIFIED** `tests/test_concurrency.py` — `real_app_client` fixture
  now wires `provider_selection`, `voice_metadata_repo`, and
  `voice_blob_repo` (with "alloy" pre-seeded) so the audio endpoint
  delegates through `synthesize_core`.

## Gates

```
ruff check .            ✓
ruff format --check .   ✓
mypy --strict src/      ✓ (52 files)
pytest                  ✓ 372 passed, 2 skipped, 3 deselected, 1 xfailed
pip-audit               ✓ No known vulnerabilities
```

Baseline at sprint start: 360 passed + 2 skipped + 1 xfailed.
Delta: +12 tests (test_openai_adapter.py); no regressions; existing
skips/xfail preserved.

## Risks + future work

- **Out of scope, by user constraint**: byte-identity verification
  (`UAT-OA-05` / `NFR-PT-03b`) is S-018's job. This doc's "Service
  Interface" section is the contract S-018 reads.
- **Streaming buffering under TestClient**: `httpx.ASGITransport`
  buffers the streaming body before returning to the test, so the
  S-015 xfail (`test_streaming_first_byte_arrives_before_half_duration`)
  still applies — out-of-process uvicorn validation is deferred to
  Sprint 6 (S-021).
- **`/v1/models` enumeration**: the response only carries an `id` per
  model object, not a `(provider, model)` pair. A strict cross-product
  test would need a richer schema; the current test asserts each
  per-provider allow-list is a subset of `/v1/models` output (UAT-OA-04
  "exact match OR documented subset"). Documented here as the intended
  shape.

