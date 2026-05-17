# llm-tts-api — Configuration, Errors & Schemas

## Purpose
Captures the static data model of the service: environment-driven `Settings`, per-voice `VoiceConfig`, the OpenAI-compatible request/response Pydantic schemas, and the OpenAI-shaped error envelope.

## Participants
- `Settings`, `VoiceConfig` — `src/llm_tts_api/config.py:9-249`
- `ModelRegistry` — `src/llm_tts_api/services/model_registry.py:7-41`
- `OpenAIError`, `OpenAIHTTPException`, factories — `src/llm_tts_api/errors.py:9-75`
- Request/response schemas — `src/llm_tts_api/schemas/{speech,models,transcription,common}.py`
- DI singletons — `src/llm_tts_api/dependencies.py`

## Narrative
`Settings` is constructed once (cached by `dependencies.get_settings` with `lru_cache`) on first request. Its `__post_init__` runs all `_load_*` methods in order: app identity, provider models, STT models, TTS limits, then the voice map loaded from `TTS_VOICE_MAP_FILE`. The voice map produces `VoiceConfig` instances (one per named voice) holding the reference-audio path, reference text, language, generation hyperparameters and target RMS.

`ModelRegistry` is a thin facade over `Settings` exposing model/provider validation and listing for the `/v1/models` endpoint.

Errors flow through `OpenAIHTTPException`, which carries an `OpenAIError` payload serialized under `{"error": {...}}` — matching the OpenAI API shape. The `invalid_request`, `not_implemented`, and `internal_error` helpers in `errors.py` are the only places these are constructed.

The schema layer is the public contract: `SpeechRequest` (POST body), `ModelListResponse` (GET /v1/models), and placeholder transcription schemas. `ErrorEnvelope` wraps `ErrorDetail` for all error responses.

## Diagram

```mermaid
classDiagram
    class Settings {
        +app_name: str
        +app_env: str
        +app_log_level: str
        +tts_provider: str
        +tts_model_default: str
        +tts_model_allowed: list
        +tts_mlx_audio_model_allowed: list
        +tts_voxtral_model_allowed: list
        +tts_vllm_omni_model_allowed: list
        +stt_model_default: str
        +tts_max_input_chars: int
        +tts_max_concurrent_requests: int
        +tts_voice_map: dict~str, VoiceConfig~
        +tts_model_default_for_provider(name)
        +tts_model_allowed_for_provider(name)
    }

    class VoiceConfig {
        +ref_audio_path: str
        +ref_text: str
        +language: str
        +number_lang: str
        +temperature: float
        +top_p: float
        +target_db: float
        +max_sentences_per_chunk: int
    }

    class ModelRegistry {
        +list_models() list~ModelObject~
        +is_allowed_tts_model(model, provider) bool
        +resolve_tts_model(model)
        +resolve_tts_target(model, provider)
    }

    class OpenAIError {
        +message: str
        +type: str
        +code: str
        +param: str
        +as_dict() dict
    }

    class OpenAIHTTPException {
        +__init__(status, error)
    }

    class SpeechRequest {
        +model: str
        +input: str
        +voice: str
        +provider: str
        +response_format: str
        +instructions: str
        +speed: float
        +stream_format: str
        +normalize_db: float
    }

    class ModelObject {
        +id: str
        +object = "model"
        +created: int
        +owned_by: str
    }

    class ModelListResponse {
        +object = "list"
        +data: list~ModelObject~
    }

    class ErrorDetail {
        +message: str
        +type: str
        +param: str
        +code: str
    }

    class ErrorEnvelope {
        +error: ErrorDetail
    }

    Settings *-- "many" VoiceConfig
    ModelRegistry --> Settings
    OpenAIHTTPException *-- OpenAIError
    ModelListResponse *-- "many" ModelObject
    ErrorEnvelope *-- ErrorDetail
```

## Notes
- All Settings fields are loaded from env vars (`TTS_*`, `APP_*`, `STT_*`). See `Settings._load_provider_models` for the per-provider model allow-list pattern.
- `VoiceConfig.ref_audio_path` must exist on disk; verified by `SpeechRequestResolver._resolve_voice` at request time, not at startup.
- The DI module wires `get_settings → get_model_registry → get_tts_service` as a singleton chain via `lru_cache(maxsize=1)`.
