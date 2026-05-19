"""S-027 — Presets configuration foundation tests.

Covers:

* UAT-PR-11 — invalid presets.json fails startup with ``config_error.presets_invalid``
  and a field-path message.
* UAT-PR-12 — preset pinning an unknown (provider, model) fails with
  ``config_error.preset_provider_invalid``.
* UAT-PR-13 — ``TTS_DEFAULT_PRESET`` naming an unknown preset fails startup.
* UAT-PR-14 — world-writable ``presets.json`` fails with
  ``config_error.presets_unsafe_permissions``.
* Pydantic schema invariants (extra="forbid", field-path messages,
  bounded numeric ranges).
* Permission-check unit test using ``tempfile`` + ``chmod 0o666``.

Each UAT-PR test exercises the same code path that ``main.lifespan``
calls (``initialize_preset_registry``) plus the ``SystemExit``-wrapping
helper from ``main`` so the "process exits non-zero with the right
code" expectation is verified end-to-end without spinning up uvicorn.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from llm_tts_api.config import Settings
from llm_tts_api.errors import (
    CONFIG_ERROR_PRESET_PROVIDER_INVALID,
    CONFIG_ERROR_PRESETS_INVALID,
    CONFIG_ERROR_PRESETS_UNSAFE_PERMISSIONS,
)
from llm_tts_api.main import _load_presets_or_exit
from llm_tts_api.services.presets import (
    PresetConfig,
    PresetProviderInvalidError,
    PresetRegistry,
    PresetsInvalidError,
    PresetsUnsafePermissionsError,
    check_presets_file_permissions,
    initialize_preset_registry,
    load_preset_registry,
)
from llm_tts_api.services.tts_providers.registry import TTSProviderRegistry

REPO_ROOT = Path(__file__).resolve().parents[1]
SHIPPED_PRESETS = REPO_ROOT / "config" / "presets.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_settings(
    presets_path: Path,
    *,
    default_preset: str = "balanced",
) -> Settings:
    """Construct a Settings with empty env, then overwrite the preset slots.

    Avoids depending on ``TTS_VOICE_MAP_FILE`` etc. — uses ``object.__new__``
    to skip ``__post_init__`` and fills only the fields the preset
    initializer touches.
    """
    settings = object.__new__(Settings)
    settings.tts_presets_file = presets_path
    settings.tts_default_preset = default_preset
    settings.tts_mlx_audio_model_allowed = ["Qwen/Qwen3-TTS-12Hz-0.6B-Base"]
    settings.tts_voxtral_model_allowed = ["mlx-community/Voxtral-4B-TTS-2603-mlx-4bit"]
    settings.tts_vllm_omni_model_allowed = ["vllm-omni/default-tts"]
    return settings


def _provider_registry_with_mlx() -> TTSProviderRegistry:
    """A registry with one fake provider matching the mlx_audio allow-list."""

    class _FakeProvider:
        provider_name = "mlx_audio"

    return TTSProviderRegistry([_FakeProvider()])  # type: ignore[list-item]


def _write_presets(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
    # Ensure the new fixture is owned by us and not world-writable; the
    # permission-check tests deliberately tamper with this afterwards.
    os.chmod(path, 0o644)


def _valid_payload() -> dict[str, object]:
    """A minimal valid two-preset payload covering the default-preset case."""
    return {
        "fast": {
            "label": "Fast",
            "description": "low TTFB",
            "defaults": {"temperature": 0.7, "response_format": "wav"},
        },
        "balanced": {
            "label": "Balanced",
            "description": "default",
            "defaults": {"temperature": 0.8},
        },
    }


# ---------------------------------------------------------------------------
# Pydantic schema unit tests (T1)
# ---------------------------------------------------------------------------


def test_shipped_presets_parse_cleanly() -> None:
    """The three built-in presets MUST parse without errors (FR-PR-01)."""
    registry = load_preset_registry(SHIPPED_PRESETS)
    assert registry.names() == frozenset({"fast", "balanced", "quality"})


def test_extra_forbid_at_root(tmp_path: Path) -> None:
    """Unknown preset-level fields raise with a clear field-path message."""
    presets = tmp_path / "presets.json"
    _write_presets(
        presets,
        {
            "fast": {
                "label": "Fast",
                "description": "x",
                "defaults": {},
                "extras": {"oops": True},
            },
            "balanced": {"label": "B", "description": "d", "defaults": {}},
        },
    )
    with pytest.raises(PresetsInvalidError) as exc_info:
        load_preset_registry(presets)
    assert "presets.fast.extras" in str(exc_info.value)


def test_preset_config_module_surface() -> None:
    """:class:`PresetConfig` is re-exported and usable as a Pydantic root model."""
    config = PresetConfig.model_validate(
        {"balanced": {"label": "B", "description": "d", "defaults": {}}}
    )
    assert list(config.items().keys()) == ["balanced"]


def test_extra_forbid_inside_defaults(tmp_path: Path) -> None:
    """Unknown ``defaults.*`` field paths surface with ``presets.<name>.defaults.<field>``."""
    presets = tmp_path / "presets.json"
    _write_presets(
        presets,
        {
            "balanced": {
                "label": "B",
                "description": "d",
                "defaults": {"temperature": 0.5, "unsupported_knob": 1},
            }
        },
    )
    with pytest.raises(PresetsInvalidError) as exc_info:
        load_preset_registry(presets)
    message = str(exc_info.value)
    assert "presets.balanced.defaults.unsupported_knob" in message


def test_field_path_in_error_includes_presets_prefix(tmp_path: Path) -> None:
    """UAT-PR-11 — non-numeric temperature surfaces a typed field-path message."""
    presets = tmp_path / "presets.json"
    _write_presets(
        presets,
        {
            "fast": {
                "label": "Fast",
                "description": "x",
                "defaults": {"temperature": "not-a-number"},
            },
            "balanced": {"label": "B", "description": "d", "defaults": {}},
        },
    )
    with pytest.raises(PresetsInvalidError) as exc_info:
        load_preset_registry(presets)
    assert "presets.fast.defaults.temperature" in str(exc_info.value)


def test_temperature_out_of_bounds_rejected(tmp_path: Path) -> None:
    """``temperature`` MUST be within [0.0, 2.0]; out-of-range surfaces clearly."""
    presets = tmp_path / "presets.json"
    _write_presets(
        presets,
        {
            "balanced": {
                "label": "B",
                "description": "d",
                "defaults": {"temperature": 3.5},
            }
        },
    )
    with pytest.raises(PresetsInvalidError) as exc_info:
        load_preset_registry(presets)
    assert "presets.balanced.defaults.temperature" in str(exc_info.value)


def test_invalid_response_format_rejected(tmp_path: Path) -> None:
    """Only ``wav`` / ``wav24`` / ``flac`` are accepted."""
    presets = tmp_path / "presets.json"
    _write_presets(
        presets,
        {
            "balanced": {
                "label": "B",
                "description": "d",
                "defaults": {"response_format": "ogg"},
            }
        },
    )
    with pytest.raises(PresetsInvalidError) as exc_info:
        load_preset_registry(presets)
    assert "presets.balanced.defaults.response_format" in str(exc_info.value)


def test_top_level_not_object_rejected(tmp_path: Path) -> None:
    """A non-object top-level fails fast — the file shape MUST be a dict."""
    presets = tmp_path / "presets.json"
    presets.write_text("[]", encoding="utf-8")
    os.chmod(presets, 0o644)
    with pytest.raises(PresetsInvalidError):
        load_preset_registry(presets)


def test_empty_object_rejected(tmp_path: Path) -> None:
    """An empty file defines no presets and is rejected."""
    presets = tmp_path / "presets.json"
    _write_presets(presets, {})
    with pytest.raises(PresetsInvalidError):
        load_preset_registry(presets)


def test_registry_get_and_names() -> None:
    """``PresetRegistry.get`` and ``.names`` form the contract for S-028 / S-029."""
    registry = load_preset_registry(SHIPPED_PRESETS)
    assert "balanced" in registry
    assert registry.get("balanced") is not None
    assert registry.get("nope") is None
    assert "balanced" in registry.names()


# ---------------------------------------------------------------------------
# File-permission posture (NFR-SE-09)
# ---------------------------------------------------------------------------


def test_world_writable_presets_rejected(tmp_path: Path) -> None:
    """UAT-PR-14 — world-writable file refuses startup."""
    presets = tmp_path / "presets.json"
    _write_presets(presets, _valid_payload())
    os.chmod(presets, 0o666)  # world-writable
    with pytest.raises(PresetsUnsafePermissionsError) as exc_info:
        check_presets_file_permissions(presets)
    assert "world-writable" in str(exc_info.value)


def test_permission_check_passes_on_safe_mode(tmp_path: Path) -> None:
    """A 0o644 file owned by the running user passes the posture check."""
    presets = tmp_path / "presets.json"
    _write_presets(presets, _valid_payload())
    os.chmod(presets, 0o644)
    # Must not raise.
    check_presets_file_permissions(presets)


# ---------------------------------------------------------------------------
# Startup orchestration (T4 — initialize_preset_registry + lifespan helper)
# ---------------------------------------------------------------------------


def test_initialize_preset_registry_happy_path(tmp_path: Path) -> None:
    """Happy path returns a populated :class:`PresetRegistry`."""
    presets = tmp_path / "presets.json"
    _write_presets(presets, _valid_payload())
    settings = _build_settings(presets, default_preset="balanced")
    registry = initialize_preset_registry(settings, _provider_registry_with_mlx())
    assert isinstance(registry, PresetRegistry)
    assert "balanced" in registry


def test_uat_pr_11_invalid_presets_exits_with_code(tmp_path: Path) -> None:
    """UAT-PR-11 — Pydantic violation → ``config_error.presets_invalid``."""
    presets = tmp_path / "presets.json"
    _write_presets(
        presets,
        {
            "fast": {
                "label": "Fast",
                "description": "x",
                "defaults": {"temperature": "not-a-number"},
            },
            "balanced": {"label": "B", "description": "d", "defaults": {}},
        },
    )
    settings = _build_settings(presets, default_preset="balanced")
    with pytest.raises(SystemExit) as exc_info:
        _load_presets_or_exit(settings, _provider_registry_with_mlx())
    message = str(exc_info.value.code) if exc_info.value.code is not None else ""
    assert message.startswith(CONFIG_ERROR_PRESETS_INVALID)
    assert "presets.fast.defaults.temperature" in message


def test_uat_pr_12_preset_pins_unknown_model(tmp_path: Path) -> None:
    """UAT-PR-12 — preset pins a model not in any allow-list."""
    presets = tmp_path / "presets.json"
    _write_presets(
        presets,
        {
            "balanced": {"label": "B", "description": "d", "defaults": {}},
            "quality": {
                "label": "Q",
                "description": "q",
                "defaults": {"provider": "mlx_audio", "model": "nonexistent-model"},
            },
        },
    )
    settings = _build_settings(presets, default_preset="balanced")
    with pytest.raises(SystemExit) as exc_info:
        _load_presets_or_exit(settings, _provider_registry_with_mlx())
    message = str(exc_info.value.code) if exc_info.value.code is not None else ""
    assert message.startswith(CONFIG_ERROR_PRESET_PROVIDER_INVALID)
    assert "quality" in message
    assert "nonexistent-model" in message


def test_uat_pr_12_preset_pins_unknown_provider(tmp_path: Path) -> None:
    """A preset pinning a provider not in the registry also fails startup."""
    presets = tmp_path / "presets.json"
    _write_presets(
        presets,
        {
            "balanced": {"label": "B", "description": "d", "defaults": {}},
            "quality": {
                "label": "Q",
                "description": "q",
                "defaults": {"provider": "ghost_provider", "model": "x"},
            },
        },
    )
    settings = _build_settings(presets, default_preset="balanced")
    with pytest.raises(PresetProviderInvalidError):
        initialize_preset_registry(settings, _provider_registry_with_mlx())


def test_uat_pr_13_bogus_default_preset(tmp_path: Path) -> None:
    """UAT-PR-13 — ``TTS_DEFAULT_PRESET=bogus`` refuses startup."""
    presets = tmp_path / "presets.json"
    _write_presets(presets, _valid_payload())
    settings = _build_settings(presets, default_preset="bogus")
    with pytest.raises(SystemExit) as exc_info:
        _load_presets_or_exit(settings, _provider_registry_with_mlx())
    message = str(exc_info.value.code) if exc_info.value.code is not None else ""
    assert message.startswith(CONFIG_ERROR_PRESETS_INVALID)
    assert "TTS_DEFAULT_PRESET" in message
    assert "bogus" in message


def test_uat_pr_14_world_writable_presets_exits(tmp_path: Path) -> None:
    """UAT-PR-14 — world-writable file exits with ``config_error.presets_unsafe_permissions``."""
    presets = tmp_path / "presets.json"
    _write_presets(presets, _valid_payload())
    os.chmod(presets, 0o666)
    settings = _build_settings(presets, default_preset="balanced")
    with pytest.raises(SystemExit) as exc_info:
        _load_presets_or_exit(settings, _provider_registry_with_mlx())
    message = str(exc_info.value.code) if exc_info.value.code is not None else ""
    assert message.startswith(CONFIG_ERROR_PRESETS_UNSAFE_PERMISSIONS)


# ---------------------------------------------------------------------------
# Settings env-var inventory (T3) — TTS_DEFAULT_PRESET, TTS_PRESETS_FILE,
# TTS_SILENCE_TRIM_THRESHOLD_DB MUST be readable from os.environ.
# ---------------------------------------------------------------------------


def test_settings_reads_cycle2_env_vars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The new env vars survive ``Settings()`` construction and validation."""
    presets = tmp_path / "presets.json"
    _write_presets(presets, _valid_payload())
    monkeypatch.setenv("TTS_DEFAULT_PRESET", "fast")
    monkeypatch.setenv("TTS_PRESETS_FILE", str(presets))
    monkeypatch.setenv("TTS_SILENCE_TRIM_THRESHOLD_DB", "-45.5")
    settings = Settings()
    assert settings.tts_default_preset == "fast"
    assert settings.tts_presets_file == presets
    assert settings.tts_silence_trim_threshold_db == pytest.approx(-45.5)


