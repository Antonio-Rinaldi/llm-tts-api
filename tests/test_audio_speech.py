import json
from pathlib import Path

from fastapi.testclient import TestClient

from llm_tts_api.errors import invalid_request


def _build_client_with_voice(monkeypatch, tmp_path: Path, extra_env: dict[str, str] | None = None) -> TestClient:
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

    if extra_env:
        for key, value in extra_env.items():
            monkeypatch.setenv(key, value)

    from llm_tts_api.services.tts_providers.mlx_audio_provider import MLXAudioTTSProvider

    monkeypatch.setattr(MLXAudioTTSProvider, "preload", lambda self, model_name: None)

    from llm_tts_api import dependencies
    from llm_tts_api.main import create_app

    dependencies.get_settings.cache_clear()
    dependencies.get_model_registry.cache_clear()
    dependencies.get_tts_provider_registry.cache_clear()
    dependencies.get_tts_service.cache_clear()

    return TestClient(create_app())


def test_speech_rejects_empty_input(monkeypatch, tmp_path: Path) -> None:
    client = _build_client_with_voice(monkeypatch, tmp_path)

    response = client.post(
        "/v1/audio/speech",
        json={"model": "Qwen/Qwen3-TTS-12Hz-0.6B-Base", "voice": "alloy", "input": "   "},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["type"] == "invalid_request_error"
    assert payload["error"]["param"] == "input"


def test_speech_rejects_unmapped_voice(monkeypatch, tmp_path: Path) -> None:
    client = _build_client_with_voice(monkeypatch, tmp_path)

    response = client.post(
        "/v1/audio/speech",
        json={"model": "Qwen/Qwen3-TTS-12Hz-0.6B-Base", "voice": "nova", "input": "hello"},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["type"] == "invalid_request_error"
    assert payload["error"]["param"] == "voice"


def test_speech_rejects_disallowed_model(monkeypatch, tmp_path: Path) -> None:
    client = _build_client_with_voice(monkeypatch, tmp_path)

    response = client.post(
        "/v1/audio/speech",
        json={"model": "other-model", "voice": "alloy", "input": "hello"},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["type"] == "invalid_request_error"
    assert payload["error"]["param"] == "model"


def test_speech_mlx_audio_requires_dependency(monkeypatch, tmp_path: Path) -> None:
    client = _build_client_with_voice(monkeypatch, tmp_path, {"TTS_MODEL_ALLOWED": "voxtral/mini-tts"})

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
        json={"model": "voxtral/mini-tts", "provider": "mlx_audio", "voice": "alloy", "input": "hello"},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["param"] == "provider"

