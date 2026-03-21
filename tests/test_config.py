import json
from pathlib import Path

import pytest



def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    from qwen_tts_api.config import Settings

    settings = Settings()

    assert settings.app_name == "qwen-tts-api"
    assert settings.qwen_tts_model_default == "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
    assert settings.qwen_tts_model_allowed == ["Qwen/Qwen3-TTS-12Hz-0.6B-Base"]



def test_settings_allowed_models_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from qwen_tts_api.config import Settings

    monkeypatch.setenv("QWEN_TTS_MODEL_DEFAULT", "m1")
    monkeypatch.setenv("QWEN_TTS_MODEL_ALLOWED", "m1,m2,m3")

    settings = Settings()

    assert settings.qwen_tts_model_default == "m1"
    assert settings.qwen_tts_model_allowed == ["m1", "m2", "m3"]



def test_settings_voice_map_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    from qwen_tts_api.config import Settings

    payload = {
        "alloy": {
            "ref_audio_path": "/tmp/alloy.wav",
            "ref_text": "hello",
            "language": "Italian",
        }
    }
    monkeypatch.setenv("QWEN_TTS_VOICE_MAP_JSON", json.dumps(payload))

    settings = Settings()

    assert "alloy" in settings.qwen_tts_voice_map
    assert settings.qwen_tts_voice_map["alloy"].ref_audio_path == "/tmp/alloy.wav"



def test_settings_voice_map_loaded_from_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from qwen_tts_api.config import Settings

    voice_map_file = tmp_path / "voice_map.json"
    voice_map_file.write_text(
        json.dumps(
            {
                "alloy": {
                    "ref_audio_path": "/tmp/alloy.wav",
                    "ref_text": "hello",
                    "language": "Italian",
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("QWEN_TTS_VOICE_MAP_FILE", str(voice_map_file))

    settings = Settings()

    assert "alloy" in settings.qwen_tts_voice_map
    assert settings.qwen_tts_voice_map["alloy"].ref_audio_path == "/tmp/alloy.wav"



def test_settings_invalid_voice_map_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from qwen_tts_api.config import Settings

    monkeypatch.setenv("QWEN_TTS_VOICE_MAP_JSON", "not-json")

    with pytest.raises(ValueError):
        Settings()



def test_settings_max_input_chars_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from qwen_tts_api.config import Settings

    monkeypatch.setenv("QWEN_TTS_MAX_INPUT_CHARS", "8192")

    settings = Settings()

    assert settings.qwen_tts_max_input_chars == 8192