def test_settings_silence_trim_threshold_rejects_non_numeric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-numeric ``TTS_SILENCE_TRIM_THRESHOLD_DB`` fails fast."""
    monkeypatch.setenv("TTS_SILENCE_TRIM_THRESHOLD_DB", "not-a-number")
    with pytest.raises(ValueError, match="TTS_SILENCE_TRIM_THRESHOLD_DB"):
        Settings()


# ---------------------------------------------------------------------------
# Sanity — even quality's pinned post-processing block survives a round-trip.
# ---------------------------------------------------------------------------


def test_quality_preset_has_flac_and_postprocess() -> None:
    """``quality`` defaults to FLAC with rms_normalize + silence_trim on."""
    registry = load_preset_registry(SHIPPED_PRESETS)
    quality = registry.get("quality")
    assert quality is not None
    assert quality.defaults.response_format == "flac"
    postproc = quality.defaults.postprocess
    assert postproc is not None
    assert postproc.rms_normalize is True
    assert postproc.silence_trim is True


def test_balanced_preset_matches_cycle1_defaults() -> None:
    """``balanced`` mirrors cycle-1 VoiceConfig defaults so OpenAI-path stays stable.

    A-PR-1 (revised): cross-cycle byte-identity is not promised, but
    operators rely on these defaults being the same as cycle-1 ``VoiceConfig``
    defaults out-of-the-box.
    """
    registry = load_preset_registry(SHIPPED_PRESETS)
    balanced = registry.get("balanced")
    assert balanced is not None
    assert balanced.defaults.temperature == pytest.approx(0.8)
    assert balanced.defaults.top_p == pytest.approx(0.95)
    assert balanced.defaults.max_sentences_per_chunk == 2
    assert balanced.defaults.normalize_db == pytest.approx(-20.0)
    assert balanced.defaults.response_format == "wav"


def test_check_presets_file_permissions_missing_file(tmp_path: Path) -> None:
    """A missing file surfaces a typed posture error, not a bare OSError."""
    with pytest.raises(PresetsUnsafePermissionsError):
        check_presets_file_permissions(tmp_path / "does_not_exist.json")


def test_load_preset_registry_missing_file(tmp_path: Path) -> None:
    """A missing presets file surfaces a typed invalid error."""
    with pytest.raises(PresetsInvalidError):
        load_preset_registry(tmp_path / "missing.json")


def test_load_preset_registry_invalid_json(tmp_path: Path) -> None:
    """Malformed JSON surfaces as ``PresetsInvalidError``."""
    presets = tmp_path / "presets.json"
    presets.write_text("{ not valid json", encoding="utf-8")
    os.chmod(presets, 0o644)
    with pytest.raises(PresetsInvalidError):
        load_preset_registry(presets)


def test_permission_check_excludes_only_other_write(tmp_path: Path) -> None:
    """Group-writable (0o664) is OK — only the world-writable bit triggers refusal."""
    presets = tmp_path / "presets.json"
    _write_presets(presets, _valid_payload())
    os.chmod(presets, 0o664)
    # Must not raise.
    check_presets_file_permissions(presets)
    # Sanity that the actual mode bits exclude S_IWOTH.
    mode = presets.stat().st_mode
    assert not (mode & stat.S_IWOTH)
