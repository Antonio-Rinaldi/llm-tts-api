from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class VoiceConfig:
    """Per-voice configuration used by synthesis providers.

    Attributes:
        ref_audio_path: Path to the reference audio file for cloning.
        ref_text: Optional transcript aligned with ``ref_audio_path``.
        language: Human-readable language used by TTS providers.
        number_lang: Optional language override used for number/date expansion.
        temperature: Sampling temperature for generation.
        top_p: Nucleus sampling value.
        target_db: Post-processing RMS target in dBFS.
        max_sentences_per_chunk: Maximum sentences grouped in one synthesis chunk.
    """

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
    """Runtime application settings loaded from environment variables.

    The class is intentionally strict: invalid values fail fast during startup,
    so runtime requests do not discover misconfiguration late.
    """

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
    tts_max_concurrent_requests: int = 1
    tts_max_queue_depth: int = 8

    def __post_init__(self) -> None:
        """Load all settings from environment and validate their values."""
        self._load_app_identity()
        self._load_provider_models()
        self._load_stt_models()
        self._load_tts_limits()
        self.tts_voice_map = self._load_voice_map_from_file()

    @staticmethod
    def _split_csv(raw: str) -> list[str]:
        """Split a comma-separated env value into trimmed non-empty strings."""
        return [item.strip() for item in raw.split(",") if item.strip()]

    def _load_app_identity(self) -> None:
        """Read app-level metadata from environment variables."""
        self.app_name = os.getenv("APP_NAME", self.app_name)
        self.app_env = os.getenv("APP_ENV", self.app_env)
        self.app_log_level = os.getenv("APP_LOG_LEVEL", self.app_log_level)

    def _load_provider_models(self) -> None:
        """Load provider choice and provider-specific model allow-lists."""
        provider_env = os.getenv("TTS_PROVIDER", self.tts_provider)
        self.tts_provider = (provider_env or "mlx_audio").strip().lower()
        if self.tts_provider not in {"mlx_audio", "voxtral", "vllm-omni"}:
            raise ValueError("TTS_PROVIDER must be one of 'mlx_audio', 'voxtral', or 'vllm-omni'")

        mlx_default, mlx_allowed = self._load_provider_model_list(
            default_env="TTS_MLX_AUDIO_MODEL_DEFAULT",
            allowed_env="TTS_MLX_AUDIO_MODEL_ALLOWED",
            fallback_default=self.tts_mlx_audio_model_default,
        )
        self.tts_mlx_audio_model_default = mlx_default
        self.tts_mlx_audio_model_allowed = mlx_allowed

        voxtral_default, voxtral_allowed = self._load_provider_model_list(
            default_env="TTS_VOXTRAL_MODEL_DEFAULT",
            allowed_env="TTS_VOXTRAL_MODEL_ALLOWED",
            fallback_default=self.tts_voxtral_model_default,
        )
        self.tts_voxtral_model_default, self.tts_voxtral_model_allowed = (
            voxtral_default,
            voxtral_allowed,
        )

        vllm_default, vllm_allowed = self._load_provider_model_list(
            default_env="TTS_VLLM_OMNI_MODEL_DEFAULT",
            allowed_env="TTS_VLLM_OMNI_MODEL_ALLOWED",
            fallback_default=self.tts_vllm_omni_model_default,
        )
        self.tts_vllm_omni_model_default = vllm_default
        self.tts_vllm_omni_model_allowed = vllm_allowed

        self.tts_model_default = self.tts_model_default_for_provider(self.tts_provider)
        self.tts_model_allowed = self.tts_model_allowed_for_provider(self.tts_provider)

    def _load_provider_model_list(
        self,
        *,
        default_env: str,
        allowed_env: str,
        fallback_default: str,
    ) -> tuple[str, list[str]]:
        """Resolve model default and allow-list for one provider namespace."""
        model_default = os.getenv(default_env, fallback_default).strip()
        raw_allowed = os.getenv(allowed_env, "").strip()
        allowed_models = self._split_csv(raw_allowed) if raw_allowed else [model_default]
        normalized_allowed = (
            allowed_models if model_default in allowed_models else [model_default, *allowed_models]
        )
        return model_default, normalized_allowed

    def _load_stt_models(self) -> None:
        """Resolve STT model default and allowed list."""
        self.stt_model_default = os.getenv("STT_MODEL_DEFAULT", self.stt_model_default)
        stt_allowed = os.getenv("STT_MODEL_ALLOWED", "").strip()
        self.stt_model_allowed = (
            self._split_csv(stt_allowed) if stt_allowed else [self.stt_model_default]
        )

    def _load_tts_limits(self) -> None:
        """Validate TTS input size limits and concurrency cap."""
        max_chars_raw = os.getenv("TTS_MAX_INPUT_CHARS", str(self.tts_max_input_chars)).strip()
        try:
            self.tts_max_input_chars = int(max_chars_raw)
        except ValueError as exc:
            raise ValueError("TTS_MAX_INPUT_CHARS must be an integer") from exc
        if self.tts_max_input_chars < 256:
            raise ValueError("TTS_MAX_INPUT_CHARS must be >= 256")

        max_req_raw = os.getenv("TTS_MAX_CONCURRENT_REQUESTS", "1").strip()
        try:
            self.tts_max_concurrent_requests = max(1, int(max_req_raw))
        except ValueError as exc:
            raise ValueError("TTS_MAX_CONCURRENT_REQUESTS must be an integer >= 1") from exc

        max_queue_raw = os.getenv("TTS_MAX_QUEUE_DEPTH", str(self.tts_max_queue_depth)).strip()
        try:
            queue_depth = int(max_queue_raw)
        except ValueError as exc:
            raise ValueError("TTS_MAX_QUEUE_DEPTH must be an integer >= 1") from exc
        if queue_depth < 1:
            raise ValueError("TTS_MAX_QUEUE_DEPTH must be an integer >= 1")
        self.tts_max_queue_depth = queue_depth

    def _load_voice_map_from_file(self) -> dict[str, VoiceConfig]:
        """Load and validate all configured voices from ``TTS_VOICE_MAP_FILE``."""
        voice_map_path = self._resolve_voice_map_path()
        try:
            raw_voice_map = json.loads(voice_map_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("TTS_VOICE_MAP_FILE must contain valid JSON") from exc

        if not isinstance(raw_voice_map, dict):
            raise ValueError("Voice map must be a JSON object")
        return {
            voice_name: self._parse_voice_entry(voice_name, voice_config)
            for voice_name, voice_config in raw_voice_map.items()
        }

    @staticmethod
    def _resolve_voice_map_path() -> Path:
        """Resolve and validate path to the JSON voice map file."""
        voice_map_file = os.getenv("TTS_VOICE_MAP_FILE", "").strip()
        if not voice_map_file:
            raise ValueError("TTS_VOICE_MAP_FILE env must be defined")

        voice_map_path = Path(voice_map_file)
        if not voice_map_path.exists() or not voice_map_path.is_file():
            raise ValueError("TTS_VOICE_MAP_FILE must point to an existing JSON file")
        return voice_map_path

    @staticmethod
    def _parse_voice_entry(voice_name: str, voice_data: object) -> VoiceConfig:
        """Parse and validate one voice entry from the voice map JSON."""
        if not isinstance(voice_data, dict):
            raise ValueError(f"Voice config for '{voice_name}' must be an object")

        ref_audio_path = voice_data.get("ref_audio_path")
        ref_text = voice_data.get("ref_text", "")
        language = voice_data.get("language")
        number_lang = voice_data.get("number_lang", "")
        temperature = voice_data.get("temperature", 0.8)
        top_p = voice_data.get("top_p", 0.95)
        target_db = voice_data.get("target_db", -20.0)
        max_sentences_per_chunk = voice_data.get("max_sentences_per_chunk", 2)

        if not isinstance(ref_audio_path, str):
            raise ValueError(f"Voice '{voice_name}' requires string 'ref_audio_path'")
        if not isinstance(language, str) or not language.strip():
            raise ValueError(f"Voice '{voice_name}' requires non-empty 'language'")
        if not isinstance(ref_text, str):
            raise ValueError(f"Voice '{voice_name}' requires string 'ref_text'")
        if not isinstance(number_lang, str):
            raise ValueError(f"Voice '{voice_name}' requires string 'number_lang'")
        if not isinstance(temperature, (int, float)):
            raise ValueError(f"Voice '{voice_name}' requires numeric 'temperature'")
        if not isinstance(top_p, (int, float)):
            raise ValueError(f"Voice '{voice_name}' requires numeric 'top_p'")
        if not isinstance(target_db, (int, float)):
            raise ValueError(f"Voice '{voice_name}' requires numeric 'target_db'")
        if not isinstance(max_sentences_per_chunk, int):
            raise ValueError(f"Voice '{voice_name}' requires integer 'max_sentences_per_chunk'")
        if max_sentences_per_chunk < 1:
            raise ValueError(f"Voice '{voice_name}' requires 'max_sentences_per_chunk' >= 1")
        if not 0.0 <= float(temperature) <= 2.0:
            raise ValueError(f"Voice '{voice_name}' requires 'temperature' between 0.0 and 2.0")
        if not 0.0 < float(top_p) <= 1.0:
            raise ValueError(f"Voice '{voice_name}' requires 'top_p' between 0.0 and 1.0")

        return VoiceConfig(
            ref_audio_path=ref_audio_path,
            ref_text=ref_text,
            language=language,
            number_lang=number_lang,
            temperature=float(temperature),
            top_p=float(top_p),
            target_db=float(target_db),
            max_sentences_per_chunk=max_sentences_per_chunk,
        )

    def tts_model_default_for_provider(self, provider: str) -> str:
        """Return the default TTS model for the selected provider."""
        if provider == "vllm-omni":
            return self.tts_vllm_omni_model_default
        if provider == "voxtral":
            return self.tts_voxtral_model_default
        return self.tts_mlx_audio_model_default

    def tts_model_allowed_for_provider(self, provider: str) -> list[str]:
        """Return allow-listed TTS models for the selected provider."""
        if provider == "vllm-omni":
            return list(self.tts_vllm_omni_model_allowed)
        if provider == "voxtral":
            return list(self.tts_voxtral_model_allowed)
        return list(self.tts_mlx_audio_model_allowed)
