# qwen-tts-api

OpenAI-compatible local audio API server built with FastAPI and Qwen TTS.

It provides:
- OpenAI-style `/v1` routes
- Environment-based model and voice configuration
- Modular src layout (`src/qwen_tts_api/...`)
- Structured OpenAI-style error envelopes
- `501 not_implemented` compatibility stubs for unsupported endpoints

## Current endpoint status

### Fully wired
- `GET /health`
- `GET /ready`
- `GET /v1/models`
- `POST /v1/audio/speech` (voice cloning via env voice map)

### Compatibility stubs (`501 not_implemented`)
- `POST /v1/audio/transcriptions`
- `POST /v1/audio/translations`
- `POST /v1/audio/voices`
- `GET/POST /v1/audio/voice_consents`
- `GET/POST/DELETE /v1/audio/voice_consents/{consent_id}`
- Chat completion routes under `/v1/chat/completions...`
- Realtime routes under `/v1/realtime...`

## Project structure

- `src/qwen_tts_api/` → application package
- `tests/` → test suite
- `plans/` → planning docs
- `examples.http` → endpoint examples for VS Code REST Client

## Install (dev)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
```

## Environment files

- `.env` → tracked template with placeholders
- `.env.local` → local real values (gitignored)

### Configure voice map (recommended: external JSON file)

Use `QWEN_TTS_VOICE_MAP_FILE` to point to a JSON file (better DX than inline JSON in `.env`):

```bash
QWEN_TTS_VOICE_MAP_FILE=./config/voice_map.local.json
```

File format (`voice -> {ref_audio_path, ref_text, language}`):

```json
{
  "alloy": {
    "ref_audio_path": "/absolute/path/alloy.wav",
    "ref_text": "Ciao, questa è una voce di riferimento.",
    "language": "Italian"
  },
  "nova": {
    "ref_audio_path": "/absolute/path/nova.wav",
    "ref_text": "Hello, this is a reference voice sample.",
    "language": "English"
  }
}
```

To make speech generation work, ensure:
1. The request `voice` value exists in the JSON file.
2. Every `ref_audio_path` points to a real file on disk.
3. The env file is loaded before starting the server.

Note: `QWEN_TTS_VOICE_MAP_JSON` is still supported as a fallback for backward compatibility.

### Configure max TTS input length

The server accepts long text and chunks it semantically (paragraph/sentence boundaries). Use:

```bash
QWEN_TTS_MAX_INPUT_CHARS=4096
```

Choose a value compatible with your model/runtime limits.

Load local env before running:

```bash
set -a
source .env.local
set +a
```

## Run the server

### With uvicorn

```bash
uvicorn qwen_tts_api.main:app --host 0.0.0.0 --port 8000 --reload --timeout-keep-alive 6000
```

### Without uvicorn command (debug-friendly)

```bash
python -m qwen_tts_api.main
```

This uses [`run()`](src/qwen_tts_api/main.py), which starts uvicorn programmatically and auto-loads [`.env`](.env) + [`.env.local`](.env.local) before app startup.

## Run tests

```bash
pytest
```

## HTTP examples

Use `examples.http` in VS Code REST Client.
