from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class VoiceConfig:
    ref_audio_path: str
    ref_text: str
    language: str


@dataclass(slots=True)
class Settings:
    app_name: str = "qwen-tts-api"
    app_env: str = "development"
    app_log_level: str = "INFO"

    qwen_tts_model_default: str = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
    qwen_tts_model_allowed: list[str] = None  # type: ignore[assignment]

    qwen_stt_model_default: str = "whisper-1"
    qwen_stt_model_allowed: list[str] = None  # type: ignore[assignment]

    qwen_tts_voice_map: dict[str, VoiceConfig] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.app_name = os.getenv("APP_NAME", self.app_name)
        self.app_env = os.getenv("APP_ENV", self.app_env)
        self.app_log_level = os.getenv("APP_LOG_LEVEL", self.app_log_level)

        self.qwen_tts_model_default = os.getenv("QWEN_TTS_MODEL_DEFAULT", self.qwen_tts_model_default)

        tts_allowed = os.getenv("QWEN_TTS_MODEL_ALLOWED", "").strip()
        if tts_allowed:
            self.qwen_tts_model_allowed = [item.strip() for item in tts_allowed.split(",") if item.strip()]
        else:
            self.qwen_tts_model_allowed = [self.qwen_tts_model_default]

        self.qwen_stt_model_default = os.getenv("QWEN_STT_MODEL_DEFAULT", self.qwen_stt_model_default)
        stt_allowed = os.getenv("QWEN_STT_MODEL_ALLOWED", "").strip()
        if stt_allowed:
            self.qwen_stt_model_allowed = [item.strip() for item in stt_allowed.split(",") if item.strip()]
        else:
            self.qwen_stt_model_allowed = [self.qwen_stt_model_default]

        voice_map_file = os.getenv("QWEN_TTS_VOICE_MAP_FILE", "").strip()

        if voice_map_file:
            voice_map_path = Path(voice_map_file)
            if not voice_map_path.exists() or not voice_map_path.is_file():
                raise ValueError("QWEN_TTS_VOICE_MAP_FILE must point to an existing JSON file")
            try:
                raw_voice_map = json.loads(voice_map_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError("QWEN_TTS_VOICE_MAP_FILE must contain valid JSON") from exc
        else:
            voice_map_json = os.getenv("QWEN_TTS_VOICE_MAP_JSON", "{}").strip() or "{}"
            try:
                raw_voice_map = json.loads(voice_map_json)
            except json.JSONDecodeError as exc:
                raise ValueError("QWEN_TTS_VOICE_MAP_JSON must be valid JSON") from exc

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

        self.qwen_tts_voice_map = parsed_map
