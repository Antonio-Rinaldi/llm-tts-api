# llm-tts-api

OpenAI-compatible local audio API built with FastAPI and pluggable TTS providers.

Supported today:
- `qwen` provider (local `qwen-tts` backend)
- `voxtral` provider (local `mlx-audio` backend)

---

## What this service provides

- OpenAI-style routes under `/v1`
- Voice-cloning speech generation via `POST /v1/audio/speech`
- Model/provider resolution through config + request fields
- Environment-driven setup (`.env` and `.env.local`)
- Compatibility stubs (`501`) for not-yet-implemented routes

---

## Endpoint status

### Implemented
- `GET /health`
- `GET /ready`
- `GET /v1/models`
- `POST /v1/audio/speech`

### Compatibility stubs (`501 not_implemented`)
- `POST /v1/audio/transcriptions`
- `POST /v1/audio/translations`
- `POST /v1/audio/voices`
- `GET/POST /v1/audio/voice_consents`
- `GET/POST/DELETE /v1/audio/voice_consents/{consent_id}`
- Chat routes under `/v1/chat/completions...`
- Realtime routes under `/v1/realtime...`

---

## Project layout

- `src/llm_tts_api/` application package
- `config/` configuration examples (including voice map)
- `voices/` local reference voice audio files
- `examples.http` ready-to-run request examples
- `tests/` unit/integration tests

---

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
```

---

## All ways to launch the API

The app loads `.env` and `.env.local` automatically at startup.

### 1) Launch with uvicorn directly

```bash
uvicorn llm_tts_api.main:app --host 0.0.0.0 --port 8000 --reload --timeout-keep-alive 6000 --workers 4
```

### 2) Launch module entrypoint (debug-friendly)

```bash
python -m llm_tts_api.main
```

### 3) Launch console script from `pyproject.toml`

```bash
llm-tts-api
```

### 4) Multi-worker launch (example)

```bash
uvicorn llm_tts_api.main:app --host 0.0.0.0 --port 8000 --workers 4
```

---

## Health checks

```bash
curl -s http://localhost:8000/health
curl -s http://localhost:8000/ready
```

---

## Configuration: complete reference

Use `.env.local` for real values (recommended).

### Core app settings

- `APP_NAME` (default: `llm-tts-api`)
- `APP_ENV` (default: `development`)
- `APP_LOG_LEVEL` (default: `INFO`)

### TTS model/provider settings

- `TTS_DEFAULT_PROVIDER` (default: `qwen`)
  - Fallback provider when provider cannot be inferred and request omits `provider`
- `TTS_MODEL_DEFAULT` (default: `Qwen/Qwen3-TTS-12Hz-0.6B-Base`)
  - Used when request omits `model`
- `TTS_MODEL_ALLOWED` (csv)
  - Allow-list for `/v1/audio/speech` model validation
- `TTS_PROVIDER_MODEL_PREFIXES` (JSON object)
  - Provider inference map: `provider -> [model_prefixes]`
  - Example:
    ```bash
    TTS_PROVIDER_MODEL_PREFIXES={"voxtral":["mistralai/"],"qwen":["qwen/"]}
    ```

### STT model settings (for stub compatibility)

- `STT_MODEL_DEFAULT` (default: `whisper-1`)
- `STT_MODEL_ALLOWED` (default: `whisper-1`)

### Speech generation limits

- `TTS_MAX_INPUT_CHARS` (default: `4096`, must be `>= 256`)
  - Input text is semantically chunked when too long

### Voice map

- `TTS_VOICE_MAP_FILE` (required)
  - Path to JSON file mapping voice name to voice metadata

---

## Recommended `.env.local` (full example)

```bash
APP_NAME=llm-tts-api
APP_ENV=development
APP_LOG_LEVEL=INFO

TTS_DEFAULT_PROVIDER=qwen
TTS_MODEL_DEFAULT=Qwen/Qwen3-TTS-12Hz-0.6B-Base
TTS_MODEL_ALLOWED=Qwen/Qwen3-TTS-12Hz-0.6B-Base,mistralai/Voxtral-Mini-3B-2507
TTS_PROVIDER_MODEL_PREFIXES={"voxtral":["voxtral/","mistral/","mistralai/"],"qwen":["qwen/"]}

STT_MODEL_DEFAULT=whisper-1
STT_MODEL_ALLOWED=whisper-1

