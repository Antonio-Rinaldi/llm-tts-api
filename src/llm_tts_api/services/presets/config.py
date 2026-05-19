"""Pydantic schema + registry + file-permission posture for audio presets.

S-027 — cycle 2 foundation. Feeds:

* S-028 (request-time resolver consuming :class:`PresetRegistry` snapshots).
* S-029 (hot-reload + validating-before-swap).

The file shape mirrors the llm-image-api reference (cycle-2 decision D10) —
flat ``{"fast": {...}, "balanced": {...}, ...}`` JSON. Pydantic validation
errors are surfaced with the path prefix ``presets.`` so messages match the
FR-PR-02 example (``presets.quality.defaults.temperature``).
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, ValidationError

VALID_RESPONSE_FORMATS: Final[frozenset[str]] = frozenset({"wav", "wav24", "flac"})


class PresetPostprocess(BaseModel):
    """Optional postprocess block (consumed by S-031 / FR-PP-01..05)."""

    model_config = ConfigDict(extra="forbid")

    rms_normalize: bool = False
    silence_trim: bool = False
    denoise: bool = False


class PresetDefaults(BaseModel):
    """Defaults block of one preset. All fields are optional — unset
    falls through to existing Settings / VoiceRecord defaults per BR-10."""

    model_config = ConfigDict(extra="forbid")

    provider: str | None = None
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, gt=0.0, le=1.0)
    max_sentences_per_chunk: int | None = Field(default=None, ge=1)
    normalize_db: float | None = None
    response_format: Literal["wav", "wav24", "flac"] | None = None
    postprocess: PresetPostprocess | None = None


class PresetEntry(BaseModel):
    """One named preset. Every preset MUST carry label, description, defaults."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    description: str = Field(min_length=1)
    defaults: PresetDefaults


class PresetConfig(RootModel[dict[str, PresetEntry]]):
    """Root model parsing ``config/presets.json`` (flat name->entry dict).

    ``extra="forbid"`` lives on :class:`PresetEntry` / :class:`PresetDefaults` /
    :class:`PresetPostprocess`. Pydantic's ``RootModel`` does not accept
    a top-level ``extra`` config (it has no fields to constrain) — unknown
    keys at the file root land as new preset names, which is intended.
    """

    def items(self) -> Mapping[str, PresetEntry]:
        """Return the underlying name->entry mapping."""
        return self.root


@dataclass(frozen=True, slots=True)
class PresetRegistry:
    """Immutable snapshot of the parsed preset registry.

    Held on ``app.state.preset_registry``. S-029 replaces this object
    atomically on hot-reload; it is never mutated in place. Both S-028's
    resolver and S-029's swap-on-validate path read/write this shape.
    """

    _presets: Mapping[str, PresetEntry]

    def get(self, name: str) -> PresetEntry | None:
        """Return the named preset or ``None`` when absent."""
        return self._presets.get(name)

    def names(self) -> frozenset[str]:
        """Return the set of registered preset names."""
        return frozenset(self._presets.keys())

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._presets

    def __len__(self) -> int:
        return len(self._presets)


# ---------------------------------------------------------------------------
# Startup-fail error types — translated by lifespan into config_error.* codes.
# ---------------------------------------------------------------------------


class PresetsInvalidError(ValueError):
    """Raised on parse / schema validation failure (FR-PR-02 / UAT-PR-11)."""


class PresetsUnsafePermissionsError(ValueError):
    """Raised on owner-mismatch / world-writable presets.json (NFR-SE-09 / UAT-PR-14)."""


class PresetProviderInvalidError(ValueError):
    """Raised on unknown (provider, model) pair pinned in a preset (FR-PR-13 / UAT-PR-12)."""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _format_validation_error(exc: ValidationError) -> str:
    """Format a Pydantic ``ValidationError`` with ``presets.`` field paths.

    Each error gets one line: ``presets.<path>: <message>``. Paths are
    joined with ``.`` and list indices are rendered like ``[0]`` so that
    operators can locate the offending JSON node quickly.
    """
    lines: list[str] = []
    for err in exc.errors():
        loc_parts: list[str] = ["presets"]
        for piece in err.get("loc", ()):
            if isinstance(piece, int):
                loc_parts.append(f"[{piece}]")
            else:
                loc_parts.append(str(piece))
        path = ".".join(loc_parts).replace(".[", "[")
        lines.append(f"{path}: {err.get('msg', 'invalid value')}")
    return "; ".join(lines)


