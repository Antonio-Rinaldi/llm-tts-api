# Enhancement Initiative Specification — llm-tts-api

## Executive Summary

A static quality audit of `llm-tts-api` (v0.1.0) reveals four problem areas that prevent the
project from passing its own declared quality gates (`mypy --strict`, `ruff`):

1. **Type annotation gaps** — mypy strict reports 29 errors across 11 files. Missing return types
   on route handlers and service methods, a wrong return type on an internal helper, and an
   unresolved `import-untyped` suppression conflict.
2. **Line-length violations** — ruff E501 reports 37 long lines across 4 files.
3. **Infrastructure concern in service layer** — `TTSService.__init__` reads the
   `TTS_MAX_CONCURRENT_REQUESTS` environment variable directly, bypassing `Settings` and making
   the dependency graph inconsistent.
4. **Mutable synthesis request** — `SynthesisRequest` is a mutable dataclass while every other
   domain object in the same layer is frozen; shared mutable state between provider and service
   layer is a latent correctness risk.

All tests pass before any change.

---

## Scope

**In scope**
- Fix all `mypy --strict` errors across `src/`
- Fix all `ruff` E501 line-length errors across `src/` and `tests/`
- Move `TTS_MAX_CONCURRENT_REQUESTS` env-var reading into `Settings`
- Make `SynthesisRequest` frozen

**Out of scope**
- New features or API endpoints
- Changes to the OpenAI-compatible API contract (routes, request/response schemas, exit codes)
- Introduction of new external dependencies
- Test suite expansion

---

## Detailed Findings

### Finding 1 — mypy type errors (29 errors, 11 files)

| File | Line | Error |
|------|------|-------|
| `src/llm_tts_api/services/audio_postprocessing.py` | 11 | Return type `type[object] \| None` incompatible with declared `dtype[signedinteger[Any]] \| None` |
| `src/llm_tts_api/errors.py` | 17 | `dict` missing type parameters |
| `src/llm_tts_api/services/stt_service.py` | 9, 13 | Methods missing return type annotations |
| `src/llm_tts_api/routers/audio.py` | 27 | `create_speech` return type `FileResponse` but implementation returns `FileResponse \| StreamingResponse` |
| `src/llm_tts_api/routers/audio.py` | 33, 35, 39, 41 | Missing return types; call to untyped STT methods |
| `src/llm_tts_api/main.py` | 49, 57 | `lifespan` missing return type; `openai_exception_handler` missing `Request` param type |
| `src/llm_tts_api/routers/health.py` | 10 | `dict` missing type parameters |
| `src/llm_tts_api/services/text_preprocessing.py` | 7, 9 | `import-untyped` on num2words not suppressed; now-unused `type: ignore[assignment]` |
| `src/llm_tts_api/services/tts_providers/mlx_audio_provider.py` | 49 | `model.generate` called on untyped `object` |
| `src/llm_tts_api/services/tts_providers/cached_model_provider.py` | 29 | `_get_model` missing return type |
| Multiple in `src/` | various | Additional strict-mode annotation gaps flagged by mypy 1.10+ |

### Finding 2 — ruff E501 line-length violations (37 errors)

All 37 violations are line-length (>100 chars) scattered across:
- `src/llm_tts_api/config.py` (lines 88, 93, 98, …) — long chained method calls
- `src/llm_tts_api/errors.py` (line 37) — long function signature
- `src/llm_tts_api/services/tts_service.py` (line 240) — long constructor signature
- `src/llm_tts_api/services/tts_providers/voice_args.py` — long function signatures

### Finding 3 — env-var read in service layer

`src/llm_tts_api/services/tts_service.py:243-246`:
```python
max_inflight_raw = os.getenv("TTS_MAX_CONCURRENT_REQUESTS", "1").strip()
try:
    max_concurrency = max(1, int(max_inflight_raw))
except ValueError as exc:
    raise ValueError("TTS_MAX_CONCURRENT_REQUESTS must be an integer >= 1") from exc
```
`TTSService` should not read environment variables directly. `Settings` is the declared boundary
for all runtime configuration. This pattern breaks the dependency inversion principle and makes
the service class untestable without env-var side effects.

### Finding 4 — mutable SynthesisRequest

`src/llm_tts_api/services/tts_providers/base.py:18`:
```python
@dataclass(slots=True)
class SynthesisRequest:
```
Every other domain value object in the project (`GenerationOptions`, `VoiceConfig`,
`ResolvedSpeechRequest`, `VoiceArgsSelection`) uses `frozen=True`. The omission is inconsistent
and allows a provider implementation to mutate the shared request object.

---

## Quality Attributes

| Attribute | Current | Target |
|-----------|---------|--------|
| mypy errors | 29 | 0 |
| ruff errors | 37 | 0 |
| Env-var reads inside service layer | 1 | 0 |
| Mutable domain value objects | 1 | 0 |
| All tests pass | ✅ | ✅ |

---

## Acceptance Criteria

1. `python -m mypy src/` reports **0 errors**.
2. `python -m ruff check src/ tests/` reports **0 errors**.
3. `python -m pytest` reports **0 failures**.
4. `Settings` is the sole class that reads `TTS_MAX_CONCURRENT_REQUESTS` from the environment.
5. `SynthesisRequest` has `frozen=True`.