# TTS — Create Speech (POST /v1/audio/speech)

## Purpose
End-to-end happy path for the only fully-implemented endpoint. Captures the resolve → synthesize → respond pipeline including text preprocessing, chunked synthesis, RMS normalization and WAV concatenation.

## Participants
- `create_speech` — `routers/audio.py:22-29`
- `TTSService.create_speech` — `services/tts_service.py:269-297`
- `SpeechRequestResolver` — `tts_service.py:101-181`
- `SpeechSynthesizer` — `tts_service.py:184-216`
- `SpeechResponseFactory` — `tts_service.py:219-241`
- `text_preprocessing.preprocess_for_tts`, `split_text_semantic` — `services/text_preprocessing.py`
- `audio_postprocessing.normalize_wav_rms` — `services/audio_postprocessing.py:17-65`
- Provider strategy — see [provider-mlx-audio.md](provider-mlx-audio.md) and siblings

## Narrative
The router receives a `SpeechRequest` and a `stream` query flag. `TTSService.create_speech` runs in three phases:

1. **Resolve.** The resolver validates `input` non-empty, resolves `(model, provider)` via `ModelRegistry.resolve_tts_target`, asserts the model is in that provider's allow-list, looks up the `VoiceConfig`, confirms the reference audio exists, locks the response format to `wav`, then preprocesses (`preprocess_for_tts` → punctuation cleanup, date expansion, number expansion) and chunks the text (`split_text_semantic`). Output: `ResolvedSpeechRequest`.
2. **Synthesize.** The synthesizer acquires a class-level semaphore (`tts_max_concurrent_requests`), pulls the provider out of the registry, calls `synthesize_chunks(SynthesisRequest)`, runs each chunk through `normalize_wav_rms(target_db=voice.target_db)`, and concatenates the WAV byte-strings.
3. **Respond.** `SpeechResponseFactory.build` either streams the bytes back or writes to a temp file with `BackgroundTask(cleanup_temp_file)`.

Validation errors raise `OpenAIHTTPException(400)`; provider errors propagate as `OpenAIHTTPException(500)`.

## Diagram

```mermaid
sequenceDiagram
    autonumber
    participant Client
    participant Router as routers/audio
    participant TTS as TTSService
    participant Res as SpeechRequestResolver
    participant MR as ModelRegistry
    participant TP as text_preprocessing
    participant Syn as SpeechSynthesizer
    participant Reg as TTSProviderRegistry
    participant Prov as Provider
    participant AP as audio_postprocessing
    participant Fac as SpeechResponseFactory

    Client->>Router: POST /v1/audio/speech (SpeechRequest, ?stream=)
    Router->>TTS: create_speech(request, stream)

    rect rgb(245,245,255)
        Note over TTS,Res: Phase 1 — Resolve
        TTS->>Res: resolve(request)
        Res->>Res: _ensure_input_present()
        Res->>MR: resolve_tts_target(model, provider)
        MR-->>Res: (model_name, provider)
        Res->>Res: _ensure_model_allowed()
        Res->>Res: _resolve_voice() (VoiceConfig + ref file exists)
        Res->>Res: _resolve_response_format() → "wav"
        Res->>TP: preprocess_for_tts(text, lang)
        TP-->>Res: normalized text
        Res->>TP: split_text_semantic(normalized, ...)
        TP-->>Res: chunks
        Res-->>TTS: ResolvedSpeechRequest
    end

    rect rgb(245,255,245)
        Note over TTS,AP: Phase 2 — Synthesize (semaphore-bounded)
        TTS->>Syn: generate(resolved)
        Syn->>Syn: acquire _synthesis_semaphore
        Syn->>Reg: get(resolved.provider)
        Reg-->>Syn: provider strategy
        Syn->>Prov: synthesize_chunks(SynthesisRequest)
        Prov-->>Syn: list[wav_bytes]
        loop for each chunk
            Syn->>AP: normalize_wav_rms(chunk, target_db)
            AP-->>Syn: normalized_chunk
        end
        Syn->>Syn: _concat_wav_bytes(chunks)
        Syn->>Syn: release semaphore
        Syn-->>TTS: wav_bytes
    end

    rect rgb(255,250,240)
        Note over TTS,Fac: Phase 3 — Respond
        TTS->>Fac: build(wav_bytes, stream)
        alt stream=true
            Fac-->>TTS: StreamingResponse(BytesIO)
        else stream=false
            Fac->>Fac: tempfile.mkstemp(); write
            Fac-->>TTS: FileResponse(+ BackgroundTask cleanup)
        end
    end

    TTS-->>Router: Response
    Router-->>Client: 200 audio/wav
```

## Notes
- Error envelope follows OpenAI shape; all `OpenAIHTTPException` instances serialize as `{"error": {...}}`.
- Concurrency cap is `Settings.tts_max_concurrent_requests` (default 1) — beyond that, requests await the semaphore.
- Streaming returns the full WAV in one chunk; this is not progressive synthesis.
