# TTS — Startup & Lifespan

## Purpose
Post-Sprint-3/5, `main.create_app` builds the FastAPI app and a lifespan that constructs every singleton via `build_default_dependencies`, runs the initial seed-ingestion pass, optionally preloads models, flips `ready=True`, then on shutdown drains in-flight synthesis.

## Participants
- `_load_default_env_files`, `create_app`, `lifespan` — `src/llm_tts_api/main.py:113-227`
- `build_default_dependencies` — `dependencies.py`
- `Settings.__post_init__` — `config.py`
- `select_provider` (S-006 auto-detection) — `services/tts_providers/auto_select.py`
- `VoiceSeedIngestor.ingest_once`, `watch_and_ingest` — `services/voice_store/seed_ingestion.py`
- `_emit_low_memory_warning` (FR-HL-05), `_drain_concurrency` (FR-HL-04) — `main.py`

## Narrative
**Startup.** `lifespan` calls `build_default_dependencies`, which: parses `Settings`, detects the `DeviceProfile` (S-005), selects a provider (S-006), constructs the model registry + cache + provider registry, wires the voice metadata + blob repos (FS / Postgres / S3 per env), and assembles the seed ingestor. The lifespan stashes everything on `app.state`, runs `voice_seed_ingestor.ingest_once()` so the very first `GET /v1/tts/voices` reflects the seed file (UAT-VM-01), then — if `TTS_VOICE_MAP_FILE` is set — spawns the `watchfiles` task so file edits hot-reload (≤ 2 s, NFR-OP-05). After an advisory low-memory warning (FR-HL-05), `ready=True` flips on.

**Shutdown.** The `finally` block flips `ready=False` and `ready_reason="draining"` first to reject new traffic, cancels the seed watcher task, then `_drain_concurrency` waits up to `TTS_SHUTDOWN_DRAIN_SECONDS` for the concurrency semaphore to fully release (FR-HL-04). Drain is passive — it polls `Semaphore._value` rather than re-acquiring (which would race with a queued waiter).

When `LLM_TTS_API_TEST_NO_LIFESPAN=1` is set, the entire construction block is skipped; the test fixture is responsible for populating `app.state`.

## Diagram

```mermaid
sequenceDiagram
    autonumber
    participant Uvicorn
    participant Life as lifespan()
    participant Deps as build_default_dependencies
    participant Cfg as Settings
    participant Auto as select_provider (S-006)
    participant Seed as VoiceSeedIngestor

    Uvicorn->>Life: enter
    alt LLM_TTS_API_TEST_NO_LIFESPAN unset
        Life->>Deps: build_default_dependencies()
        Deps->>Cfg: Settings()
        Cfg->>Cfg: _load_app_identity / providers / stt / limits / runtime knobs
        Cfg->>Cfg: _load_voice_store_dir / metadata_backend / blob_backend
        Cfg->>Cfg: _load_voice_map_from_file (legacy slot)
        Cfg-->>Deps: settings
        Deps->>Deps: detect_device → DeviceProfile
        Deps->>Auto: select_provider(profile, registry, override=TTS_PROVIDER)
        Auto-->>Deps: ProviderSelection(name, device, source)
        Deps->>Deps: wire model_registry, model_cache, provider_registry
        Deps->>Deps: wire voice_metadata_repo + voice_blob_repo
        Deps->>Deps: build VoiceSeedIngestor
        Deps-->>Life: deps bundle
        Life->>Life: app.state.* = deps.*
        Life->>Seed: ingest_once()
        Seed-->>Life: count
        opt TTS_VOICE_MAP_FILE set
            Life->>Seed: asyncio.create_task(watch_and_ingest())
            Seed-->>Life: seed_watcher_task
        end
        Life->>Life: _emit_low_memory_warning(TTS_MIN_FREE_MEMORY_GB)
        Life->>Life: app.state.ready = True
        Life->>Life: app.state.ready_reason = "ready"
    else test bypass
        Life-->>Life: skip construction; fixture populates app.state
    end
    Life-->>Uvicorn: yield (serving)

    Note over Life: --- shutdown ---
    Uvicorn->>Life: exit
    Life->>Life: app.state.ready = False
    Life->>Life: app.state.ready_reason = "draining"
    opt seed watcher running
        Life->>Seed: task.cancel(); await suppress
    end
    Life->>Life: _drain_concurrency(app, TTS_SHUTDOWN_DRAIN_SECONDS)
    Life-->>Uvicorn: done
```

## Notes
- `Settings.__post_init__` is fail-fast: a bad env var raises `ValueError` and the lifespan propagates it so uvicorn aborts startup with a readable message.
- The S-006 auto-selection runs against the registry's iteration order; the first provider declaring support for the detected device wins. `TTS_PROVIDER` overrides but is still validated against `supports_devices`.
- `_drain_concurrency` reads `Semaphore._value` directly (the CPython internal counter); the alternative — subclassing `Semaphore` — was rejected upstream in S-007.
- See [health-and-ready.md](health-and-ready.md) for the runtime view of the `ready` flag.
