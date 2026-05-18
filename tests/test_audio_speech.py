import json
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from llm_tts_api.errors import invalid_request


def _build_client_with_voice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, extra_env: dict[str, str] | None = None
) -> TestClient:
    voice_ref = tmp_path / "alloy.wav"
    voice_ref.write_bytes(b"fake-wav")

    voice_map = tmp_path / "voice_map.json"
    voice_map.write_text(
        json.dumps(
            {
                "alloy": {
                    "ref_audio_path": str(voice_ref),
                    "ref_text": "sample",
                    "language": "Italian",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(voice_map))
    # Force the device profile to MPS so S-006 auto-selection picks
    # ``mlx_audio`` (the provider whose preload these tests mock out).
    # Without this override CI runners on Linux/CPU would fail startup
    # with ``provider_error.no_viable_provider`` because no registered
    # provider declares CPU support.
    monkeypatch.setenv("TTS_DEVICE", "mps")

    if extra_env:
        for key, value in extra_env.items():
            monkeypatch.setenv(key, value)

    from llm_tts_api.services.tts_providers.mlx_audio_provider import MLXAudioTTSProvider

    monkeypatch.setattr(MLXAudioTTSProvider, "preload", lambda self, model_name: None)

    from llm_tts_api.main import create_app

    # Post-S-003: singletons are constructed by lifespan from the env we
    # just set; each create_app() call yields a fresh app whose lifespan
    # builds a fresh dependency graph. Enter the TestClient context
    # manager so lifespan startup actually fires (otherwise app.state stays
    # empty and Depends(get_model_registry) trips an AttributeError).
    client = TestClient(create_app())
    client.__enter__()  # noqa: PLC2801 — leak is bounded by test teardown
    return client


def test_speech_rejects_empty_input(monkeypatch, tmp_path: Path) -> None:
    client = _build_client_with_voice(monkeypatch, tmp_path)

    response = client.post(
        "/v1/audio/speech",
        json={"model": "Qwen/Qwen3-TTS-12Hz-0.6B-Base", "voice": "alloy", "input": "   "},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["type"] == "validation_error"
    assert payload["error"]["param"] == "input"


def test_speech_rejects_unmapped_voice(monkeypatch, tmp_path: Path) -> None:
    client = _build_client_with_voice(monkeypatch, tmp_path)

    response = client.post(
        "/v1/audio/speech",
        json={"model": "Qwen/Qwen3-TTS-12Hz-0.6B-Base", "voice": "nova", "input": "hello"},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["type"] == "validation_error"
    assert payload["error"]["param"] == "voice"


def test_speech_rejects_disallowed_model(monkeypatch, tmp_path: Path) -> None:
    client = _build_client_with_voice(monkeypatch, tmp_path)

    response = client.post(
        "/v1/audio/speech",
        json={"model": "other-model", "voice": "alloy", "input": "hello"},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["type"] == "validation_error"
    assert payload["error"]["param"] == "model"


def test_speech_mlx_audio_requires_dependency(monkeypatch, tmp_path: Path) -> None:
    client = _build_client_with_voice(
        monkeypatch,
        tmp_path,
        {
            "TTS_PROVIDER": "mlx_audio",
            "TTS_MLX_AUDIO_MODEL_ALLOWED": "voxtral/mini-tts",
        },
    )

    from llm_tts_api.services.tts_providers.mlx_audio_provider import MLXAudioTTSProvider

    def _fake_get_model(self, model_name: str):
        _ = self
        _ = model_name
        raise invalid_request(
            "Provider 'mlx_audio' requires the optional dependency 'mlx-audio'",
            param="provider",
        )

    monkeypatch.setattr(MLXAudioTTSProvider, "_get_model", _fake_get_model)

    response = client.post(
        "/v1/audio/speech",
        json={
            "model": "voxtral/mini-tts",
            "provider": "mlx_audio",
            "voice": "alloy",
            "input": "hello",
        },
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["param"] == "provider"


def test_speech_forwards_clone_voice_config_to_mlx_provider(monkeypatch, tmp_path: Path) -> None:
    client = _build_client_with_voice(monkeypatch, tmp_path)

    from llm_tts_api.services.tts_providers.mlx_audio_provider import MLXAudioTTSProvider

    captured = {}

    def _wav_bytes() -> bytes:
        import io

        buf = io.BytesIO()
        with wave.open(buf, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(16000)
            writer.writeframes(b"\x00\x00" * 10)
        return buf.getvalue()

    def _fake_synthesize(self, request):
        _ = self
        captured["voice_name"] = request.voice_name
        captured["ref_audio_path"] = request.voice.ref_audio_path
        captured["ref_text"] = request.voice.ref_text
        return [_wav_bytes()]

    monkeypatch.setattr(MLXAudioTTSProvider, "synthesize_chunks", _fake_synthesize)

    response = client.post(
        "/v1/audio/speech",
        json={
            "model": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
            "provider": "mlx_audio",
            "voice": "alloy",
            "input": "hello",
        },
    )

    assert response.status_code == 200
    assert captured["voice_name"] == "alloy"
    assert captured["ref_audio_path"].endswith("alloy.wav")
    assert captured["ref_text"] == "sample"
