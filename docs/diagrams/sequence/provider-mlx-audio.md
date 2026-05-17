# TTS — MLX Audio Provider Synthesis

## Purpose
Per-chunk synthesis with `mlx-audio`. Supports both reference-audio cloning and named voices, with an `AssertionError` clone fallback for models that disagree about supported kwargs.

## Participants
- `MLXAudioTTSProvider` — `services/tts_providers/mlx_audio_provider.py:20-115`
- `CachedModelProvider._get_model`, `_get_model_lock` — `cached_model_provider.py:16-49`
- `voice_args.select_voice_args`, `build_generation_args` — `voice_args.py:59-101`

## Narrative
`synthesize_chunks` first resolves (and caches) the model under a per-model `threading.Lock`. Inspecting `inspect.signature(model.generate).parameters` lets the provider only pass kwargs the model actually accepts. The voice-args helper produces a primary args dict (cloning preferred) and a fallback dict (named voice → ref audio). For each chunk, the provider calls `model.generate(**args)`; on `AssertionError`, if a named voice was used and a clone fallback is available, it retries with the fallback. Audio samples come back, `soundfile.write` to a `BytesIO` produces the chunk's WAV bytes.

## Diagram

```mermaid
sequenceDiagram
    autonumber
    participant Syn as SpeechSynthesizer
    participant Prov as MLXAudioTTSProvider
    participant Cache as CachedModelProvider
    participant VA as voice_args
    participant Model as mlx-audio model
    participant SF as soundfile

    Syn->>Prov: synthesize_chunks(request)
    Prov->>Cache: _get_model(model_name)
    alt cache miss
        Cache->>Prov: _load_model(name)
        Prov-->>Cache: model
    end
    Cache-->>Prov: model
    Prov->>Cache: _get_model_lock(model_name)
    Cache-->>Prov: lock

    Note over Prov: acquire model lock (thread-safe)
    Prov->>Prov: _signature_params(model)
    Prov->>VA: select_voice_args(voice, params, prefer_clone=True, require_any=True)
    VA-->>Prov: VoiceArgsSelection(primary, fallback, used_named_voice)
    Prov->>VA: build_generation_args(opts, params)
    VA-->>Prov: gen_args

    loop for each chunk
        Prov->>Model: model.generate(text=chunk, **primary, **gen_args)
        alt AssertionError + named voice + fallback available
            Prov->>Model: retry with fallback args
        end
        Model-->>Prov: result(audio, sample_rate)
        Prov->>SF: write(BytesIO, audio, sr, format="WAV")
        SF-->>Prov: wav_bytes
    end
    Prov-->>Syn: list[wav_bytes]
```

## Notes
- Used when `Settings.tts_provider == "mlx_audio"`.
- The fallback retry is the only place a provider second-guesses its voice selection.
