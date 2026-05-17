"""Tests for the hardware-detection module (S-005 / FR-HW-01..03)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from llm_tts_api.engine import device as device_module
from llm_tts_api.engine.device import (
    DeviceProfile,
    detect_device,
    detect_dtype,
    resolve_device_profile,
)


def _fake_torch(*, mps: bool, cuda: bool) -> object:
    """Build a fake torch module exposing the two probes detect_device uses."""
    return SimpleNamespace(
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps)),
        cuda=SimpleNamespace(is_available=lambda: cuda),
    )


def _install_fake_torch(monkeypatch: pytest.MonkeyPatch, *, mps: bool, cuda: bool) -> None:
    """Swap the torch-import seam to return a fake torch with the given availability."""
    monkeypatch.setattr(device_module, "_try_import_torch", lambda: _fake_torch(mps=mps, cuda=cuda))


def _install_no_torch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Swap the torch-import seam to simulate torch being absent."""
    monkeypatch.setattr(device_module, "_try_import_torch", lambda: None)


@pytest.fixture(autouse=True)
def _clear_device_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop TTS_DEVICE / TTS_DTYPE so each test sees a clean environment."""
    monkeypatch.delenv("TTS_DEVICE", raising=False)
    monkeypatch.delenv("TTS_DTYPE", raising=False)


class TestDetectDevice:
    def test_torch_with_mps_available_returns_mps(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_torch(monkeypatch, mps=True, cuda=False)
        assert detect_device() == "mps"

    def test_torch_with_cuda_only_returns_cuda(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_torch(monkeypatch, mps=False, cuda=True)
        assert detect_device() == "cuda"

    def test_torch_present_neither_available_returns_cpu(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _install_fake_torch(monkeypatch, mps=False, cuda=False)
        assert detect_device() == "cpu"

    def test_torch_absent_on_apple_silicon_returns_mps(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # MLX-only install: torch isn't there, but MLX's Metal backend works on arm64 Darwin.
        _install_no_torch(monkeypatch)
        monkeypatch.setattr(device_module.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(device_module.platform, "machine", lambda: "arm64")
        assert detect_device() == "mps"

    def test_torch_absent_on_linux_returns_cpu(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_no_torch(monkeypatch)
        monkeypatch.setattr(device_module.platform, "system", lambda: "Linux")
        monkeypatch.setattr(device_module.platform, "machine", lambda: "x86_64")
        assert detect_device() == "cpu"

    def test_env_override_takes_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Pretend torch reports MPS, but the operator forces cpu.
        _install_fake_torch(monkeypatch, mps=True, cuda=False)
        monkeypatch.setenv("TTS_DEVICE", "cpu")
        assert detect_device() == "cpu"

    def test_explicit_override_argument_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Argument takes precedence over both env and auto-probe.
        _install_fake_torch(monkeypatch, mps=True, cuda=False)
        monkeypatch.setenv("TTS_DEVICE", "auto")
        assert detect_device("cuda") == "cuda"

    def test_invalid_override_raises(self) -> None:
        with pytest.raises(ValueError, match="TTS_DEVICE"):
            detect_device("gpu")

    def test_env_value_is_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TTS_DEVICE", "MPS")
        assert detect_device() == "mps"


class TestDetectDtype:
    def test_mps_defaults_to_float16(self) -> None:
        assert detect_dtype("mps") == "float16"

    def test_cuda_defaults_to_float16(self) -> None:
        assert detect_dtype("cuda") == "float16"

    def test_cpu_defaults_to_float32(self) -> None:
        assert detect_dtype("cpu") == "float32"

    def test_env_override_bfloat16(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TTS_DTYPE", "bfloat16")
        assert detect_dtype("mps") == "bfloat16"

    def test_explicit_override_wins(self) -> None:
        assert detect_dtype("mps", "float32") == "float32"

    def test_invalid_override_raises(self) -> None:
        with pytest.raises(ValueError, match="TTS_DTYPE"):
            detect_dtype("mps", "int8")


class TestResolveDeviceProfile:
    def test_auto_path_reports_auto_source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_torch(monkeypatch, mps=True, cuda=False)
        profile = resolve_device_profile()
        assert profile == DeviceProfile(device="mps", dtype="float16", source="auto")

    def test_device_env_override_reports_env_source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_torch(monkeypatch, mps=True, cuda=False)
        monkeypatch.setenv("TTS_DEVICE", "cpu")
        profile = resolve_device_profile()
        assert profile.device == "cpu"
        assert profile.dtype == "float32"  # cpu default
        assert profile.source == "env"

    def test_dtype_env_override_reports_env_source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_torch(monkeypatch, mps=True, cuda=False)
        monkeypatch.setenv("TTS_DTYPE", "bfloat16")
        profile = resolve_device_profile()
        assert profile.device == "mps"
        assert profile.dtype == "bfloat16"
        assert profile.source == "env"

    def test_profile_is_frozen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_torch(monkeypatch, mps=True, cuda=False)
        profile = resolve_device_profile()
        with pytest.raises(AttributeError):
            # frozen dataclass — attribute assignment must fail.
            profile.device = "cpu"  # type: ignore[misc]

    def test_explicit_arguments_win_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TTS_DEVICE", "cpu")
        monkeypatch.setenv("TTS_DTYPE", "bfloat16")
        _install_fake_torch(monkeypatch, mps=False, cuda=True)
        profile = resolve_device_profile(device_override="cuda", dtype_override="float16")
        assert profile.device == "cuda"
        assert profile.dtype == "float16"
        # source is still "env" because the explicit overrides are non-auto values
        assert profile.source == "env"


class TestTryImportTorch:
    """Deterministic tests for the torch-import seam.

    SF-13 replaced an earlier smoke test that accepted either return value
    (and therefore asserted nothing meaningful in the MLX-only test env).
    These tests pin both branches by manipulating the import machinery.
    """

    def test_returns_imported_module_when_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Make `importlib.import_module("torch")` return our fake.
        fake = _fake_torch(mps=True, cuda=False)
        monkeypatch.setattr(
            device_module.importlib,
            "import_module",
            lambda name: fake if name == "torch" else None,
        )
        assert device_module._try_import_torch() is fake

    def test_returns_none_on_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(_name: str) -> object:
            raise ImportError("no torch in this env")

        monkeypatch.setattr(device_module.importlib, "import_module", _raise)
        assert device_module._try_import_torch() is None

    def test_returns_none_on_non_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """SF-11: a broken torch install (e.g. RuntimeError from CUDA init)
        must NOT crash startup — the soft-import returns None and a warning
        is logged."""

        def _raise(_name: str) -> object:
            raise RuntimeError("CUDA driver / runtime mismatch")

        monkeypatch.setattr(device_module.importlib, "import_module", _raise)
        assert device_module._try_import_torch() is None


class TestEmptyOverrides:
    """SF-10: empty / whitespace env values are common 'defined-but-unset'
    shell-wrapper states and must be treated as auto, not as hard errors."""

    @pytest.mark.parametrize("value", ["", "   ", "\t"])
    def test_empty_device_override_falls_back_to_auto(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        _install_fake_torch(monkeypatch, mps=True, cuda=False)
        # Empty override argument
        assert detect_device(value) == "mps"
        # Empty env var
        monkeypatch.setenv("TTS_DEVICE", value)
        assert detect_device() == "mps"

    @pytest.mark.parametrize("value", ["", "   ", "\t"])
    def test_empty_dtype_override_falls_back_to_auto(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        # MPS default dtype
        assert detect_dtype("mps", value) == "float16"
        # CPU default dtype
        monkeypatch.setenv("TTS_DTYPE", value)
        assert detect_dtype("cpu") == "float32"
