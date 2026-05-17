# TTS — List Models (GET /v1/models)

## Purpose
Resolve the union of allowed TTS models across providers plus the STT model list, return them in OpenAI's model-list envelope.

## Participants
- `list_models` — `src/llm_tts_api/routers/models.py:13-16`
- `ModelRegistry.list_models` — `services/model_registry.py:14-23`
- `Settings.tts_*_model_allowed`, `Settings.stt_model_allowed` — `config.py`

## Narrative
The handler depends on `get_model_registry`, which is a singleton bound to the cached `Settings`. `list_models` collects all allowlists, deduplicates, and emits a `ModelListResponse` with one `ModelObject` per id. There is no I/O.

## Diagram

```mermaid
sequenceDiagram
    autonumber
    participant Client
    participant Router as routers/models
    participant DI as dependencies
    participant MR as ModelRegistry
    participant S as Settings

    Client->>Router: GET /v1/models
    Router->>DI: get_model_registry()
    DI-->>Router: ModelRegistry (cached)
    Router->>MR: list_models()
    MR->>S: tts_mlx_audio_model_allowed
    MR->>S: tts_voxtral_model_allowed
    MR->>S: tts_vllm_omni_model_allowed
    MR->>S: stt_model_allowed
    MR->>MR: deduplicate ids
    MR-->>Router: ModelListResponse(object="list", data=[...])
    Router-->>Client: 200 ModelListResponse
```

## Notes
- Models from disabled providers still appear if their allow-list env var is populated; the registry doesn't filter by active provider.
