from __future__ import annotations

from typing import Final

VOICE_NAME_ALIASES: Final[tuple[str, ...]] = ("voice", "voice_id", "speaker")
REF_AUDIO_ALIASES: Final[tuple[str, ...]] = ("ref_audio", "reference_audio")
REF_TEXT_ALIASES: Final[tuple[str, ...]] = ("ref_text", "reference_text")


def _first_supported_name(candidates: tuple[str, ...], available_params: set[str]) -> str | None:
    for candidate in candidates:
        if candidate in available_params:
            return candidate
    return None


def build_named_voice_args(voice_name: str, available_params: set[str]) -> dict[str, str]:
    if not voice_name:
        return {}

    voice_param_name = _first_supported_name(VOICE_NAME_ALIASES, available_params)
    if not voice_param_name:
        return {}

    return {voice_param_name: voice_name}


def build_clone_voice_args(ref_audio_path: str, ref_text: str, available_params: set[str]) -> dict[str, str]:
    if not ref_audio_path:
        return {}

    ref_audio_param_name = _first_supported_name(REF_AUDIO_ALIASES, available_params)
    ref_text_param_name = _first_supported_name(REF_TEXT_ALIASES, available_params)

    return {
        **({ref_audio_param_name: ref_audio_path} if ref_audio_param_name else {}),
        **({ref_text_param_name: ref_text} if ref_text_param_name else {}),
    }


