"""S-017 — OpenAI adapter contract tests (UAT-OA-01..04).

Pins the four acceptance criteria of S-017:

* **UAT-OA-01**: an OpenAI-shaped POST to ``/v1/audio/speech`` returns 200
  with audio bytes — same pipeline as the rich endpoint.
* **UAT-OA-02**: SDK streaming (``with_streaming_response``) drains chunked
  bytes end-to-end.
* **UAT-OA-03**: ``routers/audio.py`` is a thin translator — no import of
  :class:`SpeechSynthesizer`, no calls into ``routers/synthesize``, and the
  ``create_speech`` body stays under 30 logical LOC of translation.
* **UAT-OA-04**: ``GET /v1/models`` lists the same model ids the rich
  endpoint will accept (provider allow-lists union).

The audio adapter also strips every rich-endpoint-only header on this
response shape per the user-decided constraint (no ``X-Voice-Source`` /
``X-Voice-Id`` / ``X-Chunks`` / ``X-Total-Duration-Ms`` / ``X-Device`` /
``X-Dtype`` / ``X-Provider`` / ``X-Model`` leak). Only ``X-Request-ID``
remains, which OpenAI's own contract permits.
"""

from __future__ import annotations

import ast
import asyncio
import io
import wave
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from llm_tts_api.services.voice_store import VoiceRecord

_RICH_ONLY_HEADERS: frozenset[str] = frozenset(
    {
        "x-provider",
        "x-model",
        "x-device",
        "x-dtype",
        "x-voice-source",
        "x-voice-id",
        "x-chunks",
        "x-total-duration-ms",
    }
)


def _run(coro: Any) -> Any:
    return asyncio.new_event_loop().run_until_complete(coro)


