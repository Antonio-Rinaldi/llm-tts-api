from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

VOICE_NAME_ALIASES: Final[tuple[str, ...]] = ("voice", "voice_id", "speaker")
REF_AUDIO_ALIASES: Final[tuple[str, ...]] = ("ref_audio", "reference_audio")
REF_TEXT_ALIASES: Final[tuple[str, ...]] = ("ref_text", "reference_text")
LANGUAGE_ALIASES: Final[tuple[str, ...]] = ("lang_code", "language", "lang")
TEMPERATURE_ALIASES: Final[tuple[str, ...]] = ("temperature",)
TOP_P_ALIASES: Final[tuple[str, ...]] = ("top_p", "topP")


@dataclass(frozen=True)
class VoiceArgsSelection:
    """Precomputed voice argument selection for one provider call path."""

    primary_args: dict[str, str]
    clone_fallback_args: dict[str, str]
    used_named_voice: bool


def _first_supported_name(candidates: tuple[str, ...], available_params: set[str]) -> str | None:
    """Return the first supported parameter name from a candidate alias list."""
    for candidate in candidates:
        if candidate in available_params:
            return candidate
    return None


def build_named_voice_args(voice_name: str, available_params: set[str]) -> dict[str, str]:
    """Build args for selecting a provider-native named voice."""
    if not voice_name:
        return {}

    voice_param_name = _first_supported_name(VOICE_NAME_ALIASES, available_params)
    if not voice_param_name:
        return {}

    return {voice_param_name: voice_name}


def build_clone_voice_args(
    ref_audio_path: str, ref_text: str, available_params: set[str]
) -> dict[str, str]:
    """Build args for reference-audio voice cloning when supported."""
    if not ref_audio_path:
        return {}

    ref_audio_param_name = _first_supported_name(REF_AUDIO_ALIASES, available_params)
    ref_text_param_name = _first_supported_name(REF_TEXT_ALIASES, available_params)

    return {
        **({ref_audio_param_name: ref_audio_path} if ref_audio_param_name else {}),
        **({ref_text_param_name: ref_text} if ref_text_param_name else {}),
    }


def select_voice_args(
    *,
    voice_name: str,
    ref_audio_path: str,
    ref_text: str,
    available_params: set[str],
    prefer_clone: bool,
    require_any: bool,
) -> VoiceArgsSelection:
    """Select primary and fallback voice args with explicit priority rules."""
    clone_args = build_clone_voice_args(ref_audio_path, ref_text, available_params)
    named_args = build_named_voice_args(voice_name, available_params)
    ordered = (clone_args, named_args) if prefer_clone else (named_args, clone_args)
    selected = next((args for args in ordered if args), {})
    if require_any and not selected:
        return VoiceArgsSelection(
            primary_args={},
            clone_fallback_args=clone_args,
            used_named_voice=False,
        )
    return VoiceArgsSelection(
        primary_args=selected,
        clone_fallback_args=clone_args,
        used_named_voice=bool(selected and selected == named_args),
    )


def build_generation_args(
    *,
    language: str,
    temperature: float,
    top_p: float,
    available_params: set[str],
) -> dict[str, Any]:
    """Map generation options to provider-specific parameter names."""
    language_param = _first_supported_name(LANGUAGE_ALIASES, available_params)
    temperature_param = _first_supported_name(TEMPERATURE_ALIASES, available_params)
    top_p_param = _first_supported_name(TOP_P_ALIASES, available_params)
    return {
        **({language_param: language} if language_param else {}),
        **({temperature_param: temperature} if temperature_param else {}),
        **({top_p_param: top_p} if top_p_param else {}),
    }


