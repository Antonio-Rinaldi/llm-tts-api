from pathlib import Path

from llm_tts_api.config import Settings
from llm_tts_api.services.model_registry import ModelRegistry


def test_provider_defaults_to_configured_mlx_audio(monkeypatch, tmp_path: Path) -> None:
    voice_map_file = tmp_path / "voice_map.json"
    voice_map_file.write_text(
        '{"alloy": {"ref_audio_path": "/tmp/alloy.wav", "ref_text": "hello", "language": "Italian"}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(voice_map_file))
    monkeypatch.setenv("TTS_MLX_AUDIO_MODEL_ALLOWED", "Qwen/Qwen3-TTS-12Hz-0.6B-Base")
    monkeypatch.setenv("TTS_VOXTRAL_MODEL_ALLOWED", "voxtral/mini-tts")

    registry = ModelRegistry(Settings())

    model, provider = registry.resolve_tts_target("voxtral/mini-tts", None)
    assert model == "voxtral/mini-tts"
    assert provider == "mlx_audio"


def test_provider_explicit_override(monkeypatch, tmp_path: Path) -> None:
    voice_map_file = tmp_path / "voice_map.json"
    voice_map_file.write_text(
        '{"alloy": {"ref_audio_path": "/tmp/alloy.wav", "ref_text": "hello", "language": "Italian"}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(voice_map_file))

    registry = ModelRegistry(Settings())

    model, provider = registry.resolve_tts_target("Qwen/Qwen3-TTS-12Hz-0.6B-Base", "mlx_audio")
    assert model == "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
    assert provider == "mlx_audio"


def test_provider_specific_default_model_when_model_missing(monkeypatch, tmp_path: Path) -> None:
    voice_map_file = tmp_path / "voice_map.json"
    voice_map_file.write_text(
        '{"alloy": {"ref_audio_path": "/tmp/alloy.wav", "ref_text": "hello", "language": "Italian"}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(voice_map_file))
    monkeypatch.setenv("TTS_PROVIDER", "voxtral")
    monkeypatch.setenv("TTS_VOXTRAL_MODEL_DEFAULT", "mlx-community/Voxtral-4B-TTS-2603-mlx-4bit")

    registry = ModelRegistry(Settings())

    model, provider = registry.resolve_tts_target(None, None)
    assert provider == "voxtral"
    assert model == "mlx-community/Voxtral-4B-TTS-2603-mlx-4bit"


def test_provider_specific_default_model_for_vllm_omni(monkeypatch, tmp_path: Path) -> None:
    voice_map_file = tmp_path / "voice_map.json"
    voice_map_file.write_text(
        '{"alloy": {"ref_audio_path": "/tmp/alloy.wav", "ref_text": "hello", "language": "Italian"}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(voice_map_file))
    monkeypatch.setenv("TTS_PROVIDER", "vllm-omni")
    monkeypatch.setenv("TTS_VLLM_OMNI_MODEL_DEFAULT", "vllm-omni/default-tts")

    registry = ModelRegistry(Settings())

    model, provider = registry.resolve_tts_target(None, None)
    assert provider == "vllm-omni"
    assert model == "vllm-omni/default-tts"


