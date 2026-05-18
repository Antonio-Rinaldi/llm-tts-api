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


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from llm_tts_api.config import Settings

    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))

    settings = Settings()

    assert settings.app_name == "llm-tts-api"
    assert settings.tts_model_default == "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
    assert settings.tts_model_allowed == ["Qwen/Qwen3-TTS-12Hz-0.6B-Base"]
    assert settings.tts_provider == "mlx_audio"


def test_settings_allowed_models_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from llm_tts_api.config import Settings

    monkeypatch.setenv("TTS_MLX_AUDIO_MODEL_DEFAULT", "m1")
    monkeypatch.setenv("TTS_MLX_AUDIO_MODEL_ALLOWED", "m1,m2,m3")
    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))

    settings = Settings()

    assert settings.tts_model_default == "m1"
    assert settings.tts_model_allowed == ["m1", "m2", "m3"]


def test_settings_voice_map_parsed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from llm_tts_api.config import Settings

    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))

    settings = Settings()

    assert "alloy" in settings.tts_voice_map
    assert settings.tts_voice_map["alloy"].ref_audio_path == "/tmp/alloy.wav"
    assert settings.tts_voice_map["alloy"].temperature == 0.8
    assert settings.tts_voice_map["alloy"].top_p == 0.95
    assert settings.tts_voice_map["alloy"].target_db == -20.0
    assert settings.tts_voice_map["alloy"].max_sentences_per_chunk == 2


def test_settings_voice_map_loaded_from_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from llm_tts_api.config import Settings

    voice_map_file = _write_voice_map(tmp_path)
    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(voice_map_file))

    settings = Settings()

    assert "alloy" in settings.tts_voice_map
    assert settings.tts_voice_map["alloy"].ref_audio_path == "/tmp/alloy.wav"


def test_settings_invalid_voice_map_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from llm_tts_api.config import Settings

    voice_map_file = tmp_path / "voice_map.json"
    voice_map_file.write_text("not-json", encoding="utf-8")
    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(voice_map_file))

    with pytest.raises(ValueError):
        Settings()


def test_settings_max_input_chars_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from llm_tts_api.config import Settings

    monkeypatch.setenv("TTS_MAX_INPUT_CHARS", "8192")
    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))

    settings = Settings()

    assert settings.tts_max_input_chars == 8192


def test_settings_provider_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from llm_tts_api.config import Settings

    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
    monkeypatch.setenv("TTS_PROVIDER", "mlx_audio")

    settings = Settings()

    assert settings.tts_provider == "mlx_audio"


def test_settings_voxtral_provider_specific_model_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from llm_tts_api.config import Settings

    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
    monkeypatch.setenv("TTS_PROVIDER", "voxtral")
    monkeypatch.setenv("TTS_VOXTRAL_MODEL_DEFAULT", "mlx-community/Voxtral-4B-TTS-2603-mlx-4bit")
    monkeypatch.setenv(
        "TTS_VOXTRAL_MODEL_ALLOWED",
        "mlx-community/Voxtral-4B-TTS-2603-mlx-4bit,mlx-community/Voxtral-Mini-4B-Realtime-2602-4bit",
    )

    settings = Settings()

    assert settings.tts_provider == "voxtral"
    assert settings.tts_model_default == "mlx-community/Voxtral-4B-TTS-2603-mlx-4bit"
    assert settings.tts_model_allowed == [
        "mlx-community/Voxtral-4B-TTS-2603-mlx-4bit",
        "mlx-community/Voxtral-Mini-4B-Realtime-2602-4bit",
    ]


def test_settings_vllm_omni_provider_specific_model_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from llm_tts_api.config import Settings

    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
    monkeypatch.setenv("TTS_PROVIDER", "vllm-omni")
    monkeypatch.setenv("TTS_VLLM_OMNI_MODEL_DEFAULT", "vllm-omni/default-tts")
    monkeypatch.setenv(
        "TTS_VLLM_OMNI_MODEL_ALLOWED",
        "vllm-omni/default-tts,vllm-omni/voice-clone-tts",
    )

    settings = Settings()

    assert settings.tts_provider == "vllm-omni"
    assert settings.tts_model_default == "vllm-omni/default-tts"
    assert settings.tts_model_allowed == ["vllm-omni/default-tts", "vllm-omni/voice-clone-tts"]


# ----- S-012 — runtime knobs (FR-CF-01..03, UAT-CF-01..03) ------------------


class TestRuntimeKnobDefaults:
    """Default values when no Sprint-2 env vars are set (UAT-CF-02)."""

    def test_defaults_are_safe(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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


class TestRuntimeKnobEnumValidation:
    """UAT-CF-01: invalid enum-style values exit with named-var message."""

    @pytest.mark.parametrize(
        ("env_name", "bad_value"),
        [
            ("TTS_DEVICE", "tpu"),
            ("TTS_DTYPE", "float8"),
            ("APP_LOG_FORMAT", "yaml"),
        ],
    )
    def test_invalid_enum_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        env_name: str,
        bad_value: str,
    ) -> None:
        from llm_tts_api.config import Settings

        monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
        monkeypatch.setenv(env_name, bad_value)

        with pytest.raises(ValueError, match=env_name):
            Settings()

    def test_empty_value_uses_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from llm_tts_api.config import Settings

        monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
        monkeypatch.setenv("TTS_DEVICE", "   ")

        settings = Settings()

        assert settings.tts_device == "auto"

    def test_valid_values_parsed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        from llm_tts_api.config import Settings

        monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
        monkeypatch.setenv("TTS_DEVICE", "MPS")
        monkeypatch.setenv("TTS_DTYPE", "bfloat16")
        monkeypatch.setenv("APP_LOG_FORMAT", "json")

        settings = Settings()

        assert settings.tts_device == "mps"
        assert settings.tts_dtype == "bfloat16"
        assert settings.app_log_format == "json"


