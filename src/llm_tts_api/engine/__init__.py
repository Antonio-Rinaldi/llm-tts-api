"""Engine layer: device detection and (future) inference-pipeline abstractions."""

from llm_tts_api.engine.device import (
    DeviceProfile,
    detect_device,
    detect_dtype,
    resolve_device_profile,
)

__all__ = [
    "DeviceProfile",
    "detect_device",
    "detect_dtype",
    "resolve_device_profile",
]
