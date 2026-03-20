import json
from pathlib import Path

from fastapi.testclient import TestClient



def _build_client_with_voice(monkeypatch, tmp_path: Path) -> TestClient:
    voice_ref = tmp_path / "alloy.wav"
    voice_ref.write_bytes(b"fake-wav")

    monkeypatch.setenv(
        "QWEN_TTS_VOICE_MAP_JSON",
        json.dumps(
            {
                "alloy": {
                    "ref_audio_path": str(voice_ref),
                    "ref_text": "sample",
                    "language": "Italian",
                }
            }
        ),
    )

    from qwen_tts_api.main import create_app

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
