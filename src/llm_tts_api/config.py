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
    number_lang: str = ""
    temperature: float = 0.8
    top_p: float = 0.95
    target_db: float = -20.0
    max_sentences_per_chunk: int = 2


@dataclass(slots=True)
class Settings:
    app_name: str = "llm-tts-api"
    app_env: str = "development"
    app_log_level: str = "INFO"

    tts_model_default: str = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
    tts_model_allowed: list[str] = field(default_factory=list)
    tts_provider: str = "mlx_audio"
    tts_mlx_audio_model_default: str = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
    tts_mlx_audio_model_allowed: list[str] = field(default_factory=list)
    tts_voxtral_model_default: str = "mlx-community/Voxtral-4B-TTS-2603-mlx-4bit"
    tts_voxtral_model_allowed: list[str] = field(default_factory=list)
    tts_vllm_omni_model_default: str = "vllm-omni/default-tts"
    tts_vllm_omni_model_allowed: list[str] = field(default_factory=list)

    stt_model_default: str = "whisper-1"
    stt_model_allowed: list[str] = field(default_factory=list)

    tts_voice_map: dict[str, VoiceConfig] = field(default_factory=dict)
    tts_max_input_chars: int = 4096

    def __post_init__(self) -> None:
        self.app_name = os.getenv("APP_NAME", self.app_name)
        self.app_env = os.getenv("APP_ENV", self.app_env)
        self.app_log_level = os.getenv("APP_LOG_LEVEL", self.app_log_level)

        provider_env = os.getenv("TTS_PROVIDER", self.tts_provider)
        self.tts_provider = (provider_env or "mlx_audio").strip().lower()
        if self.tts_provider not in {"mlx_audio", "voxtral", "vllm-omni"}:
            raise ValueError("TTS_PROVIDER must be one of 'mlx_audio', 'voxtral', or 'vllm-omni'")

        self.tts_mlx_audio_model_default = os.getenv(
            "TTS_MLX_AUDIO_MODEL_DEFAULT",
            self.tts_mlx_audio_model_default,
        ).strip()
        mlx_allowed_raw = os.getenv("TTS_MLX_AUDIO_MODEL_ALLOWED", "").strip()
        if mlx_allowed_raw:
            self.tts_mlx_audio_model_allowed = [item.strip() for item in mlx_allowed_raw.split(",") if item.strip()]
        else:
            self.tts_mlx_audio_model_allowed = [self.tts_mlx_audio_model_default]
        if self.tts_mlx_audio_model_default not in self.tts_mlx_audio_model_allowed:
            self.tts_mlx_audio_model_allowed.insert(0, self.tts_mlx_audio_model_default)

        self.tts_voxtral_model_default = os.getenv(
            "TTS_VOXTRAL_MODEL_DEFAULT",
            self.tts_voxtral_model_default,
        ).strip()
        voxtral_allowed_raw = os.getenv("TTS_VOXTRAL_MODEL_ALLOWED", "").strip()
        if voxtral_allowed_raw:
            self.tts_voxtral_model_allowed = [item.strip() for item in voxtral_allowed_raw.split(",") if item.strip()]
        else:
            self.tts_voxtral_model_allowed = [self.tts_voxtral_model_default]
        if self.tts_voxtral_model_default not in self.tts_voxtral_model_allowed:
            self.tts_voxtral_model_allowed.insert(0, self.tts_voxtral_model_default)

        self.tts_vllm_omni_model_default = os.getenv(
            "TTS_VLLM_OMNI_MODEL_DEFAULT",
            self.tts_vllm_omni_model_default,
        ).strip()
        vllm_omni_allowed_raw = os.getenv("TTS_VLLM_OMNI_MODEL_ALLOWED", "").strip()
        if vllm_omni_allowed_raw:
            self.tts_vllm_omni_model_allowed = [
                item.strip() for item in vllm_omni_allowed_raw.split(",") if item.strip()
            ]
        else:
            self.tts_vllm_omni_model_allowed = [self.tts_vllm_omni_model_default]
        if self.tts_vllm_omni_model_default not in self.tts_vllm_omni_model_allowed:
            self.tts_vllm_omni_model_allowed.insert(0, self.tts_vllm_omni_model_default)

        self.tts_model_default = self.tts_model_default_for_provider(self.tts_provider)
        self.tts_model_allowed = self.tts_model_allowed_for_provider(self.tts_provider)

        self.stt_model_default = os.getenv("STT_MODEL_DEFAULT", self.stt_model_default)
        stt_allowed = os.getenv("STT_MODEL_ALLOWED", "").strip()
        if stt_allowed:
            self.stt_model_allowed = [item.strip() for item in stt_allowed.split(",") if item.strip()]
        else:
            self.stt_model_allowed = [self.stt_model_default]

        max_chars_raw = os.getenv("TTS_MAX_INPUT_CHARS", str(self.tts_max_input_chars)).strip()
        try:
            self.tts_max_input_chars = int(max_chars_raw)
        except ValueError as exc:
            raise ValueError("TTS_MAX_INPUT_CHARS must be an integer") from exc
        if self.tts_max_input_chars < 256:
            raise ValueError("TTS_MAX_INPUT_CHARS must be >= 256")


        voice_map_file = os.getenv("TTS_VOICE_MAP_FILE", "").strip()
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
            number_lang = cfg.get("number_lang", "")
            temperature = cfg.get("temperature", 0.8)
            top_p = cfg.get("top_p", 0.95)
            target_db = cfg.get("target_db", -20.0)
            max_sentences_per_chunk = cfg.get("max_sentences_per_chunk", 2)

            if not isinstance(ref_audio_path, str):
                raise ValueError(f"Voice '{voice}' requires string 'ref_audio_path'")
            if not isinstance(language, str) or not language.strip():
                raise ValueError(f"Voice '{voice}' requires non-empty 'language'")
            if not isinstance(ref_text, str):
                raise ValueError(f"Voice '{voice}' requires string 'ref_text'")
            if not isinstance(number_lang, str):
                raise ValueError(f"Voice '{voice}' requires string 'number_lang'")
            if not isinstance(temperature, (int, float)):
                raise ValueError(f"Voice '{voice}' requires numeric 'temperature'")
            if not isinstance(top_p, (int, float)):
                raise ValueError(f"Voice '{voice}' requires numeric 'top_p'")
            if not isinstance(target_db, (int, float)):
                raise ValueError(f"Voice '{voice}' requires numeric 'target_db'")
            if not isinstance(max_sentences_per_chunk, int):
                raise ValueError(f"Voice '{voice}' requires integer 'max_sentences_per_chunk'")
            if max_sentences_per_chunk < 1:
                raise ValueError(f"Voice '{voice}' requires 'max_sentences_per_chunk' >= 1")
            if not 0.0 <= float(temperature) <= 2.0:
                raise ValueError(f"Voice '{voice}' requires 'temperature' between 0.0 and 2.0")
            if not 0.0 < float(top_p) <= 1.0:
                raise ValueError(f"Voice '{voice}' requires 'top_p' between 0.0 and 1.0")

            parsed_map[voice] = VoiceConfig(
                ref_audio_path=ref_audio_path,
                ref_text=ref_text,
                language=language,
                number_lang=number_lang,
                temperature=float(temperature),
                top_p=float(top_p),
                target_db=float(target_db),
                max_sentences_per_chunk=max_sentences_per_chunk,
            )

        self.tts_voice_map = parsed_map

    def tts_model_default_for_provider(self, provider: str) -> str:
        if provider == "vllm-omni":
            return self.tts_vllm_omni_model_default
        if provider == "voxtral":
            return self.tts_voxtral_model_default
        return self.tts_mlx_audio_model_default

    def tts_model_allowed_for_provider(self, provider: str) -> list[str]:
        if provider == "vllm-omni":
            return list(self.tts_vllm_omni_model_allowed)
        if provider == "voxtral":
            return list(self.tts_voxtral_model_allowed)
        return list(self.tts_mlx_audio_model_allowed)

