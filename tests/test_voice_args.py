from __future__ import annotations

from llm_tts_api.services.tts_providers.voice_args import (
    build_clone_voice_args,
    build_generation_args,
    build_named_voice_args,
    select_voice_args,
)


def test_build_named_voice_args_uses_first_supported_alias() -> None:
    out = build_named_voice_args("gold", {"text", "speaker", "voice"})

    assert out == {"voice": "gold"}


def test_build_named_voice_args_returns_empty_without_supported_param() -> None:
    out = build_named_voice_args("gold", {"text"})

    assert out == {}


def test_build_clone_voice_args_supports_reference_aliases() -> None:
    out = build_clone_voice_args(
        "/tmp/gold.wav", "hello", {"text", "reference_audio", "reference_text"}
    )

    assert out == {"reference_audio": "/tmp/gold.wav", "reference_text": "hello"}


def test_build_clone_voice_args_returns_empty_without_audio_path() -> None:
    out = build_clone_voice_args("", "hello", {"text", "ref_audio", "ref_text"})

    assert out == {}


def test_select_voice_args_prefers_clone_when_requested() -> None:
    selection = select_voice_args(
        voice_name="gold",
        ref_audio_path="/tmp/gold.wav",
        ref_text="hello",
        available_params={"text", "voice", "ref_audio", "ref_text"},
        prefer_clone=True,
        require_any=True,
    )

    assert selection.primary_args == {"ref_audio": "/tmp/gold.wav", "ref_text": "hello"}
    assert selection.clone_fallback_args == {"ref_audio": "/tmp/gold.wav", "ref_text": "hello"}
    assert selection.used_named_voice is False


def test_build_generation_args_maps_supported_fields() -> None:
    out = build_generation_args(
        language="Italian",
        temperature=0.8,
        top_p=0.95,
        available_params={"text", "lang_code", "temperature", "top_p"},
    )

    assert out == {"lang_code": "Italian", "temperature": 0.8, "top_p": 0.95}


