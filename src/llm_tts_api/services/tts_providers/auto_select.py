"""Provider auto-selection from the resolved :class:`DeviceProfile`.

Implements FR-HW-04..07 (SRS §4.1 / analyst-frs.md):

* If ``TTS_PROVIDER`` is unset (or ``auto`` / empty), pick the first
  registered provider whose ``supports_devices`` set contains the detected
  device. Registration order is authoritative — the registry constructor
  in :mod:`llm_tts_api.dependencies` orders providers as ``mlx_audio``,
  ``voxtral``, ``vllm-omni`` so MPS hosts pick ``mlx_audio`` first.
* If ``TTS_PROVIDER`` is set, it overrides auto-selection but is still
  validated against the device. An override naming an unknown provider, or
  a provider whose ``supports_devices`` does not contain the detected
  device, raises :class:`ProviderSelectionError` so startup fails fast
  (FR-HW-05/06).

The selection result is stashed on ``app.state.provider_selection`` so the
``/health`` endpoint can report the picked provider plus an ``auto`` vs.
``env`` source label.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from llm_tts_api.engine import DeviceProfile
from llm_tts_api.services.tts_providers.registry import TTSProviderRegistry

SelectionSource = Literal["auto", "env"]

# Truthy markers that mean "no override" — accepted for the env var so
# operators can wire `TTS_PROVIDER=auto` in their config without crashing.
_AUTO_OVERRIDE_TOKENS: frozenset[str] = frozenset({"", "auto"})


@dataclass(frozen=True, slots=True)
class ProviderRejection:
    """One row of the rejection table emitted on no-viable-provider."""

    provider: str
    reason: str


@dataclass(frozen=True, slots=True)
class ProviderSelection:
    """The auto-selection (or override-validated) outcome for this process."""

    provider_name: str
    device: str
    source: SelectionSource


class ProviderSelectionError(RuntimeError):
    """Raised when no registered provider can serve the detected device.

    Carries the structured fields S-009 will surface through the error
    envelope: ``error_type`` and ``error_code`` map to the envelope's
    ``type`` / ``code``; ``rejections`` is the per-provider audit trail
    operators read in startup logs.
    """

    error_type = "provider_error"
    error_code = "no_viable_provider"

    def __init__(
        self,
        message: str,
        rejections: list[ProviderRejection],
    ) -> None:
        """Initialize with a human message and the structured rejection table."""
        super().__init__(message)
        self.rejections = list(rejections)

    @classmethod
    def for_device(cls, device: str, rejections: list[ProviderRejection]) -> ProviderSelectionError:
        """Construct the canonical no-viable-provider error for a device."""
        rows = "; ".join(f"{r.provider}: {r.reason}" for r in rejections)
        table = rows or "no providers registered"
        msg = (
            f"provider_error.no_viable_provider: no registered TTS provider "
            f"supports device {device!r} ({table})"
        )
        return cls(msg, rejections)

    @classmethod
    def for_override(cls, override: str, device: str, reason: str) -> ProviderSelectionError:
        """Construct a typed error for an explicit but incompatible override."""
        msg = (
            f"provider_error.no_viable_provider: TTS_PROVIDER={override!r} "
            f"cannot serve device {device!r}: {reason}"
        )
        return cls(msg, [ProviderRejection(provider=override, reason=reason)])


def _read_override_from_env() -> str | None:
    """Read the ``TTS_PROVIDER`` env var as a normalized override.

    Returns ``None`` when the variable is unset, empty, or set to ``auto``
    (auto-selection mode). Returns the lowercased provider name otherwise.
    """
    raw = os.environ.get("TTS_PROVIDER")
    if raw is None:
        return None
    candidate = raw.strip().lower()
    if candidate in _AUTO_OVERRIDE_TOKENS:
        return None
    return candidate


def select_provider(
    *,
    device_profile: DeviceProfile,
    registry: TTSProviderRegistry,
    override: str | None = None,
) -> ProviderSelection:
    """Pick a provider for the given device, honouring an explicit override.

    Args:
        device_profile: Resolved hardware profile (S-005).
        registry: Process-wide provider registry. Iteration order is the
            registration order — used as the auto-select priority.
        override: Optional pre-parsed override name. When ``None`` we read
            ``TTS_PROVIDER`` from the environment so callers don't have to.
            Pass an explicit value (including ``""``) to bypass the env read
            in tests.

    Returns:
        The :class:`ProviderSelection` describing the chosen provider and
        whether the choice came from auto-detection or an env override.

    Raises:
        ProviderSelectionError: when the override is unknown, when the
            override is incompatible with the device, or when no registered
            provider supports the detected device.
    """
    resolved_override = override if override is not None else _read_override_from_env()
    device = device_profile.device

    if resolved_override is not None:
        return _validate_override(resolved_override, device, registry)

    return _select_auto(device, registry)


def _validate_override(
    override: str, device: str, registry: TTSProviderRegistry
) -> ProviderSelection:
    """Validate an explicit ``TTS_PROVIDER`` against the device profile."""
    provider = registry.find(override)
    if provider is None:
        known = ", ".join(sorted(registry.names())) or "(none registered)"
        raise ProviderSelectionError.for_override(
            override,
            device,
            f"unknown provider; known providers: {known}",
        )

    supports = provider.supports_devices
    if device not in supports:
        raise ProviderSelectionError.for_override(
            override,
            device,
            f"provider supports devices {sorted(supports)} but device is {device!r}",
        )

    return ProviderSelection(provider_name=override, device=device, source="env")


def _select_auto(device: str, registry: TTSProviderRegistry) -> ProviderSelection:
    """Pick the first registered provider that declares support for the device."""
    rejections: list[ProviderRejection] = []
    for provider in registry.all():
        if device in provider.supports_devices:
            return ProviderSelection(
                provider_name=provider.provider_name,
                device=device,
                source="auto",
            )
        rejections.append(
            ProviderRejection(
                provider=provider.provider_name,
                reason=f"supports_devices={sorted(provider.supports_devices)}",
            )
        )

    raise ProviderSelectionError.for_device(device, rejections)