async def _seed_voice(client: TestClient, *, voice_id: str = "alloy") -> VoiceRecord:
    """Populate the in-memory fakes with a usable voice record + blob."""
    state = client.app.state
    buf = io.BytesIO()
    with wave.open(buf, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\x00\x00" * 16)
    record = VoiceRecord(
        id=voice_id,
        transcript="ref text",
        language="Italian",
        consent_acknowledged=True,
        source="crud",  # type: ignore[arg-type]
    )
    await state.voice_metadata_repo.create(record)
    await state.voice_blob_repo.put(voice_id, buf.getvalue())
    return record


# ---------------------------------------------------------------------------
# UAT-OA-01 — OpenAI-shaped request still works
# ---------------------------------------------------------------------------


def test_openai_speech_happy_path_returns_audio(client: TestClient) -> None:
    """UAT-OA-01: minimal OpenAI request → 200 + audio body."""
    _run(_seed_voice(client))
    response = client.post(
        "/v1/audio/speech",
        json={
            "model": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
            "input": "Ciao mondo.",
            "voice": "alloy",
            "response_format": "wav",
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "audio/wav"
    # Body parses as a WAV (the rich pipeline concatenates per-chunk WAVs).
    with wave.open(io.BytesIO(response.content), "rb") as reader:
        assert reader.getnchannels() == 1


def test_openai_speech_strips_rich_endpoint_headers(client: TestClient) -> None:
    """User constraint: response shape is OpenAI-identical — no rich headers leak."""
    _run(_seed_voice(client))
    response = client.post(
        "/v1/audio/speech",
        json={"model": "Qwen/Qwen3-TTS-12Hz-0.6B-Base", "input": "Hi", "voice": "alloy"},
    )
    assert response.status_code == 200
    present = {k.lower() for k in response.headers}
    leaked = present & _RICH_ONLY_HEADERS
    assert leaked == set(), f"rich-only headers leaked to OpenAI path: {leaked}"
    # ``x-request-id`` is allowed (OpenAI's own contract permits a request id).
    assert "x-request-id" in present


def test_openai_speech_rejects_non_wav_response_format(client: TestClient) -> None:
    """response_format other than 'wav' → 400 with param=response_format."""
    _run(_seed_voice(client))
    response = client.post(
        "/v1/audio/speech",
        json={
            "model": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
            "input": "Hi",
            "voice": "alloy",
            "response_format": "mp3",
        },
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["type"] == "validation_error"
    assert payload["error"]["param"] == "response_format"


def test_openai_speech_extra_openai_fields_ignored(client: TestClient) -> None:
    """OpenAI-only fields (instructions, speed, stream_format) are accepted+ignored."""
    _run(_seed_voice(client))
    response = client.post(
        "/v1/audio/speech",
        json={
            "model": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
            "input": "Hi",
            "voice": "alloy",
            "instructions": "speak slowly",
            "speed": 1.2,
            "stream_format": "raw",
        },
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# UAT-OA-02 — Streaming via SDK's with_streaming_response.create(...)
# ---------------------------------------------------------------------------


def test_openai_speech_streaming_drains_chunked_bytes(client: TestClient) -> None:
    """UAT-OA-02: streaming returns chunked WAV bytes and strips rich headers.

    The OpenAI SDK's ``with_streaming_response.create(...)`` reads the
    response body iteratively; what matters is that the server returns
    audio bytes and the client can consume them in chunks.
    """
    _run(_seed_voice(client))
    with client.stream(
        "POST",
        "/v1/audio/speech?stream=true",
        json={
            "model": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
            "input": "Uno. Due. Tre.",
            "voice": "alloy",
        },
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/wav"
        present = {k.lower() for k in response.headers}
        leaked = present & _RICH_ONLY_HEADERS
        assert leaked == set(), f"rich-only headers leaked to OpenAI streaming path: {leaked}"
        body = b"".join(response.iter_bytes())
    assert len(body) > 0
    # The first chunk alone parses cleanly as WAV (rich pipeline emits one
    # WAV per chunk; OpenAI clients concatenate them as opaque audio bytes).
    with wave.open(io.BytesIO(body[:8000]), "rb") as reader:
        assert reader.getnchannels() == 1


# ---------------------------------------------------------------------------
# UAT-OA-03 — Static check: handler is a thin translator
# ---------------------------------------------------------------------------


def _audio_router_path() -> Path:
    import llm_tts_api.routers.audio as audio_router

    return Path(audio_router.__file__)


def test_audio_router_has_no_speech_synthesizer_imports() -> None:
    """UAT-OA-03: ``routers/audio.py`` MUST NOT import :class:`SpeechSynthesizer`
    or pull in ``routers.synthesize`` — only the shared service-layer entry.

    AST-based check so docstring/comment mentions of the forbidden names do
    not produce false positives.
    """
    source = _audio_router_path().read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert module != "llm_tts_api.routers.synthesize", (
                "routers/audio.py must not import from routers.synthesize — "
                "use the shared service-layer entry point"
            )
            for alias in node.names:
                assert alias.name != "SpeechSynthesizer", (
                    f"routers/audio.py imports SpeechSynthesizer from {module!r} — "
                    "the adapter must delegate via synthesize_core"
                )
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "llm_tts_api.routers.synthesize", (
                    "routers/audio.py must not import routers.synthesize"
                )
    # Source-level safety net: there's no call-site for SpeechSynthesizer
    # (the docstring may mention it, but no executable code should).
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "SpeechSynthesizer":
            raise AssertionError(
                "routers/audio.py references SpeechSynthesizer at runtime — "
                "the adapter must call synthesize_core only"
            )
        if isinstance(node, ast.Attribute) and node.attr == "SpeechSynthesizer":
            raise AssertionError(
                "routers/audio.py references SpeechSynthesizer at runtime — "
                "the adapter must call synthesize_core only"
            )


def test_audio_router_imports_synthesize_core_only() -> None:
    """UAT-OA-03 (positive): the only synthesis call site is ``synthesize_core``."""
    source = _audio_router_path().read_text(encoding="utf-8")
    tree = ast.parse(source)
    synthesize_imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and "synthesize" in node.module:
            synthesize_imports.append(node.module)
            for alias in node.names:
                assert alias.name == "synthesize_core", (
                    f"unexpected import from {node.module}: {alias.name!r} — "
                    "only synthesize_core is the sanctioned service-layer entry"
                )
    assert any("services.synthesize_service" in m for m in synthesize_imports), (
        "routers/audio.py must import synthesize_core from services.synthesize_service"
    )


def test_create_speech_handler_under_30_loc() -> None:
    """UAT-OA-03: the ``create_speech`` body is ≤ 30 logical lines of translation."""
    source = _audio_router_path().read_text(encoding="utf-8")
    tree = ast.parse(source)
    handler: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.name == "create_speech"
        ):
            handler = node
            break
    assert handler is not None, "expected create_speech to be defined in routers/audio.py"
    body_lines = handler.end_lineno - handler.body[0].lineno + 1  # type: ignore[operator]
    assert body_lines <= 30, (
        f"create_speech body is {body_lines} LOC — UAT-OA-03 requires ≤ 30 lines of translation"
    )


# ---------------------------------------------------------------------------
# UAT-OA-04 — /v1/models matches the rich-endpoint catalog
# ---------------------------------------------------------------------------


def test_models_endpoint_matches_provider_allowlists(client: TestClient) -> None:
    """UAT-OA-04: ``GET /v1/models`` enumerates every model the rich endpoint
    will accept (the union of per-provider allow-lists in :class:`Settings`)."""
    state = client.app.state
    state.settings.tts_mlx_audio_model_allowed = ["mlx-model-a", "mlx-model-b"]
    state.settings.tts_voxtral_model_allowed = ["voxtral-model"]
    state.settings.tts_vllm_omni_model_allowed = ["vllm-model"]

    response = client.get("/v1/models")
    assert response.status_code == 200
    listed = {obj["id"] for obj in response.json()["data"]}

    expected_tts = (
        set(state.settings.tts_mlx_audio_model_allowed)
        | set(state.settings.tts_voxtral_model_allowed)
        | set(state.settings.tts_vllm_omni_model_allowed)
    )
    # Rich-endpoint accepts each model only under its declared provider;
    # /v1/models is the per-id union — the rich set MUST be a subset.
    missing = expected_tts - listed
    assert missing == set(), f"/v1/models is missing rich-endpoint allow-list models: {missing}"


@pytest.mark.parametrize(
    "provider_attr,model_id",
    [
        ("tts_mlx_audio_model_allowed", "Qwen/Qwen3-TTS-12Hz-0.6B-Base"),
        ("tts_voxtral_model_allowed", "mlx-community/Voxtral-4B-TTS-2603-mlx-4bit"),
        ("tts_vllm_omni_model_allowed", "vllm-omni/default-tts"),
    ],
)
def test_models_endpoint_reflects_each_provider(
    client: TestClient, provider_attr: str, model_id: str
) -> None:
    """UAT-OA-04: rotating a single provider's allow-list propagates to /v1/models."""
    state = client.app.state
    state.settings.tts_mlx_audio_model_allowed = []
    state.settings.tts_voxtral_model_allowed = []
    state.settings.tts_vllm_omni_model_allowed = []
    setattr(state.settings, provider_attr, [model_id])

    response = client.get("/v1/models")
    assert response.status_code == 200
    listed = {obj["id"] for obj in response.json()["data"]}
    assert model_id in listed
