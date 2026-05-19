# llm-tts-api — Service Overview

## Purpose
Top-level composition of the post-Sprint-5 service. The rich endpoint (`POST /v1/tts/synthesize`) and the OpenAI adapter (`POST /v1/audio/speech`) both delegate to the single service-layer entry point `services/synthesize_service.synthesize_core` (BR-9, NFR-PT-03b). The voice store sits behind two Protocols (see [voice-store.md](voice-store.md)); the provider layer is auto-selected from the `DeviceProfile` (see [providers.md](providers.md)).

## Participants
- `create_app`, lifespan — `src/llm_tts_api/main.py:136-227`
- DI getters — `src/llm_tts_api/dependencies.py`
- `synthesize_core` (the single synthesis pipeline) — `src/llm_tts_api/services/synthesize_service.py`
- Router handlers — `routers/{health,models,audio,synthesize,voices,chat,realtime}.py`
- `Settings`, `VoiceConfig` — `config.py`
- `TTSProviderRegistry`, `ProviderSelection`, `DeviceProfile` — `services/tts_providers/`
- `ModelCache`, `ModelRegistry` — `services/`
- `VoiceMetadataRepository`, `VoiceBlobRepository`, `VoiceSeedIngestor` — `services/voice_store/`

## Narrative
The FastAPI lifespan constructs every collaborator once via `build_default_dependencies` and stashes them on `app.state`. Routers receive them through `Annotated[..., Depends(get_*)]`. The `ready` flag flips True only after the seed-ingestion pass runs (so a startup `GET /v1/tts/voices` already reflects the seed file). On shutdown the flag flips False first, the seed watcher task is cancelled, and `_drain_concurrency` waits up to `TTS_SHUTDOWN_DRAIN_SECONDS` for in-flight synthesis to release the concurrency semaphore.

`routers/synthesize.py` (rich) and `routers/audio.py` (OpenAI) are both **thin wrappers** over `synthesize_core`. The rich router passes the raw `SynthesizeRequest`; the OpenAI router maps `SpeechRequest → SynthesizeRequest` first and then strips the `X-Provider` / `X-Model` / `X-Device` / `X-Dtype` / `X-Voice-Source` / `X-Voice-Id` / `X-Chunks` / `X-Total-Duration-Ms` headers from the response so the OpenAI shape stays byte-identical (FR-OA-01..03; NFR-PT-03b paired UAT). The handlers MUST NOT import each other's internals — `tests/test_openai_adapter.py` enforces this with a static check.

## Diagram

```mermaid
classDiagram
    class FastAPIApp {
        <<FastAPI>>
        +state: AppState
        +lifespan
    }

    class AppState {
        +settings: Settings
        +device_profile: DeviceProfile
        +provider_selection: ProviderSelection
        +provider_registry: TTSProviderRegistry
        +model_registry: ModelRegistry
        +model_cache: ModelCache
        +model_locks: dict
        +concurrency_semaphore: asyncio.Semaphore
        +queue_semaphore: asyncio.Semaphore
        +voice_metadata_repo: VoiceMetadataRepository
        +voice_blob_repo: VoiceBlobRepository
        +voice_seed_ingestor: VoiceSeedIngestor
        +preset_registry: PresetRegistry
        +preset_registry_reloader: PresetRegistryReloader
        +tts_service: TTSService
        +ready: bool
        +ready_reason: str
    }

    class SynthesizeRouter {
        <<router /v1/tts/synthesize>>
        +synthesize(payload, request)
    }

    class AudioRouter {
        <<router /v1/audio>>
        +create_speech(payload, request)
        +stripped_headers = _RICH_ONLY_HEADERS
    }

    class VoicesRouter {
        <<router /v1/tts/voices>>
        +list_voices(repo)
        +create_voice(metadata, audio)
        +get_voice(voice_id)
        +get_voice_audio(voice_id)
        +update_voice(voice_id, metadata, audio)
        +delete_voice(voice_id)
    }

    class HealthRouter {
        <<router>>
        +health(request) dict
        +ready(request) JSONResponse
    }

    class synthesize_core {
        <<function>>
        +synthesize_core(payload, request, settings, provider_registry, provider_selection, device_profile, metadata_repo, blob_repo, preset_snapshot) Response
    }

    class PresetRegistry {
        <<dataclass frozen>>
        +get(name) PresetEntry|None
        +names() frozenset
    }

    class PresetRegistryReloader {
        +watch() None
        +reload_once() None
    }

    class TTSProviderRegistry {
        +get(name) TTSProviderStrategy
        +find(name) TTSProviderStrategy
        +all() Iterator
        +names() list
    }

    class VoiceMetadataRepository {
        <<Protocol>>
    }

    class VoiceBlobRepository {
        <<Protocol>>
    }

    class VoiceSeedIngestor {
        +ingest_once() int
        +watch_and_ingest() None
    }

    FastAPIApp *-- AppState
    AppState *-- VoiceSeedIngestor
    AppState *-- PresetRegistry
    AppState *-- PresetRegistryReloader
    SynthesizeRouter ..> synthesize_core : delegates
    AudioRouter ..> synthesize_core : delegates (post-translate, post-strip)
    VoicesRouter ..> VoiceMetadataRepository : reads/writes
    VoicesRouter ..> VoiceBlobRepository : reads/writes
    synthesize_core ..> TTSProviderRegistry : looks up provider
    synthesize_core ..> VoiceMetadataRepository : reads voice
    synthesize_core ..> VoiceBlobRepository : reads ref audio
    synthesize_core ..> PresetRegistry : resolve_preset(request, snapshot, settings)
    PresetRegistryReloader ..> PresetRegistry : produces new snapshot (hot reload)
    VoiceSeedIngestor ..> VoiceMetadataRepository : upserts
    VoiceSeedIngestor ..> VoiceBlobRepository : copies audio
    HealthRouter ..> AppState : reads ready/state
```

## Notes
- One synthesis pipeline (BR-9). The S-017 unification removed `SpeechSynthesizer` / `SpeechRequestResolver` / `SpeechResponseFactory` from the OpenAI path; the same `_RICH_ONLY_HEADERS` constant in `routers/audio.py` lists exactly which response headers are stripped.
- `_drain_concurrency` (in `main.py`) waits passively on the semaphore counter rather than re-acquiring (which would race with a queued waiter).
- Schemas + error envelope: [config-and-schemas.md](config-and-schemas.md).
- Voice-store details: [voice-store.md](voice-store.md). Provider strategy details: [providers.md](providers.md).
- Runtime sequences: [../sequence/startup.md](../sequence/startup.md), [../sequence/synthesize-rich.md](../sequence/synthesize-rich.md), [../sequence/create-speech.md](../sequence/create-speech.md), [../sequence/voice-crud.md](../sequence/voice-crud.md), [../sequence/voice-seed-ingestion.md](../sequence/voice-seed-ingestion.md), [../sequence/preset-resolution.md](../sequence/preset-resolution.md), [../sequence/preset-hot-reload.md](../sequence/preset-hot-reload.md).
- Preset class shape: [presets.md](presets.md) — `PresetConfig`, `PresetEntry`, `PresetDefaults` (HF-2 expansion: `language` / `number_lang` / `voice`), `PresetRegistry`, resolver, reloader.
