from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class VoiceConfig:
    ref_audio_path: str
    ref_text: str
    language: str


@dataclass(slots=True)
class Settings:
    app_name: str = "llm-tts-api"
    app_env: str = "development"
    app_log_level: str = "INFO"

    tts_model_default: str = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
    tts_model_allowed: list[str] = field(default_factory=list)
    tts_default_provider: str = "qwen"
    tts_provider_model_prefixes: dict[str, list[str]] = field(
        default_factory=lambda: {
            "voxtral": ["voxtral/", "mistral/", "mistralai/"],
            "qwen": ["qwen/"],
        }
    )

    stt_model_default: str = "whisper-1"
    stt_model_allowed: list[str] = field(default_factory=list)

    tts_voice_map: dict[str, VoiceConfig] = field(default_factory=dict)
    tts_max_input_chars: int = 4096

    def __post_init__(self) -> None:
        self.app_name = os.getenv("APP_NAME", self.app_name)
        self.app_env = os.getenv("APP_ENV", self.app_env)
        self.app_log_level = os.getenv("APP_LOG_LEVEL", self.app_log_level)

        self.tts_default_provider = os.getenv("TTS_DEFAULT_PROVIDER", self.tts_default_provider).strip().lower()
        if self.tts_default_provider not in {"qwen", "voxtral"}:
            raise ValueError("TTS_DEFAULT_PROVIDER must be either 'qwen' or 'voxtral'")

        self.tts_model_default = os.getenv(
            "TTS_MODEL_DEFAULT",
            os.getenv("QWEN_TTS_MODEL_DEFAULT", self.tts_model_default),
        )

        tts_allowed = os.getenv("TTS_MODEL_ALLOWED", os.getenv("QWEN_TTS_MODEL_ALLOWED", "")).strip()
        if tts_allowed:
            self.tts_model_allowed = [item.strip() for item in tts_allowed.split(",") if item.strip()]
        else:
            self.tts_model_allowed = [self.tts_model_default]

        if self.tts_model_default not in self.tts_model_allowed:
            self.tts_model_allowed.insert(0, self.tts_model_default)

        self.stt_model_default = os.getenv("STT_MODEL_DEFAULT", os.getenv("QWEN_STT_MODEL_DEFAULT", self.stt_model_default))
        stt_allowed = os.getenv("STT_MODEL_ALLOWED", os.getenv("QWEN_STT_MODEL_ALLOWED", "")).strip()
        if stt_allowed:
            self.stt_model_allowed = [item.strip() for item in stt_allowed.split(",") if item.strip()]
        else:
            self.stt_model_allowed = [self.stt_model_default]

        max_chars_raw = os.getenv("TTS_MAX_INPUT_CHARS", os.getenv("QWEN_TTS_MAX_INPUT_CHARS", str(self.tts_max_input_chars))).strip()
        try:
            self.tts_max_input_chars = int(max_chars_raw)
        except ValueError as exc:
            raise ValueError("TTS_MAX_INPUT_CHARS must be an integer") from exc
        if self.tts_max_input_chars < 256:
            raise ValueError("TTS_MAX_INPUT_CHARS must be >= 256")


        provider_prefixes_raw = os.getenv("TTS_PROVIDER_MODEL_PREFIXES", "").strip()
        if provider_prefixes_raw:
            try:
                parsed_prefixes = json.loads(provider_prefixes_raw)
            except json.JSONDecodeError as exc:
                raise ValueError("TTS_PROVIDER_MODEL_PREFIXES must be valid JSON") from exc
            if not isinstance(parsed_prefixes, dict):
                raise ValueError("TTS_PROVIDER_MODEL_PREFIXES must be a JSON object")

            normalized: dict[str, list[str]] = {}
            for provider_name, prefixes in parsed_prefixes.items():
                if not isinstance(provider_name, str) or not isinstance(prefixes, list):
                    raise ValueError("TTS_PROVIDER_MODEL_PREFIXES entries must be provider -> list[str]")
                normalized[provider_name.strip().lower()] = [
                    str(prefix).strip().lower() for prefix in prefixes if str(prefix).strip()
                ]
            self.tts_provider_model_prefixes = normalized

        voice_map_file = os.getenv("TTS_VOICE_MAP_FILE", os.getenv("QWEN_TTS_VOICE_MAP_FILE", "")).strip()
        if not voice_map_file or voice_map_file == "":
            raise ValueError("TTS_VOICE_MAP_FILE env must be defined")

        voice_map_path = Path(voice_map_file)
        if not voice_map_path.exists() or not voice_map_path.is_file():
            raise ValueError("TTS_VOICE_MAP_FILE must point to an existing JSON file")

        try:
            raw_voice_map = json.loads(voice_map_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("TTS_VOICE_MAP_FILE must contain valid JSON") from exc

        if not isinstance(raw_voice_map, dict):
            raise ValueError("Voice map must be a JSON object")

        parsed_map: dict[str, VoiceConfig] = {}
        for voice, cfg in raw_voice_map.items():
            if not isinstance(cfg, dict):
                raise ValueError(f"Voice config for '{voice}' must be an object")

            ref_audio_path = cfg.get("ref_audio_path")
            ref_text = cfg.get("ref_text", "")
            language = cfg.get("language")

            if not isinstance(ref_audio_path, str) or not ref_audio_path.strip():
                raise ValueError(f"Voice '{voice}' requires non-empty 'ref_audio_path'")
            if not isinstance(language, str) or not language.strip():
                raise ValueError(f"Voice '{voice}' requires non-empty 'language'")
            if not isinstance(ref_text, str):
                raise ValueError(f"Voice '{voice}' requires string 'ref_text'")

            parsed_map[voice] = VoiceConfig(
                ref_audio_path=ref_audio_path,
                ref_text=ref_text,
                language=language,
            )

        self.tts_voice_map = parsed_map

    # Backward-compatible aliases for existing internal callers/tests.
    @property
    def qwen_tts_model_default(self) -> str:
        return self.tts_model_default

    @property
    def qwen_tts_model_allowed(self) -> list[str]:
        return self.tts_model_allowed

    @property
    def qwen_stt_model_default(self) -> str:
        return self.stt_model_default

    @property
    def qwen_stt_model_allowed(self) -> list[str]:
        return self.stt_model_allowed

    @property
    def qwen_tts_voice_map(self) -> dict[str, VoiceConfig]:
        return self.tts_voice_map

    @property
    def qwen_tts_max_input_chars(self) -> int:
        return self.tts_max_input_chars
