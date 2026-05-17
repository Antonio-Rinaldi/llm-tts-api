"""Hardware detection: pick the inference device and dtype.

Implements FR-HW-01..03 (SRS §4.1):

* ``detect_device()`` — Apple Silicon MPS → NVIDIA CUDA → CPU, with an
  optional env-driven override.
* ``detect_dtype()`` — float16 on MPS/CUDA, float32 on CPU, with an
  optional env-driven override.
* ``DeviceProfile`` — immutable record consumed by the provider auto-selection
  layer (S-006) at startup.

The detection is **torch-soft**: if PyTorch is importable, its
``torch.backends.mps.is_available`` / ``torch.cuda.is_available`` probes are
authoritative. If PyTorch is absent (the default MLX-only install) the module
falls back to platform/architecture detection — Apple Silicon Darwin reports
``mps`` because MLX uses the Metal backend regardless of PyTorch's presence.

Env overrides:

* ``TTS_DEVICE`` ∈ ``{auto, mps, cuda, cpu}`` — default ``auto``.
* ``TTS_DTYPE`` ∈ ``{auto, float16, bfloat16, float32}`` — default ``auto``.
"""

from __future__ import annotations

import importlib
import logging
import os
import platform
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

Device = Literal["mps", "cuda", "cpu"]
Dtype = Literal["float16", "bfloat16", "float32"]
DetectionSource = Literal["auto", "env"]

_VALID_DEVICES: frozenset[str] = frozenset({"mps", "cuda", "cpu"})
_VALID_DTYPES: frozenset[str] = frozenset({"float16", "bfloat16", "float32"})


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    """Resolved view of the inference target.

    Constructed once at startup by :func:`resolve_device_profile` and stashed
    on ``app.state`` for the lifespan of the process. Immutable so it can be
    shared across the event loop without locking.
    """

    device: Device
    dtype: Dtype
    source: DetectionSource


def detect_device(override: str | None = None) -> Device:
    """Return the inference device.

    ``override`` (or env ``TTS_DEVICE`` when ``override`` is ``None``) wins
    over auto-detection when set to a concrete device name. ``auto`` (or
    unset) triggers the MPS → CUDA → CPU probe.
    """
    raw = override if override is not None else os.environ.get("TTS_DEVICE", "auto")
    raw = raw.strip().lower()
    # SF-10: empty / whitespace-only values are a common "defined-but-unset"
    # state from shell wrappers (e.g. `export TTS_DEVICE=$DEVICE` where
    # `$DEVICE` is empty). Treat as auto rather than crashing startup.
    if not raw:
        raw = "auto"

    if raw != "auto":
        if raw not in _VALID_DEVICES:
            raise ValueError(
                f"TTS_DEVICE={raw!r} is not a valid device "
                f"(expected one of: auto, {', '.join(sorted(_VALID_DEVICES))})"
            )
        return raw  # type: ignore[return-value]

    return _probe_device()


def detect_dtype(device: Device, override: str | None = None) -> Dtype:
    """Return the inference dtype, conditioned on the device.

    Auto rules: float16 on MPS/CUDA, float32 on CPU.
    """
    raw = override if override is not None else os.environ.get("TTS_DTYPE", "auto")
    raw = raw.strip().lower()
    # SF-10: empty / whitespace → auto (see detect_device).
    if not raw:
        raw = "auto"

    if raw != "auto":
        if raw not in _VALID_DTYPES:
            raise ValueError(
                f"TTS_DTYPE={raw!r} is not a valid dtype "
                f"(expected one of: auto, {', '.join(sorted(_VALID_DTYPES))})"
            )
        return raw  # type: ignore[return-value]

    return "float32" if device == "cpu" else "float16"


def resolve_device_profile(
    device_override: str | None = None,
    dtype_override: str | None = None,
) -> DeviceProfile:
    """Build the immutable :class:`DeviceProfile` for this process.

    ``device_override`` and ``dtype_override`` accept the same values as the
    ``TTS_DEVICE`` / ``TTS_DTYPE`` env vars and take precedence over them.
    Pass ``None`` to read directly from the environment (the lifespan path).
    """
    raw_device = (
        device_override if device_override is not None else os.environ.get("TTS_DEVICE", "auto")
    )
    raw_dtype = (
        dtype_override if dtype_override is not None else os.environ.get("TTS_DTYPE", "auto")
    )

    device_source: DetectionSource = "env" if raw_device.strip().lower() != "auto" else "auto"
    dtype_source: DetectionSource = "env" if raw_dtype.strip().lower() != "auto" else "auto"

    device = detect_device(raw_device)
    dtype = detect_dtype(device, raw_dtype)

    # A single profile carries one source label. When either field is
    # env-driven we report "env" so operators can grep startup logs to see
    # that auto-detection was overridden.
    overall_source: DetectionSource = (
        "env" if device_source == "env" or dtype_source == "env" else "auto"
    )

    profile = DeviceProfile(device=device, dtype=dtype, source=overall_source)
    # SF-12: use structured extras so JSON-format consumers can query
    # `device:"mps"` etc. as top-level fields instead of regexing the message.
    logger.info(
        "device profile resolved",
        extra={
            "device": profile.device,
            "dtype": profile.dtype,
            "source": profile.source,
        },
    )
    return profile


def _probe_device() -> Device:
    """Probe the host for the best available device, MPS → CUDA → CPU.

    Tries PyTorch first when present; falls back to platform/arch detection
    so MLX-only installs still report ``mps`` on Apple Silicon.
    """
    torch = _try_import_torch()
    if torch is not None:
        backends = getattr(torch, "backends", None)
        mps_backend = getattr(backends, "mps", None) if backends is not None else None
        mps_is_available = getattr(mps_backend, "is_available", None) if mps_backend else None
        if callable(mps_is_available) and mps_is_available():
            return "mps"

        cuda_module = getattr(torch, "cuda", None)
        cuda_is_available = getattr(cuda_module, "is_available", None) if cuda_module else None
        if callable(cuda_is_available) and cuda_is_available():
            return "cuda"

        return "cpu"

    # Torch unavailable: MLX is the only inference path. MLX uses the Metal
    # backend on Apple Silicon Darwin without requiring torch, so report
    # "mps" there. Everything else falls to CPU until a torch-using provider
    # is installed.
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "mps"
    return "cpu"


def _try_import_torch() -> object | None:
    """Import torch if available; return ``None`` on any import failure.

    Centralised so tests can monkeypatch this single seam to simulate
    torch-present and torch-absent environments.

    SF-11: catches **any** exception during import (not just ``ImportError``).
    A broken torch install can fail with ``RuntimeError`` (CUDA driver
    mismatch on first GPU touch), ``OSError`` (missing shared library), or
    ``AttributeError`` mid-init. The "torch-soft" framing means none of
    these should crash startup — the MLX-only path stays viable.
    """
    try:
        return importlib.import_module("torch")
    except Exception as exc:  # noqa: BLE001 — intentional: torch-soft
        logger.warning(
            "torch import failed; falling back to platform detection",
            extra={"error_type": type(exc).__name__, "error": str(exc)},
        )
        return None
