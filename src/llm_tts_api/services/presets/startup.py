"""Startup-time preset registry initialisation (S-027 T4).

This module owns the four-step sequence the lifespan runs before serving
traffic:

1. Parse ``presets.json`` via :class:`PresetConfig` (FR-PR-02).
2. Verify file-permission posture — owner uid + not world-writable (NFR-SE-09).
3. Validate ``TTS_DEFAULT_PRESET`` resolves to a loaded preset name (FR-PR-05).
4. Validate every preset-pinned ``(provider, model)`` against the provider
   allow-lists held on :class:`Settings` (FR-PR-13).

Each failure raises a typed exception subclass of ``ValueError`` so the
caller (lifespan in production, pytest in UAT-PR-11..14) can introspect
the failure mode without parsing log strings. The lifespan wraps the
raised exception in a ``SystemExit`` whose message carries the matching
``config_error.*`` code from :mod:`llm_tts_api.errors`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING

from llm_tts_api.errors import (
    CONFIG_ERROR_PRESET_PROVIDER_INVALID,
    CONFIG_ERROR_PRESETS_INVALID,
    CONFIG_ERROR_PRESETS_UNSAFE_PERMISSIONS,
)
from llm_tts_api.services.presets.config import (
    PresetRegistry,
    PresetsInvalidError,
    check_presets_file_permissions,
    load_preset_registry,
    validate_preset_providers,
)

if TYPE_CHECKING:
    from llm_tts_api.config import Settings
    from llm_tts_api.services.tts_providers.registry import TTSProviderRegistry

logger = logging.getLogger(__name__)


def _allow_lists_from_settings(
    settings: Settings,
    provider_registry: TTSProviderRegistry | None,
) -> Mapping[str, frozenset[str]]:
    """Project the per-provider allow-list slots into a uniform mapping.

    Restricts the mapping to providers actually registered in
    ``provider_registry`` when supplied — a preset that pins a provider
    that was filtered out during auto-selection is just as much a
    misconfiguration as one that pins a nonexistent provider.
    """
    raw: dict[str, frozenset[str]] = {
        "mlx_audio": frozenset(settings.tts_mlx_audio_model_allowed),
        "voxtral": frozenset(settings.tts_voxtral_model_allowed),
        "vllm-omni": frozenset(settings.tts_vllm_omni_model_allowed),
    }
    if provider_registry is None:
        return raw
    registered = set(provider_registry.names())
    return {name: models for name, models in raw.items() if name in registered}


def initialize_preset_registry(
    settings: Settings,
    provider_registry: TTSProviderRegistry | None = None,
) -> PresetRegistry:
    """Run the full T4 validation sequence and return the loaded registry.

    Args:
        settings: Process-wide :class:`Settings`; supplies the presets
            file path, the default-preset name, and the per-provider
            model allow-lists.
        provider_registry: The registered provider strategies (from
            ``app.state.provider_registry``). When ``None`` (rare —
            tests that don't care about FR-PR-13 may pass ``None``)
            the provider cross-check ranges over the raw Settings
            allow-lists.

    Returns:
        The validated, frozen :class:`PresetRegistry` snapshot ready
        to hang off ``app.state.preset_registry``.

    Raises:
        PresetsInvalidError: parse/schema failure or unknown default preset.
        PresetsUnsafePermissionsError: owner-mismatch or world-writable file.
        PresetProviderInvalidError: a preset-pinned (provider, model) is
            not in the matching allow-list.
    """
    path = settings.tts_presets_file
    # NOTE: file-permission posture is verified BEFORE parsing the file
    # so a tampered-permissions file never even reaches the JSON parser
    # (an attacker who can flip the bits could otherwise race a parser
    # bug to crash the service in unintended ways).
    check_presets_file_permissions(path)
    registry = load_preset_registry(path)

    default_name = settings.tts_default_preset
    if default_name not in registry.names():
        raise PresetsInvalidError(
            f"TTS_DEFAULT_PRESET={default_name!r} does not match any preset in {path}; "
            f"defined presets: {sorted(registry.names())}"
        )

    allow_lists = _allow_lists_from_settings(settings, provider_registry)
    validate_preset_providers(registry, allow_lists)

    logger.info(
        "preset_registry_loaded path=%s default=%s presets=%s",
        path,
        default_name,
        sorted(registry.names()),
    )
    return registry


__all__ = [
    "CONFIG_ERROR_PRESETS_INVALID",
    "CONFIG_ERROR_PRESETS_UNSAFE_PERMISSIONS",
    "CONFIG_ERROR_PRESET_PROVIDER_INVALID",
    "initialize_preset_registry",
]
