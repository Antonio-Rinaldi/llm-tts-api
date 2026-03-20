# qwen-tts-api Professional Refactor Plan

## 1) Goals

This plan defines a phased refactor of the current `qwen-tts-api` into a modular, professional, OpenAI-compatible service with environment-driven configuration.

Primary objectives:

1. Keep compatibility with OpenAI-style `/v1` routes listed in `docs/openapi/audio`.
2. Fully implement currently feasible routes with existing/local backend capabilities.
3. Return consistent OpenAI-style structured `501 not_implemented` errors for unsupported routes.
4. Make model selection and behavior configurable via environment variables.
5. Maintain clean architecture with DRY and SOLID principles.
6. Build tests before implementation for critical behavior.

---

## 2) Input Constraints and Confirmed Product Decisions

### Confirmed by product direction

- Endpoint scope: all endpoints documented under `docs/openapi/audio` must exist under `/v1`.
- Unsupported endpoint behavior: use structured `501 not_implemented` with OpenAI-style error envelope.
- Auth: no auth for now (local network usage).
- Voice handling for speech: OpenAI `voice` field must map via environment to `{ref_audio_path, ref_text, language}`; unmapped voice returns structured error.
- Architecture preference: modular, non-monolithic design with abstractions.
- Implementation order (strict phases):
  1. Write this `PLAN.md`
  2. Write tests first
  3. Implement speech
  4. Implement transcription/translation
  5. Implement stubs for remaining endpoints

---

## 3) Endpoint Inventory (from docs/openapi/audio)

### 3.1 Chat Completion API

- `POST /v1/chat/completions`
- `GET /v1/chat/completions`
- `GET /v1/chat/completions/{completion_id}`
- `POST /v1/chat/completions/{completion_id}`
- `DELETE /v1/chat/completions/{completion_id}`
- `GET /v1/chat/completions/{completion_id}/messages`

### 3.2 Audio API

- `POST /v1/audio/speech`
- `POST /v1/audio/transcriptions`
- `POST /v1/audio/translations`
- `POST /v1/audio/voices`
- `GET /v1/audio/voice_consents`
- `POST /v1/audio/voice_consents`
- `GET /v1/audio/voice_consents/{consent_id}`
- `POST /v1/audio/voice_consents/{consent_id}`
- `DELETE /v1/audio/voice_consents/{consent_id}`

### 3.3 Realtime API

- `POST /v1/realtime/client_secrets`
- `POST /v1/realtime/calls/{call_id}/accept`
- `POST /v1/realtime/calls/{call_id}/hangup`
- `POST /v1/realtime/calls/{call_id}/refer`
- `POST /v1/realtime/calls/{call_id}/reject`
- `POST /v1/realtime/sessions`
- `POST /v1/realtime/transcription_sessions`

### 3.4 Additional compatibility utility route

- `GET /v1/models` (not listed in those docs but required by OpenAI-compatible clients and requested behavior)

### 3.5 Operational routes

- `GET /health` (liveness)
- `GET /ready` (readiness: model/config checks)

---

## 4) Support Matrix (Phase Target)

### Fully implemented in this refactor

1. `GET /v1/models`
2. `POST /v1/audio/speech`
3. `POST /v1/audio/transcriptions`
4. `POST /v1/audio/translations`

### Present but returning structured `501 not_implemented`

- All chat completion endpoints listed above.
- All voice/voice-consent endpoints.
- All realtime endpoints.

This guarantees route compatibility now, while exposing clear unsupported behavior.

---

## 5) Architecture and Module Design

Target package layout:

```text
qwen-tts-api/
  app/
    __init__.py
    main.py                      # app factory + router wiring
    config.py                    # settings from env
    logging.py                   # structured logging setup
    errors.py                    # OpenAI-compatible error payloads/handlers
    dependencies.py              # shared dependencies/injections
    schemas/
      __init__.py
      common.py                  # shared response schemas
      models.py                  # /v1/models schemas
      speech.py                  # speech request schemas
      transcription.py           # transcription/translation schemas
      stubs.py                   # generic not implemented schemas if needed
    services/
      __init__.py
      model_registry.py          # model metadata + validation
      tts_service.py             # qwen TTS speech generation
      stt_service.py             # transcription/translation abstraction
      not_implemented.py         # helper for 501 errors
    routers/
      __init__.py
      health.py                  # /health, /ready
      models.py                  # /v1/models
      audio.py                   # speech/transcriptions/translations + stubs
      chat.py                    # chat completion stubs
      realtime.py                # realtime stubs
  tests/
    conftest.py
    test_config.py
    test_models_endpoint.py
    test_audio_speech.py
    test_audio_transcription_translation.py
    test_stubs.py
  PLAN.md
  main.py                        # tiny launcher importing app.main:app
```

