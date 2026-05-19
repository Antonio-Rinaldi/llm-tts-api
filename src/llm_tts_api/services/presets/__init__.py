"""Audio-generation presets (cycle 2 / S-027 onwards).

Public surface:

* :class:`PresetPostprocess` / :class:`PresetDefaults` / :class:`PresetEntry`
  – Pydantic models that describe one preset's shape in ``config/presets.json``.
* :class:`PresetConfig` – Pydantic root model that parses the whole file
  (``{"fast": {...}, "balanced": {...}, ...}`` — flat dict-of-name->entry,
  matching the llm-image-api reference shape per cycle-2 D10).
* :class:`PresetRegistry` – frozen, immutable snapshot held on
  ``app.state.preset_registry``. Replaced atomically on hot-reload by
  S-029; never mutated in place. The snapshot is the parameter S-028's
  resolver consumes (locked contract — see ``docs/planning/sprints/.pending/S-027-impl.md``).
* :func:`load_preset_registry` – parses + validates a path and returns
  a registry. Pydantic errors are re-raised with their field paths prefixed
  by ``presets.`` so operators see ``presets.quality.defaults.temperature``.
* :func:`check_presets_file_permissions` – owner-uid match + not-world-writable
  posture check (NFR-SE-09). Startup-only; not re-run on reload (RISK-PR-3).
* :func:`validate_preset_providers` – verifies every preset-pinned
  ``(provider, model)`` is in the corresponding provider's allow-list
  (FR-PR-13).
"""

from __future__ import annotations

from llm_tts_api.services.presets.config import (
    PresetConfig,
    PresetDefaults,
    PresetEntry,
    PresetPostprocess,
    PresetProviderInvalidError,
    PresetRegistry,
    PresetsInvalidError,
    PresetsUnsafePermissionsError,
    check_presets_file_permissions,
    load_preset_registry,
    validate_preset_providers,
)
from llm_tts_api.services.presets.startup import (
    initialize_preset_registry,
)

__all__ = [
    "PresetConfig",
    "PresetDefaults",
    "PresetEntry",
    "PresetPostprocess",
    "PresetRegistry",
    "PresetsInvalidError",
    "PresetsUnsafePermissionsError",
    "PresetProviderInvalidError",
    "check_presets_file_permissions",
    "initialize_preset_registry",
    "load_preset_registry",
    "validate_preset_providers",
]
