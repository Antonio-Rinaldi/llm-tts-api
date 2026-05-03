# Product Backlog — llm-tts-api Quality Uplift

## Priority Matrix

| Story ID | Title | Epic | Points | Priority | Dependencies | Status |
|----------|-------|------|--------|----------|-------------|--------|
| E1S1 | Fix mypy strict type errors | E1 | 3 | P1 Critical | — | ✅ Done |
| E1S2 | Fix ruff E501 + import/lint violations | E1 | 1 | P1 Critical | — | ✅ Done |
| E1S3 | Move TTS_MAX_CONCURRENT_REQUESTS to Settings | E1 | 2 | P2 High | E1S1 | ✅ Done |
| E1S4 | Make SynthesisRequest frozen | E1 | 1 | P2 High | E1S1 | ✅ Done |

---

## Epic 1 — Code Quality and SOLID Refactoring

### E1S1 — Fix mypy strict type errors

**As a** contributor running the quality gate,
**I want** `python -m mypy src/` to report zero errors,
**so that** type-checking is a reliable signal and not ignored noise.

**Acceptance Criteria**
- `audio_postprocessing.py:11` — `_dtype_for_width` return type changed from `np.dtype[np.signedinteger[Any]] | None` to `type[np.int16] | type[np.int32] | None`
- `errors.py:17` — `dict` return type annotated as `dict[str, object]`
- `stt_service.py:9,13` — both methods annotated `-> NoReturn`
- `routers/audio.py:27` — `create_speech` return type updated to `FileResponse | StreamingResponse`
- `routers/audio.py:33,39` — `create_transcription` and `create_translation` annotated `-> NoReturn`
- `main.py:49` — `lifespan` annotated `-> AsyncIterator[None]`
- `main.py:57` — `openai_exception_handler` request param typed as `Request`
- `routers/health.py:10` — `health` returns `dict[str, str]`
- `text_preprocessing.py:7,9` — num2words import uses `# type: ignore[import-untyped]`; unused `# type: ignore[assignment]` removed
- `cached_model_provider.py:29` — `_get_model` return type annotated as `Any`
- `python -m mypy src/` exits with 0 errors

**Points:** 3 | **Priority:** P1 Critical | **Dependencies:** —

---

### E1S2 — Fix ruff E501 line-length violations

**As a** contributor,
**I want** `python -m ruff check src/ tests/` to report zero errors,
**so that** style is uniform and the CI gate is clean.

**Acceptance Criteria**
- All 37 E501 violations resolved — lines in `config.py`, `errors.py`, `tts_service.py`, `voice_args.py` wrapped within 100-character limit
- `python -m ruff check src/ tests/` exits with 0 errors
- No functional logic changed

**Points:** 1 | **Priority:** P1 Critical | **Dependencies:** —

---

### E1S3 — Move TTS_MAX_CONCURRENT_REQUESTS to Settings

**As a** developer writing tests for `TTSService`,
**I want** `Settings` to be the single source of all environment configuration,
**so that** I can construct `TTSService` with a plain typed value and don't need env-var patching.

**Acceptance Criteria**
- `Settings` gains `tts_max_concurrent_requests: int` field loaded in `_load_tts_limits` from `TTS_MAX_CONCURRENT_REQUESTS` env var (default `1`, min `1`)
- `TTSService.__init__` receives `settings.tts_max_concurrent_requests` and does not call `os.getenv`
- `tts_service.py` no longer imports `os`
- All existing tests pass
- `python -m mypy src/` still exits with 0 errors

**Points:** 2 | **Priority:** P2 High | **Dependencies:** E1S1

---

### E1S4 — Make SynthesisRequest frozen

**As a** provider implementer,
**I want** `SynthesisRequest` to be immutable,
**so that** it is consistent with every other domain value object and cannot be accidentally mutated.

**Acceptance Criteria**
- `base.py:18` — `@dataclass(slots=True)` changed to `@dataclass(slots=True, frozen=True)`
- No provider implementation mutates a `SynthesisRequest` field (verified by search)
- All existing tests pass

**Points:** 1 | **Priority:** P2 High | **Dependencies:** E1S1