### Why this structure

- **Single responsibility**: routers handle HTTP; services handle domain behavior.
- **Dependency inversion**: routers depend on service interfaces/abstractions, not implementation details.
- **Open/closed**: unsupported endpoints can later transition from stub router handlers to real service calls with minimal route churn.
- **DRY**: shared error envelope and validation logic centralized.

---

## 6) Environment Configuration Specification

Configuration source: environment variables.

### 6.1 Core server

- `APP_NAME` (default: `qwen-tts-api`)
- `APP_ENV` (default: `development`)
- `APP_LOG_LEVEL` (default: `INFO`)
- `APP_HOST` (default: `0.0.0.0`)
- `APP_PORT` (default: `8000`)

### 6.2 Model settings

- `QWEN_TTS_MODEL_DEFAULT`
  - default model id used when request omits model
  - example default: `Qwen/Qwen3-TTS-12Hz-0.6B-Base`

- `QWEN_TTS_MODEL_ALLOWED`
  - comma-separated list of allowed model identifiers for speech path
  - if empty, only default is allowed

- `QWEN_STT_MODEL_DEFAULT`
  - default model id for transcription/translation requests
  - can be mapped to local implementation choice

- `QWEN_STT_MODEL_ALLOWED`
  - comma-separated list of allowed STT models

### 6.3 Voice mapping

- `QWEN_TTS_VOICE_MAP_JSON` (required for speech)
  - JSON object keyed by voice name
  - value schema:
    - `ref_audio_path` (required)
    - `ref_text` (optional but recommended)
    - `language` (required)

Example:

```json
{
  "alloy": {
    "ref_audio_path": "/opt/voices/alloy.wav",
    "ref_text": "Reference sentence",
    "language": "Italian"
  },
  "nova": {
    "ref_audio_path": "/opt/voices/nova.wav",
    "ref_text": "Another sentence",
    "language": "English"
  }
}
```

### 6.4 Feature flags

- `FEATURE_ENABLE_CHAT` (default: `false`)
- `FEATURE_ENABLE_REALTIME` (default: `false`)
- `FEATURE_ENABLE_VOICE_CONSENTS` (default: `false`)
- `FEATURE_ENABLE_CUSTOM_VOICES` (default: `false`)

> In this phase, these remain disabled and endpoints return 501; flags are future-ready.

---

## 7) API Contract and Error Model

### 7.1 OpenAI-style error envelope

Errors should follow:

```json
{
  "error": {
    "message": "Human readable message",
    "type": "invalid_request_error | not_implemented_error | server_error",
    "param": "field_name_or_null",
    "code": "invalid_parameter | not_implemented | internal_error"
  }
}
```

### 7.2 HTTP status mapping

- `400` invalid request/validation
- `404` route/path resource missing
- `422` semantic validation issues (if needed)
- `500` unexpected internal error
- `501` endpoint exists but currently unsupported

### 7.3 Unsupported route behavior

Each unsupported endpoint returns:

- HTTP `501`
- OpenAI-style payload with:
  - `type = not_implemented_error`
  - `code = not_implemented`
  - clear `message` naming the route

---

## 8) Request/Response Behavior by Implemented Endpoint

### 8.1 `GET /v1/models`

- Returns OpenAI-like list object:
  - `object: list`
  - `data: [ {id, object: model, created, owned_by}, ... ]`
- Includes configured models (speech + stt) and marks compatibility metadata.

### 8.2 `POST /v1/audio/speech`

Input handling:

- Accept OpenAI-compatible fields (`model`, `input`, `voice`, etc.).
- Validate:
  - `input` non-empty
  - `model` allowed
  - `voice` exists in env mapping
  - mapped `ref_audio_path` exists/readable

Behavior:

1. Resolve model -> load/get cached model instance.
2. Resolve voice -> `ref_audio_path`, `ref_text`, `language`.
3. Generate audio using Qwen TTS clone API.
4. Serialize output to requested format where supported initially (`wav` guaranteed; optionally add mp3/opus later with conversion).

Output:

- Binary audio response with proper media type and filename.

### 8.3 `POST /v1/audio/transcriptions`

- Accept multipart input per OpenAI style.
- Initial implementation can provide local transcribe abstraction (if available in backend).
- If backend transcription engine is not available, still keep endpoint and return `501 not_implemented` until engine is wired.
- In this phase we target implementation with whichever local backend is available under service abstraction.

### 8.4 `POST /v1/audio/translations`

- Similar abstraction to transcription.
- If direct translation model unavailable, perform transcribe + translate abstraction if local translator exists; otherwise return 501.
- Keep response shape aligned with OpenAI translation object.

---

## 9) Test-First Strategy

Tests are written before feature code for target behavior.

### 9.1 Unit tests: configuration

- parse env into settings correctly
- invalid/missing `QWEN_TTS_VOICE_MAP_JSON` handling
- allowed model list validation

### 9.2 Contract tests: error envelope

- unsupported routes return `501` and proper envelope
- invalid speech payload returns `400` with param/code

### 9.3 Endpoint tests: models

- `/v1/models` returns list object and includes configured models

### 9.4 Endpoint tests: speech

- valid request with mapped voice succeeds (mock service)
- unknown voice returns error envelope
- unsupported model returns error envelope

### 9.5 Endpoint tests: transcription/translation

- route exists
- expected schema on success path (mock backend)
- fallback/unsupported behavior if backend absent

### 9.6 Stub coverage tests

- all chat/realtime/voice-consent endpoints exist and return 501 envelope

---

## 10) Phased Execution Plan

### Phase 1 — Planning (this file)

- Produce complete architecture and rollout plan.

### Phase 2 — Tests first

- Create test scaffolding and initial failing tests (red).

### Phase 3 — Speech implementation

- Build config/model registry + speech service + `/v1/audio/speech` + `/v1/models`.
- Make speech tests pass.

### Phase 4 — Transcription and translation

- Implement service abstraction and endpoints.
- Make tests pass for supported behavior.

### Phase 5 — Remaining endpoints as structured stubs

- Add chat/realtime/voice routes with consistent 501 envelopes.
- Ensure route existence and tests pass.

### Phase 6 — Final integration and hardening

- Ensure `main.py` launcher works.
- Validate test suite and endpoint consistency.

---

## 11) Implementation Notes and Risks

1. **Backend capability mismatch**
   - Risk: Qwen package may not provide STT/translation directly.
   - Mitigation: abstraction + clear 501 fallback while preserving route compatibility.

2. **Audio format conversion support**
   - Risk: only WAV straightforward initially.
   - Mitigation: guarantee WAV now; add controlled converter layer for additional formats.

3. **Model loading latency**
   - Risk: first request cold start.
   - Mitigation: lazy load + optional eager warmup flag later.

4. **Temporary file cleanup**
   - Risk: disk leaks.
   - Mitigation: use streamed responses and lifecycle cleanup hooks/context managers.

5. **Schema drift from OpenAI docs**
   - Risk: clients rely on strict shapes.
   - Mitigation: explicit pydantic response models and compatibility tests.

---

## 12) Definition of Done

Done when all are true:

1. Project is modularized into app package with routers/services/config.
2. `/v1/models` and `/v1/audio/speech` fully work with env model+voice mapping.
3. `/v1/audio/transcriptions` and `/v1/audio/translations` implemented as supported by backend abstraction; unsupported behavior still OpenAI-style if necessary.
4. All remaining documented routes exist and return structured 501 envelopes.
5. `/health` and `/ready` exist and are usable.
6. Tests cover critical behavior and pass.
7. `main.py` remains a thin startup wrapper.

---

## 13) Next Immediate Step

Next step is to implement **Phase 2 (tests first)** by creating test scaffolding and failing tests for:

- config/env parsing
- `/v1/models`
- `/v1/audio/speech`
- structured 501 stubs for unsupported endpoints
- transcription/translation route presence and contract

# Usefull Links

- https://developers.openai.com/api/docs/guides/audio
