# llm-tts-api — Architecture Diagrams

Mermaid class and sequence diagrams for the TTS service.

## Class Diagrams
- [Service Overview](class/overview.md) — `TTSService`, resolver, synthesizer, response factory.
- [Providers](class/providers.md) — `TTSProviderStrategy`, `CachedModelProvider`, MLX / Voxtral / vLLM Omni, voice-args.
- [Config & Schemas](class/config-and-schemas.md) — `Settings`, `VoiceConfig`, `ModelRegistry`, error envelope, OpenAI-shaped schemas.

## Sequence Diagrams
- [Startup & DI Wiring](sequence/startup.md)
- [Health & Readiness](sequence/health-and-ready.md)
- [List Models](sequence/list-models.md)
- [Create Speech (POST /v1/audio/speech)](sequence/create-speech.md)
- [Provider: MLX Audio](sequence/provider-mlx-audio.md)
- [Provider: Voxtral](sequence/provider-voxtral.md)
- [Provider: vLLM Omni](sequence/provider-vllm-omni.md)

## Conventions
- Class diagrams show only public/structural members.
- Sequence diagrams cover one happy path plus important alt/opt branches; errors flow through `OpenAIHTTPException`.
- Every element references source via `file:line` in the **Participants** section of each doc.
