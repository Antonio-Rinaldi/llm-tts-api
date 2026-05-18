# llm-tts-api — Configuration, Errors & Schemas

## Purpose
Captures the static data model of the post-Sprint-5 service: `Settings` (env-driven), per-voice `VoiceConfig`, the rich `SynthesizeRequest` Pydantic schema, the OpenAI-compatible `SpeechRequest`, the voice-CRUD `Voice*` schemas, and the typed OpenAI-shaped error envelope.

## Participants
- `Settings`, `VoiceConfig`, `PreloadEntry` — `src/llm_tts_api/config.py`
- `ModelRegistry`, `ModelCache` — `src/llm_tts_api/services/{model_registry,model_cache}.py`
- `OpenAIError`, `OpenAIHTTPException`, `ERROR_CODES`, factories — `src/llm_tts_api/errors.py`
- Request schemas — `schemas/{speech,synthesis,voices,models,common}.py`
- DI singletons — `src/llm_tts_api/dependencies.py`

## Narrative
`Settings` is constructed once at lifespan startup (via `build_default_dependencies`). `__post_init__` runs `_load_app_identity → _load_provider_models → _load_stt_models → _load_tts_limits → _load_runtime_knobs → _load_voice_store_dir → _load_voice_metadata_backend → _load_voice_blob_backend → _load_int(TTS_REFAUDIO_MAX_BYTES) → _load_voice_map_from_file`. The full env-var inventory is enumerated in the README and pinned by `tests/test_docs_inventory.py`.

The rich endpoint accepts `SynthesizeRequest` (`extra="forbid"`); the OpenAI adapter accepts `SpeechRequest` and translates it field-by-field into `SynthesizeRequest` before delegating to `synthesize_core`. Voice-CRUD uses `VoiceCreate` / `VoiceUpdate` (multipart `metadata` part) and emits `VoiceResponse` / `VoiceSummary` / `VoiceListResponse`.

Errors flow through `OpenAIHTTPException` carrying an `OpenAIError`. The `ERROR_CODES` constant in `errors.py` declares the closed set of types (`validation_error`, `voice_error`, `provider_error`, `capacity_error`, `internal_error`) and the documented sub-codes. The handler renders the envelope (with the active request id) and sets `X-Error-Code` (FR-ER-03).

## Diagram

```mermaid
classDiagram
    class Settings {
        +app_name: str
        +app_env: str
        +app_log_level: str
        +app_log_format: str
        +tts_provider: str
        +tts_model_default: str
        +tts_model_allowed: list~str~
        +tts_mlx_audio_model_default: str
        +tts_mlx_audio_model_allowed: list~str~
        +tts_voxtral_model_default: str
        +tts_voxtral_model_allowed: list~str~
        +tts_vllm_omni_model_default: str
        +tts_vllm_omni_model_allowed: list~str~
        +stt_model_default: str
        +stt_model_allowed: list~str~
        +tts_max_input_chars: int
        +tts_max_concurrent_requests: int
        +tts_max_queue_depth: int
        +tts_model_cache_size: int
        +tts_preload_models: list~PreloadEntry~
        +tts_inference_timeout_seconds: float
        +tts_shutdown_drain_seconds: int
        +tts_min_free_memory_gb: int
        +tts_device: str
        +tts_dtype: str
        +tts_voice_store_dir: Path
        +tts_voice_metadata_backend: str
        +tts_voice_metadata_dsn: str
        +tts_voice_blob_backend: str
        +tts_voice_blob_s3_bucket: str
        +tts_voice_blob_s3_endpoint: str
        +tts_voice_blob_s3_region: str
        +tts_refaudio_max_bytes: int
        +tts_voice_map: dict~str, VoiceConfig~
    }

    class PreloadEntry {
        +provider: str
        +model: str
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

    class SynthesizeRequest {
        <<extra=forbid>>
        +input: str
        +voice: str|null
        +provider: str|null
        +model: str|null
        +response_format: Literal[wav]
        +stream: bool
        +normalize_db: float|null
        +max_sentences_per_chunk: int|null
        +language: str|null
        +number_lang: str|null
        +temperature: float|null
        +top_p: float|null
    }

    class SpeechRequest {
        +model: str
        +input: str
        +voice: str
        +provider: str|null
        +response_format: str
        +instructions: str|null
        +speed: float|null
        +stream_format: str|null
        +normalize_db: float|null
    }

    class VoiceCreate {
        <<extra=forbid>>
        +id: str
        +transcript: str
        +language: str
        +consent_acknowledged: bool
        +number_lang: str
        +target_db: float
        +temperature: float
        +top_p: float
        +max_sentences_per_chunk: int
    }

    class VoiceUpdate {
        <<extra=forbid>>
        +transcript: str
        +language: str
        +consent_acknowledged: bool
        +...
    }

    class VoiceResponse
    class VoiceSummary
    class VoiceListResponse

    class OpenAIError {
        +message: str
        +type: ErrorCategory
        +code: str
        +param: str|null
        +as_envelope(request_id) dict
    }

    class OpenAIHTTPException {
        +status_code: int
        +error: OpenAIError
    }

    class ERROR_CODES {
        <<constant>>
        validation_error: frozenset
        voice_error: frozenset
        provider_error: frozenset
        capacity_error: frozenset
        internal_error: frozenset
    }

    Settings *-- "many" VoiceConfig
    Settings *-- "many" PreloadEntry
    OpenAIHTTPException *-- OpenAIError
    OpenAIError ..> ERROR_CODES : type/code drawn from
    VoiceListResponse o-- VoiceSummary
```

## Notes
- `Settings.__post_init__` validates every env var fail-fast at startup — typos and out-of-range values raise `ValueError` with the env-var name in the message.
- The `tests/test_docs_inventory.py` test walks `config.py` via AST and asserts the README documents every env var; same test walks `ERROR_CODES` and asserts each `(type, code)` is in the README.
- `SynthesizeRequest` and `VoiceCreate` both pin `extra="forbid"` (NFR-MT-04) so unknown fields surface as `validation_error.invalid_parameter` rather than being silently dropped.
- The `request_id` field of the envelope is injected at render time by `openai_exception_handler` (via the S-004 contextvar) — call sites don't thread the id manually.