TTS_MAX_INPUT_CHARS=4096
TTS_VOICE_MAP_FILE=./config/voice_map.local.json
```

---

## Model/provider selection behavior

`POST /v1/audio/speech` resolves model/provider in this order:

1. `model`: request `model` or fallback `TTS_MODEL_DEFAULT`
2. `provider`:
   - if request `provider` is provided, use it
   - else if request `model` is provided, infer from `TTS_PROVIDER_MODEL_PREFIXES`
   - else fallback `TTS_DEFAULT_PROVIDER`
3. Validate `model` is in `TTS_MODEL_ALLOWED`
4. Dispatch to the provider strategy implementation

This means you can select any supported model id dynamically per request, without hardcoding model names in code.

---

## Speech request parameters (`POST /v1/audio/speech`)

Body fields:

- `model` (string, required by schema; can be defaulted by server logic if omitted upstream)
- `provider` (optional, `qwen`/`voxtral` or any provider registered in strategy registry)
- `input` (string, required)
- `voice` (string, must exist in `TTS_VOICE_MAP_FILE`)
- `response_format` (currently only `wav` is supported)
- `instructions` (accepted by schema)
- `speed` (accepted by schema)
- `stream_format` (accepted by schema)

Query params:

- `stream` (boolean, default `false`)
  - `false`: file response with temp-file cleanup
  - `true`: in-memory streaming response

---

## Request examples for all supported providers

### Qwen model

```bash
curl -X POST "http://localhost:8000/v1/audio/speech" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
    "provider": "qwen",
    "voice": "alloy",
    "input": "Ciao, questo e un test.",
    "response_format": "wav"
  }' --output speech_qwen.wav
```

### Voxtral model (local MLX via mlx-audio)

```bash
curl -X POST "http://localhost:8000/v1/audio/speech" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mistralai/Voxtral-Mini-3B-2507",
    "provider": "voxtral",
    "voice": "nova",
    "input": "Hello, this is a local Voxtral synthesis test.",
    "response_format": "wav"
  }' --output speech_voxtral.wav
```

---

## Professional voice onboarding workflow

This section describes a repeatable process to create production-quality voices and add them safely.

### 1) Record or select clean reference samples

Guidelines:
- mono WAV preferred
- low background noise, no music bed
- consistent mic distance and room
- stable speaking style matching target use-case
- clear language pronunciation for target `language`

Recommended target quality:
- `16 kHz` or `24 kHz` sample rate
- at least `8-20` seconds of clean speech

### 2) Prepare voice files in repository

Store files under `voices/` with predictable names:

```text
voices/
  alloy.wav
  nova.wav
  narrator_it_female.wav
  narrator_en_male.wav
```

### 3) Build `config/voice_map.local.json`

Each voice entry requires:
- `ref_audio_path`
- `ref_text`
- `language`

Example:

```json
{
  "alloy": {
    "ref_audio_path": "/absolute/path/to/voices/alloy.wav",
    "ref_text": "Ciao, questa e una voce di riferimento chiara e naturale.",
    "language": "Italian"
  },
  "narrator_en_male": {
    "ref_audio_path": "/absolute/path/to/voices/narrator_en_male.wav",
    "ref_text": "Hello, this is a clean English reference sample for narration.",
    "language": "English"
  }
}
```

### 4) Point env to the voice map

```bash
TTS_VOICE_MAP_FILE=./config/voice_map.local.json
```

### 5) Validate each voice before production

Checklist:
- reference path exists and is readable
- `ref_text` matches spoken content reasonably well
- language tag is correct
- short and long prompts both sound stable
- no clipping/artifacts in output WAV

### 6) Naming and governance best practices

- Use stable snake_case voice keys (API contract)
- Keep one source-of-truth JSON under `config/`
- Version-control non-sensitive voice metadata
- Prefer absolute paths in local development, mapped paths in deployment
- Add smoke tests for newly introduced voice keys

---

## Extending with a new provider (strategy pattern)

Provider architecture is strategy-based under `src/llm_tts_api/services/tts_providers/`.

To add a provider professionally:

1. Implement strategy with `provider_name` + `synthesize_chunks(...)`
2. Register it in `get_tts_provider_registry()` in `src/llm_tts_api/dependencies.py`
3. Add/update prefixes in `TTS_PROVIDER_MODEL_PREFIXES`
4. Add tests for dispatch and failure paths
5. Update README examples and `.env.example`

---

## Run tests

```bash
python -m pytest -q tests
```

---

## Useful files

- `examples.http` request collection
- `config/voice_map.example.json` starter template
- `src/llm_tts_api/config.py` runtime settings and validation
- `src/llm_tts_api/services/model_registry.py` model/provider resolution
- `src/llm_tts_api/services/tts_service.py` speech orchestration
- `src/llm_tts_api/services/tts_providers/` provider strategies
