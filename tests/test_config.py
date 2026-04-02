import json
from pathlib import Path

import pytest


def _write_voice_map(tmp_path: Path) -> Path:
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
    return voice_map_file


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from llm_tts_api.config import Settings

    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))

    settings = Settings()

    assert settings.app_name == "llm-tts-api"
    assert settings.tts_model_default == "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
    assert settings.tts_model_allowed == ["Qwen/Qwen3-TTS-12Hz-0.6B-Base"]
    assert settings.tts_provider == "mlx_audio"


def test_settings_allowed_models_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from llm_tts_api.config import Settings

    monkeypatch.setenv("TTS_MODEL_DEFAULT", "m1")
    monkeypatch.setenv("TTS_MODEL_ALLOWED", "m1,m2,m3")
    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))

    settings = Settings()

    assert settings.tts_model_default == "m1"
    assert settings.tts_model_allowed == ["m1", "m2", "m3"]


def test_settings_voice_map_parsed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from llm_tts_api.config import Settings

    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))

    settings = Settings()

    assert "alloy" in settings.tts_voice_map
    assert settings.tts_voice_map["alloy"].ref_audio_path == "/tmp/alloy.wav"


def test_settings_voice_map_loaded_from_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from llm_tts_api.config import Settings

    voice_map_file = _write_voice_map(tmp_path)
    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(voice_map_file))

    settings = Settings()

    assert "alloy" in settings.tts_voice_map
    assert settings.tts_voice_map["alloy"].ref_audio_path == "/tmp/alloy.wav"


def test_settings_invalid_voice_map_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from llm_tts_api.config import Settings

    voice_map_file = tmp_path / "voice_map.json"
    voice_map_file.write_text("not-json", encoding="utf-8")
    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(voice_map_file))

    with pytest.raises(ValueError):
        Settings()


def test_settings_max_input_chars_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from llm_tts_api.config import Settings

    monkeypatch.setenv("TTS_MAX_INPUT_CHARS", "8192")
    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))

    settings = Settings()

    assert settings.tts_max_input_chars == 8192


def test_settings_provider_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from llm_tts_api.config import Settings

    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
    monkeypatch.setenv("TTS_PROVIDER", "mlx_audio")

    settings = Settings()

    assert settings.tts_provider == "mlx_audio"
