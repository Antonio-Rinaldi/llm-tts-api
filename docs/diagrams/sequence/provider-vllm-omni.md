# TTS — vLLM Omni Provider Synthesis

## Purpose
Most flexible of the three providers: tolerates multiple loader import paths, optional voice, multiple possible result payload shapes, and falls back on `AssertionError`.

## Participants
- `VllmOmniTTSProvider` — `services/tts_providers/vllm_omni_provider.py:21-199`
- `CachedModelProvider` — `cached_model_provider.py:16-49`
- `voice_args.select_voice_args`, `build_generation_args` — `voice_args.py:59-101`

## Narrative
Loading uses `_resolve_loader` which probes a list of import paths (`vllm_omni.tts.utils.load`, `vllm_omni.tts.load`, `vllm_omni.load`). Voice selection uses `require_any=False`, so chunks may be generated with no voice args at all (model default voice). `_generate` tries `model.generate(**kwargs)` first, falling back to `model(**kwargs)` if generate is missing but the model is callable. Results are decoded by `_result_to_wav_bytes`, which dispatches on whether the payload is raw `bytes`, has `wav_bytes`, or is a `(audio, sample_rate)` pair.

## Diagram

```mermaid
sequenceDiagram
    autonumber
    participant Syn as SpeechSynthesizer
    participant Prov as VllmOmniTTSProvider
    participant Cache as CachedModelProvider
    participant VA as voice_args
    participant Model as vllm-omni model
    participant Decode as _result_to_wav_bytes

    Syn->>Prov: synthesize_chunks(request)
    Prov->>Cache: _get_model(model_name)
    alt cache miss
        Cache->>Prov: _load_model(name)
        Prov->>Prov: _resolve_loader() probes vllm_omni paths
        Prov-->>Cache: model
    end
    Cache-->>Prov: model
    Prov->>Cache: _get_model_lock(model_name)
    Cache-->>Prov: lock

    Note over Prov: acquire model lock
    Prov->>Prov: _signature_params(model)
    Prov->>VA: select_voice_args(voice, params, require_any=False)
    VA-->>Prov: VoiceArgsSelection
    Prov->>VA: build_generation_args(opts, params)
    VA-->>Prov: gen_args

    loop for each chunk
        Prov->>Prov: kwargs = {text=chunk, **primary, **gen_args}
        Prov->>Model: _generate(model, kwargs)
        alt model.generate exists
            Model-->>Prov: result
        else callable model
            Model-->>Prov: result
        end
        alt AssertionError and fallback available
            Prov->>Model: retry with fallback or empty voice args
        end
        Prov->>Decode: _result_to_wav_bytes(result)
        alt raw bytes
            Decode-->>Prov: wav_bytes
        else dict/object with wav_bytes
            Decode-->>Prov: wav_bytes
        else audio + sample_rate
            Decode->>Decode: soundfile.write(BytesIO, ...)
            Decode-->>Prov: wav_bytes
        end
    end
    Prov-->>Syn: list[wav_bytes]
```

## Notes
- Used when `Settings.tts_provider == "vllm-omni"` (note hyphen).
- The "no voice args" branch is what lets vLLM Omni serve default-voice prompts without any user `VoiceConfig` content beyond the entry existing.