class TestRuntimeKnobIntegerValidation:
    """UAT-CF-01: invalid integer values exit with named-var message."""

    @pytest.mark.parametrize(
        ("env_name", "bad_value"),
        [
            ("TTS_MAX_QUEUE_DEPTH", "not-a-number"),
            ("TTS_MODEL_CACHE_SIZE", "0"),
            ("TTS_MODEL_CACHE_SIZE", "-3"),
            ("TTS_SHUTDOWN_DRAIN_SECONDS", "-1"),
            ("TTS_MAX_QUEUE_DEPTH", "-1"),
        ],
    )
    def test_invalid_int_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        env_name: str,
        bad_value: str,
    ) -> None:
        from llm_tts_api.config import Settings

        monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
        monkeypatch.setenv(env_name, bad_value)

        with pytest.raises(ValueError, match=env_name):
            Settings()

    def test_valid_ints_parsed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        from llm_tts_api.config import Settings

        monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
        monkeypatch.setenv("TTS_MAX_QUEUE_DEPTH", "16")
        monkeypatch.setenv("TTS_MODEL_CACHE_SIZE", "3")
        monkeypatch.setenv("TTS_SHUTDOWN_DRAIN_SECONDS", "45")

        settings = Settings()

        assert settings.tts_max_queue_depth == 16
        assert settings.tts_model_cache_size == 3
        assert settings.tts_shutdown_drain_seconds == 45


class TestInferenceTimeout:
    """UAT-CF-03 + UAT-CF-02: TTS_INFERENCE_TIMEOUT_SECONDS opt-in semantics.

    The actual ``asyncio.wait_for`` wrapper lives in S-007's synthesis
    path; these tests pin down the parsing contract S-007 will consume.
    """

    def test_unset_means_disabled(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        from llm_tts_api.config import Settings

        monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))

        settings = Settings()

        assert settings.tts_inference_timeout_seconds is None

    def test_empty_string_means_disabled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from llm_tts_api.config import Settings

        monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
        monkeypatch.setenv("TTS_INFERENCE_TIMEOUT_SECONDS", "   ")

        settings = Settings()

        assert settings.tts_inference_timeout_seconds is None

    def test_positive_value_enabled(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        from llm_tts_api.config import Settings

        monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
        monkeypatch.setenv("TTS_INFERENCE_TIMEOUT_SECONDS", "2")

        settings = Settings()

        assert settings.tts_inference_timeout_seconds == 2.0

    def test_fractional_value_enabled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from llm_tts_api.config import Settings

        monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
        monkeypatch.setenv("TTS_INFERENCE_TIMEOUT_SECONDS", "1.5")

        settings = Settings()

        assert settings.tts_inference_timeout_seconds == 1.5

    @pytest.mark.parametrize("bad_value", ["0", "-1", "-3.5", "not-a-number"])
    def test_invalid_value_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bad_value: str
    ) -> None:
        from llm_tts_api.config import Settings

        monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
        monkeypatch.setenv("TTS_INFERENCE_TIMEOUT_SECONDS", bad_value)

        with pytest.raises(ValueError, match="TTS_INFERENCE_TIMEOUT_SECONDS"):
            Settings()


class TestPreloadModels:
    """``TTS_PRELOAD_MODELS`` parse + validate (S-008 producer config)."""

    def test_unset_is_empty_list(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        from llm_tts_api.config import Settings

        monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))

        settings = Settings()

        assert settings.tts_preload_models == []

    def test_single_entry_parsed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        from llm_tts_api.config import PreloadEntry, Settings

        monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
        monkeypatch.setenv("TTS_MLX_AUDIO_MODEL_ALLOWED", "modelA,modelB")
        monkeypatch.setenv("TTS_PRELOAD_MODELS", "mlx_audio:modelA")

        settings = Settings()

        assert settings.tts_preload_models == [PreloadEntry("mlx_audio", "modelA")]

    def test_multiple_entries_parsed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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

    def test_missing_colon_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        from llm_tts_api.config import Settings

        monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
        monkeypatch.setenv("TTS_PRELOAD_MODELS", "no-colon-here")

        with pytest.raises(ValueError, match="TTS_PRELOAD_MODELS"):
            Settings()

    def test_unknown_provider_raises(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        from llm_tts_api.config import Settings

        monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
        monkeypatch.setenv("TTS_PRELOAD_MODELS", "nope:model")

        with pytest.raises(ValueError, match="nope"):
            Settings()

    def test_model_not_in_allow_list_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from llm_tts_api.config import Settings

        monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(_write_voice_map(tmp_path)))
        monkeypatch.setenv("TTS_MLX_AUDIO_MODEL_ALLOWED", "modelA")
        monkeypatch.setenv("TTS_PRELOAD_MODELS", "mlx_audio:unknown-model")

        with pytest.raises(ValueError, match="allow-list"):
            Settings()
