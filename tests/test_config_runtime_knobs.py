"""S-012 — runtime config knobs (FR-CF-01..03, UAT-CF-01..03).

These tests pin down the env-driven knob parsing and validation introduced
by Sprint 2's S-012 story. Separate file because ``tests/test_config.py``
covers the pre-Sprint-2 ``Settings`` surface and was already a long file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write_voice_map(tmp_path: Path) -> Path:
    voice_map_file = tmp_path / "voice_map.json"
    voice_map_file.write_text(
        json.dumps(
            {
                "alloy": {
                    "ref_audio_path": "/tmp/alloy.wav",
                    "ref_text": "hello",
                    "language": "Italian",
                }
            }
        ),
        encoding="utf-8",
    )
    return voice_map_file


# ----- defaults (UAT-CF-02) -------------------------------------------------


def test_defaults_are_safe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """UAT-CF-02: with no env, timeout is disabled and knobs use safe defaults."""
    from llm_tts_api.config import Settings

    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))

    settings = Settings()

    assert settings.tts_inference_timeout_seconds is None
    assert settings.tts_device == "auto"
    assert settings.tts_dtype == "auto"
    assert settings.tts_max_queue_depth == 8
    assert settings.tts_model_cache_size == 1
    assert settings.tts_preload_models == []
    assert settings.tts_shutdown_drain_seconds == 30
    assert settings.app_log_format == "text"


# ----- enum validation (UAT-CF-01) ------------------------------------------


@pytest.mark.parametrize(
    ("env_name", "bad_value"),
    [
        ("TTS_DEVICE", "tpu"),
        ("TTS_DTYPE", "float8"),
        ("APP_LOG_FORMAT", "yaml"),
    ],
)
def test_invalid_enum_raises_named(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    env_name: str,
    bad_value: str,
) -> None:
    """UAT-CF-01: invalid enum value → ValueError naming the env var."""
    from llm_tts_api.config import Settings

    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
    monkeypatch.setenv(env_name, bad_value)

    with pytest.raises(ValueError, match=env_name):
        Settings()


def test_empty_enum_value_uses_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Whitespace-only env (common from shell wrappers) → default, not crash."""
    from llm_tts_api.config import Settings

    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
    monkeypatch.setenv("TTS_DEVICE", "   ")

    settings = Settings()

    assert settings.tts_device == "auto"


def test_valid_enum_values_parsed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from llm_tts_api.config import Settings

    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
    monkeypatch.setenv("TTS_DEVICE", "MPS")  # case-insensitive
    monkeypatch.setenv("TTS_DTYPE", "bfloat16")
    monkeypatch.setenv("APP_LOG_FORMAT", "json")

    settings = Settings()

    assert settings.tts_device == "mps"
    assert settings.tts_dtype == "bfloat16"
    assert settings.app_log_format == "json"


# ----- integer validation (UAT-CF-01) ---------------------------------------


@pytest.mark.parametrize(
    ("env_name", "bad_value"),
    [
        ("TTS_MAX_QUEUE_DEPTH", "not-a-number"),
        ("TTS_MODEL_CACHE_SIZE", "0"),  # below minimum=1
        ("TTS_MODEL_CACHE_SIZE", "-3"),
        ("TTS_SHUTDOWN_DRAIN_SECONDS", "-1"),
        ("TTS_MAX_QUEUE_DEPTH", "-1"),
    ],
)
def test_invalid_int_raises_named(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    env_name: str,
    bad_value: str,
) -> None:
    """UAT-CF-01: invalid integer value → ValueError naming the env var."""
    from llm_tts_api.config import Settings

    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
    monkeypatch.setenv(env_name, bad_value)

    with pytest.raises(ValueError, match=env_name):
        Settings()


def test_valid_ints_parsed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from llm_tts_api.config import Settings

    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
    monkeypatch.setenv("TTS_MAX_QUEUE_DEPTH", "16")
    monkeypatch.setenv("TTS_MODEL_CACHE_SIZE", "3")
    monkeypatch.setenv("TTS_SHUTDOWN_DRAIN_SECONDS", "45")

    settings = Settings()

    assert settings.tts_max_queue_depth == 16
    assert settings.tts_model_cache_size == 3
    assert settings.tts_shutdown_drain_seconds == 45


