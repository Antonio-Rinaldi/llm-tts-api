from __future__ import annotations

from llm_tts_api.services.tts_providers.voice_args import build_clone_voice_args, build_named_voice_args


def test_build_named_voice_args_uses_first_supported_alias() -> None:
    out = build_named_voice_args("gold", {"text", "speaker", "voice"})

    assert out == {"voice": "gold"}


def test_build_named_voice_args_returns_empty_without_supported_param() -> None:
    out = build_named_voice_args("gold", {"text"})

    assert out == {}


def test_build_clone_voice_args_supports_reference_aliases() -> None:
    out = build_clone_voice_args("/tmp/gold.wav", "hello", {"text", "reference_audio", "reference_text"})

    assert out == {"reference_audio": "/tmp/gold.wav", "reference_text": "hello"}


def test_build_clone_voice_args_returns_empty_without_audio_path() -> None:
    out = build_clone_voice_args("", "hello", {"text", "ref_audio", "ref_text"})

    assert out == {}

