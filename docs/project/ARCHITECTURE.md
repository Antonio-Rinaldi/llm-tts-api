# Architecture — llm-tts-api

## Current Architecture Overview

```
CLI / entry-point
└── main.py                          [composition root + module-level side effect]
    ├── FastAPI app + lifespan
    ├── routers/
    │   ├── audio.py                 [HTTP handlers — type annotation gaps]
    │   ├── health.py                [liveness / readiness probes]
    │   ├── models.py                [model list endpoint]
    │   ├── chat.py                  [placeholder]
    │   └── realtime.py              [placeholder]
    ├── dependencies.py              [lru_cache singletons — composition]
    └── services/
        ├── tts_service.py           [TTSService — reads env var directly ⚠]
        │   ├── SpeechRequestResolver
        │   ├── SpeechSynthesizer
        │   └── SpeechResponseFactory
        ├── audio_postprocessing.py  [normalize_wav_rms]
        ├── text_preprocessing.py    [clean_punctuation / expand_* / split_text_semantic]
        ├── model_registry.py        [model allow-list lookup]
        ├── stt_service.py           [placeholder STT — missing return types ⚠]
        └── tts_providers/
            ├── base.py              [TTSProviderStrategy Protocol + SynthesisRequest (mutable) ⚠]
            ├── registry.py          [TTSProviderRegistry]
            ├── cached_model_provider.py [model cache + per-model Lock]
            ├── mlx_audio_provider.py   [MLXAudioTTSProvider]
            ├── voxtral_provider.py     [VoxtralTTSProvider]
            ├── vllm_omni_provider.py   [VllmOmniTTSProvider]
            └── voice_args.py           [voice/clone argument selection]
```

### Layer Boundary Violations

| Location | Violation |
|----------|-----------|
| `tts_service.py:243-246` | Service layer reads `os.getenv("TTS_MAX_CONCURRENT_REQUESTS")` — infrastructure concern belongs in `Settings` |

### Mutable Domain Object

| Location | Issue |
|----------|-------|
| `base.py:18` | `SynthesisRequest` is mutable (`@dataclass(slots=True)`) while all other domain value objects are frozen |

---

## Proposed Architecture (after enhancements)

All changes are additive type annotations, line-length reformatting, one field migration to
`Settings`, and one `frozen=True` addition. The logical structure is unchanged.

```
CLI / entry-point
└── main.py                          [typed lifespan + typed exception handler]
    ├── FastAPI app
    ├── routers/
    │   ├── audio.py                 [correct return types on all handlers]
    │   ├── health.py                [dict[str, str] return type]
    │   └── …
    ├── dependencies.py
    └── services/
        ├── tts_service.py           [reads max_concurrent_requests from Settings ✅]
        ├── audio_postprocessing.py  [correct _dtype_for_width return type ✅]
        ├── text_preprocessing.py    [clean num2words type ignore ✅]
        ├── stt_service.py           [NoReturn annotations ✅]
        └── tts_providers/
            ├── base.py              [SynthesisRequest frozen=True ✅]
            ├── cached_model_provider.py [typed _get_model return ✅]
            └── …
```

### Settings changes

Add one field to `Settings`:

```python
tts_max_concurrent_requests: int = 1
```

Loaded in `_load_tts_limits` via:
```python
max_req_raw = os.getenv("TTS_MAX_CONCURRENT_REQUESTS", "1").strip()
try:
    self.tts_max_concurrent_requests = max(1, int(max_req_raw))
except ValueError as exc:
    raise ValueError("TTS_MAX_CONCURRENT_REQUESTS must be an integer >= 1") from exc
```

`TTSService.__init__` receives `settings.tts_max_concurrent_requests` directly — no env access.

---

## Architecture Decision Records

### ADR-1: NoReturn for always-raising placeholder methods

**Decision:** `STTService.create_transcription` and `create_translation` are annotated
`-> NoReturn` because they unconditionally raise `OpenAIHTTPException`.

**Rationale:** `NoReturn` is the only honest return type for a function that never returns a
value. The router handlers that call these methods can then be annotated `-> NoReturn` as well,
satisfying mypy strict mode without introducing a fake return type.

**Consequence:** FastAPI is happy with `NoReturn`-annotated route handlers because they always
raise before FastAPI needs to serialize a response.

---

### ADR-2: Frozen SynthesisRequest

**Decision:** Add `frozen=True` to `SynthesisRequest`.

**Rationale:** All other domain value objects in the project are frozen. `SynthesisRequest` is
created by the service layer and consumed by provider strategies; providers should not mutate it.
Consistency and immutability safety.

**Consequence:** Provider implementations that previously set fields after construction will need
to be refactored (none currently do, so no functional change).

---

### ADR-3: Settings as sole env-var boundary

**Decision:** All `os.getenv` calls are restricted to `Settings.__post_init__` and its private
helper methods.

**Rationale:** A single configuration boundary makes the system testable — tests can construct
`Settings` directly with overridden fields without patching environment variables in unrelated
service constructors.

**Consequence:** `TTSService` takes `settings.tts_max_concurrent_requests: int` — a clean,
typed value — instead of reading the raw env string itself.

---

### ADR-4: _dtype_for_width returns type[…] not np.dtype[…]

**Decision:** Change the return annotation from `np.dtype[np.signedinteger[Any]] | None` to
`type[np.int16] | type[np.int32] | None`.

**Rationale:** The dict literal `{2: np.int16, 4: np.int32}` contains the dtype *classes*, not
dtype *instances*. `np.frombuffer` accepts dtype classes, so the runtime behavior is correct.
The annotation just needs to match what the dict actually holds.

**Consequence:** Callers using the result as a dtype argument to numpy functions remain correct.