def load_preset_registry(path: Path) -> PresetRegistry:
    """Parse + validate ``presets.json`` and return a frozen registry.

    Raises :class:`PresetsInvalidError` with a field-path-prefixed message
    on any JSON / Pydantic failure.
    """
    if not path.exists():
        raise PresetsInvalidError(f"presets file not found: {path}")
    if not path.is_file():
        raise PresetsInvalidError(f"presets path is not a regular file: {path}")
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PresetsInvalidError(f"cannot read presets file {path}: {exc}") from exc
    try:
        raw_json = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise PresetsInvalidError(f"presets.json is not valid JSON: {exc}") from exc
    if not isinstance(raw_json, dict):
        raise PresetsInvalidError("presets.json top-level must be a JSON object of name -> preset")
    try:
        config = PresetConfig.model_validate(raw_json)
    except ValidationError as exc:
        raise PresetsInvalidError(_format_validation_error(exc)) from exc
    if not config.root:
        raise PresetsInvalidError("presets.json defines no presets")
    return PresetRegistry(_presets=dict(config.root))


# ---------------------------------------------------------------------------
# File-permission posture (NFR-SE-09)
# ---------------------------------------------------------------------------


def check_presets_file_permissions(path: Path) -> None:
    """Verify presets.json is owned by the service user and not world-writable.

    Raises :class:`PresetsUnsafePermissionsError` on any failure. Symlinks
    are resolved before stat — an attacker who can flip a symlink to a
    world-writable target still trips the check.

    NOTE: startup-only per RISK-PR-3 (documented limitation — hot-reload
    does NOT re-run this check; race window from ``mv`` + ``chmod`` is
    accepted).
    """
    try:
        st = path.stat()
    except OSError as exc:
        raise PresetsUnsafePermissionsError(f"cannot stat presets file {path}: {exc}") from exc

    process_uid = os.geteuid() if hasattr(os, "geteuid") else None
    if process_uid is not None and st.st_uid != process_uid:
        raise PresetsUnsafePermissionsError(
            f"presets file {path} is owned by uid={st.st_uid} but service runs as uid={process_uid}"
        )

    if st.st_mode & stat.S_IWOTH:
        raise PresetsUnsafePermissionsError(
            f"presets file {path} is world-writable (mode={oct(st.st_mode & 0o777)})"
        )


# ---------------------------------------------------------------------------
# Provider allow-list cross-check (FR-PR-13)
# ---------------------------------------------------------------------------


def validate_preset_providers(
    registry: PresetRegistry,
    allow_lists: Mapping[str, Mapping[str, object] | list[str] | frozenset[str]],
) -> None:
    """Cross-check every preset's pinned ``(provider, model)`` against allow-lists.

    ``allow_lists`` maps provider name → that provider's allowed model
    list (or any iterable of model names). A preset that pins a provider
    not in ``allow_lists`` OR a model not in that provider's list raises
    :class:`PresetProviderInvalidError` naming the offending preset.

    A preset that pins only ``provider`` (no ``model``) is checked for
    the provider name. A preset that pins only ``model`` (no ``provider``)
    is NOT checked here — auto-selected provider may legitimately pick
    a different one at runtime; runtime resolution validates that
    combination instead (S-028 / S-033 scope).
    """
    for name, entry in registry._presets.items():  # noqa: SLF001 — same module
        provider = entry.defaults.provider
        model = entry.defaults.model
        if provider is None:
            continue
        allowed = allow_lists.get(provider)
        if allowed is None:
            raise PresetProviderInvalidError(
                f"preset {name!r} pins unknown provider {provider!r}; "
                f"known providers: {sorted(allow_lists.keys())}"
            )
        if model is None:
            continue
        if model not in allowed:
            raise PresetProviderInvalidError(
                f"preset {name!r} pins model {model!r} for provider {provider!r}, "
                "but that model is not in the provider's allow-list"
            )