# ----- inference timeout (UAT-CF-02 unset / UAT-CF-03 configured) -----------


def test_timeout_unset_means_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """UAT-CF-02: TTS_INFERENCE_TIMEOUT_SECONDS unset → no wait_for wrapper."""
    from llm_tts_api.config import Settings

    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))

    settings = Settings()

    assert settings.tts_inference_timeout_seconds is None


def test_timeout_empty_string_means_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from llm_tts_api.config import Settings

    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
    monkeypatch.setenv("TTS_INFERENCE_TIMEOUT_SECONDS", "   ")

    settings = Settings()

    assert settings.tts_inference_timeout_seconds is None


def test_timeout_positive_value_enabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """UAT-CF-03: positive timeout enables the wrapper at the synthesis path."""
    from llm_tts_api.config import Settings

    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
    monkeypatch.setenv("TTS_INFERENCE_TIMEOUT_SECONDS", "2")

    settings = Settings()

    assert settings.tts_inference_timeout_seconds == 2.0


def test_timeout_fractional_value_enabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from llm_tts_api.config import Settings

    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
    monkeypatch.setenv("TTS_INFERENCE_TIMEOUT_SECONDS", "1.5")

    settings = Settings()

    assert settings.tts_inference_timeout_seconds == 1.5


@pytest.mark.parametrize("bad_value", ["0", "-1", "-3.5", "not-a-number"])
def test_timeout_invalid_value_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bad_value: str
) -> None:
    """UAT-CF-01: zero / negative / non-numeric timeout → ValueError."""
    from llm_tts_api.config import Settings

    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
    monkeypatch.setenv("TTS_INFERENCE_TIMEOUT_SECONDS", bad_value)

    with pytest.raises(ValueError, match="TTS_INFERENCE_TIMEOUT_SECONDS"):
        Settings()


# ----- preload models -------------------------------------------------------


def test_preload_unset_is_empty_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from llm_tts_api.config import Settings

    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))

    settings = Settings()

    assert settings.tts_preload_models == []


def test_preload_single_entry_parsed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from llm_tts_api.config import PreloadEntry, Settings

    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
    monkeypatch.setenv("TTS_MLX_AUDIO_MODEL_ALLOWED", "modelA,modelB")
    monkeypatch.setenv("TTS_PRELOAD_MODELS", "mlx_audio:modelA")

    settings = Settings()

    assert settings.tts_preload_models == [PreloadEntry("mlx_audio", "modelA")]


def test_preload_multiple_entries_parsed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from llm_tts_api.config import PreloadEntry, Settings

    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
    monkeypatch.setenv("TTS_MLX_AUDIO_MODEL_ALLOWED", "m1,m2")
    monkeypatch.setenv("TTS_VOXTRAL_MODEL_ALLOWED", "vx1")
    monkeypatch.setenv("TTS_PRELOAD_MODELS", "mlx_audio:m1, voxtral:vx1 , mlx_audio:m2")

    settings = Settings()

    assert settings.tts_preload_models == [
        PreloadEntry("mlx_audio", "m1"),
        PreloadEntry("voxtral", "vx1"),
        PreloadEntry("mlx_audio", "m2"),
    ]


def test_preload_missing_colon_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from llm_tts_api.config import Settings

    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
    monkeypatch.setenv("TTS_PRELOAD_MODELS", "no-colon-here")

    with pytest.raises(ValueError, match="TTS_PRELOAD_MODELS"):
        Settings()


def test_preload_unknown_provider_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from llm_tts_api.config import Settings

    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
    monkeypatch.setenv("TTS_PRELOAD_MODELS", "nope:model")

    with pytest.raises(ValueError, match="nope"):
        Settings()


def test_preload_model_not_in_allow_list_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from llm_tts_api.config import Settings

    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
    monkeypatch.setenv("TTS_MLX_AUDIO_MODEL_ALLOWED", "modelA")
    monkeypatch.setenv("TTS_PRELOAD_MODELS", "mlx_audio:unknown-model")

    with pytest.raises(ValueError, match="allow-list"):
        Settings()
