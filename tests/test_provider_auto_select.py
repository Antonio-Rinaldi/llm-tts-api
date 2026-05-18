"""Tests for S-006 provider auto-selection (FR-HW-04..07).

Covers:

* Capability-driven auto-selection across devices.
* ``TTS_PROVIDER`` env override semantics (still validated against the
  detected device — FR-HW-06).
* UAT-HW-04: ``TTS_DEVICE=cpu`` + no CPU-viable provider → typed startup
  error listing the rejected providers with their reasons.
* UAT-HW-05: ``TTS_PROVIDER=vllm-omni`` on Apple Silicon → typed startup
  error explaining the device mismatch.
"""

from __future__ import annotations

import pytest

from llm_tts_api.engine import DeviceProfile
from llm_tts_api.services.tts_providers.auto_select import (
    ProviderRejection,
    ProviderSelection,
    ProviderSelectionError,
    select_provider,
)
from llm_tts_api.services.tts_providers.mlx_audio_provider import MLXAudioTTSProvider
from llm_tts_api.services.tts_providers.registry import TTSProviderRegistry
from llm_tts_api.services.tts_providers.vllm_omni_provider import VllmOmniTTSProvider
from llm_tts_api.services.tts_providers.voxtral_provider import VoxtralTTSProvider


def _registry() -> TTSProviderRegistry:
    """Build the same registry shape ``build_default_dependencies`` uses."""
    return TTSProviderRegistry(
        providers=[
            MLXAudioTTSProvider(),
            VoxtralTTSProvider(),
            VllmOmniTTSProvider(),
        ]
    )


def _profile(device: str) -> DeviceProfile:
    """Build a ``DeviceProfile`` with sensible dtype for the given device."""
    dtype = "float32" if device == "cpu" else "float16"
    return DeviceProfile(device=device, dtype=dtype, source="auto")  # type: ignore[arg-type]


# --- capability declarations (T2) -------------------------------------------


def test_each_provider_declares_supports_devices() -> None:
    """T2: every registered provider must declare a non-empty support set."""
    for provider in _registry().all():
        assert isinstance(provider.supports_devices, frozenset)
        assert provider.supports_devices  # non-empty


def test_mlx_audio_supports_mps_only() -> None:
    assert MLXAudioTTSProvider.supports_devices == frozenset({"mps"})


def test_voxtral_supports_mps_only() -> None:
    assert VoxtralTTSProvider.supports_devices == frozenset({"mps"})


def test_vllm_omni_supports_cuda_only() -> None:
    assert VllmOmniTTSProvider.supports_devices == frozenset({"cuda"})


# --- auto-selection (T3) ----------------------------------------------------


def test_auto_select_on_mps_returns_mlx_audio() -> None:
    selection = select_provider(
        device_profile=_profile("mps"),
        registry=_registry(),
        override=None,
    )
    assert selection == ProviderSelection(provider_name="mlx_audio", device="mps", source="auto")


def test_auto_select_on_cuda_returns_vllm_omni() -> None:
    selection = select_provider(
        device_profile=_profile("cuda"),
        registry=_registry(),
        override=None,
    )
    assert selection == ProviderSelection(provider_name="vllm-omni", device="cuda", source="auto")


def test_explicit_override_on_compatible_device_marks_source_env() -> None:
    selection = select_provider(
        device_profile=_profile("mps"),
        registry=_registry(),
        override="voxtral",
    )
    assert selection == ProviderSelection(provider_name="voxtral", device="mps", source="env")


def test_env_override_via_environment_is_picked_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TTS_PROVIDER", "voxtral")
    selection = select_provider(
        device_profile=_profile("mps"),
        registry=_registry(),
    )
    assert selection.source == "env"
    assert selection.provider_name == "voxtral"


@pytest.mark.parametrize("token", ["", "auto", "AUTO", "  ", "  Auto "])
def test_auto_token_in_env_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch, token: str) -> None:
    monkeypatch.setenv("TTS_PROVIDER", token)
    selection = select_provider(
        device_profile=_profile("mps"),
        registry=_registry(),
    )
    assert selection.source == "auto"


# --- typed startup-failure path (T4) ---------------------------------------


def test_uat_hw_04_no_cpu_viable_provider_raises_typed_error() -> None:
    """UAT-HW-04: cpu detection + no CPU-viable provider → typed error."""
    with pytest.raises(ProviderSelectionError) as exc_info:
        select_provider(
            device_profile=_profile("cpu"),
            registry=_registry(),
            override=None,
        )

    err = exc_info.value
    assert err.error_type == "provider_error"
    assert err.error_code == "no_viable_provider"
    # Each registered provider must appear in the rejection list.
    rejected_names = {r.provider for r in err.rejections}
    assert rejected_names == {"mlx_audio", "voxtral", "vllm-omni"}
    # The reason must reference each provider's actual support set so
    # operators can debug from the log line alone.
    for rejection in err.rejections:
        assert "supports_devices" in rejection.reason
    # Top-level message carries the typed code path.
    assert "provider_error.no_viable_provider" in str(err)


def test_uat_hw_05_incompatible_override_raises_typed_error() -> None:
    """UAT-HW-05: ``TTS_PROVIDER=vllm-omni`` on MPS → typed startup error."""
    with pytest.raises(ProviderSelectionError) as exc_info:
        select_provider(
            device_profile=_profile("mps"),
            registry=_registry(),
            override="vllm-omni",
        )

    err = exc_info.value
    assert err.error_type == "provider_error"
    assert err.error_code == "no_viable_provider"
    assert err.rejections == [
        ProviderRejection(
            provider="vllm-omni",
            reason="provider supports devices ['cuda'] but device is 'mps'",
        )
    ]
    assert "provider_error.no_viable_provider" in str(err)


def test_unknown_override_lists_known_providers() -> None:
    with pytest.raises(ProviderSelectionError) as exc_info:
        select_provider(
            device_profile=_profile("mps"),
            registry=_registry(),
            override="bogus",
        )

    err = exc_info.value
    assert err.error_code == "no_viable_provider"
    assert "unknown provider" in err.rejections[0].reason
    assert "mlx_audio" in err.rejections[0].reason


def test_uat_hw_05_via_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same scenario as ``test_uat_hw_05_incompatible_override`` but the
    override is read from the environment rather than passed explicitly."""
    monkeypatch.setenv("TTS_PROVIDER", "vllm-omni")
    with pytest.raises(ProviderSelectionError):
        select_provider(
            device_profile=_profile("mps"),
            registry=_registry(),
        )


# --- /health surfaces provider info (T5) ------------------------------------


def test_health_reports_provider_with_env_source_when_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T5: ``/health`` must report ``provider_source=env`` after an override."""
    from fastapi.testclient import TestClient

    from llm_tts_api.engine import DeviceProfile as DP
    from llm_tts_api.main import TEST_BYPASS_ENV, create_app

    monkeypatch.setenv(TEST_BYPASS_ENV, "1")
    app = create_app()
    app.state.provider_selection = ProviderSelection(
        provider_name="voxtral", device="mps", source="env"
    )
    app.state.device_profile = DP(device="mps", dtype="float16", source="env")

    with TestClient(app) as test_client:
        response = test_client.get("/health")

    body = response.json()
    assert response.status_code == 200
    assert body["provider"] == "voxtral"
    assert body["provider_source"] == "env"
    assert body["device"] == "mps"
