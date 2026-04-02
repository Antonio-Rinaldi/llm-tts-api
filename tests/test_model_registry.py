from pathlib import Path

from llm_tts_api.config import Settings
from llm_tts_api.services.model_registry import ModelRegistry


def test_provider_inference_voxtral(monkeypatch, tmp_path: Path) -> None:
    voice_map_file = tmp_path / "voice_map.json"
    voice_map_file.write_text(
        '{"alloy": {"ref_audio_path": "/tmp/alloy.wav", "ref_text": "hello", "language": "Italian"}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(voice_map_file))
    monkeypatch.setenv("TTS_MODEL_ALLOWED", "Qwen/Qwen3-TTS-12Hz-0.6B-Base,voxtral/mini-tts")

    registry = ModelRegistry(Settings())

    model, provider = registry.resolve_tts_target("voxtral/mini-tts", None)
    assert model == "voxtral/mini-tts"
    assert provider == "voxtral"


def test_provider_explicit_override(monkeypatch, tmp_path: Path) -> None:
    voice_map_file = tmp_path / "voice_map.json"
    voice_map_file.write_text(
        '{"alloy": {"ref_audio_path": "/tmp/alloy.wav", "ref_text": "hello", "language": "Italian"}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(voice_map_file))

    registry = ModelRegistry(Settings())

    model, provider = registry.resolve_tts_target("Qwen/Qwen3-TTS-12Hz-0.6B-Base", "voxtral")
    assert model == "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
    assert provider == "voxtral"


