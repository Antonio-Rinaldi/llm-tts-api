from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

_VALID_DEVICES: frozenset[str] = frozenset({"auto", "mps", "cuda", "cpu"})
_VALID_DTYPES: frozenset[str] = frozenset({"auto", "float16", "bfloat16", "float32"})
_VALID_LOG_FORMATS: frozenset[str] = frozenset({"text", "json"})
_VALID_VOICE_METADATA_BACKENDS: frozenset[str] = frozenset({"fs_json", "postgres"})
# S-024 — voice blob backend selectors. The default is ``fs`` (the
# FsBlobRepository introduced in S-022); ``s3`` flips the wiring in
# ``dependencies.build_default_dependencies`` to an S3BlobRepository
# (requires the ``[s3]`` optional extra).
_VALID_VOICE_BLOB_BACKENDS: frozenset[str] = frozenset({"fs", "s3"})


@dataclass(frozen=True, slots=True)
class PreloadEntry:
    """One ``provider:model`` pair from ``TTS_PRELOAD_MODELS``."""

    provider: str
    model: str


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

    # S-012 — runtime knobs introduced for Sprint 2 stories. Parsing /
    # validation lives in ``_load_runtime_knobs`` below; defaults here are
    # the conservative single-process values used when env is unset.
    tts_device: str = "auto"
    tts_dtype: str = "auto"
    tts_max_queue_depth: int = 8
    tts_model_cache_size: int = 1
    tts_preload_models: list[PreloadEntry] = field(default_factory=list)
    tts_inference_timeout_seconds: float | None = None
    tts_shutdown_drain_seconds: int = 30
    # S-010 — FR-HL-05: psutil-based low-memory soft warning floor in GiB.
    # ``0`` disables the check (the floor is itself non-positive).
    tts_min_free_memory_gb: int = 4
    app_log_format: str = "text"

    # S-022 — root directory for the FS-default voice store. Both
    # ``FsJsonMetadataRepository`` (single ``metadata.json``) and
    # ``FsBlobRepository`` (``blobs/<id>.wav``) live underneath it.
    tts_voice_store_dir: Path = field(default_factory=lambda: Path("var/voices"))

    # S-023 — optional Postgres metadata backend behind the ``[postgres]``
    # extra. ``fs_json`` keeps the default deploy zero-external-services;
    # ``postgres`` requires ``TTS_VOICE_METADATA_DSN`` and the extra.
    tts_voice_metadata_backend: str = "fs_json"
    tts_voice_metadata_dsn: str | None = None
    # S-024 — voice blob backend selector. ``fs`` (default) keeps the
    # zero-dependency FsBlobRepository under ``tts_voice_store_dir``;
    # ``s3`` swaps in :class:`S3BlobRepository` (requires the ``[s3]``
    # extra) and reads the three ``TTS_VOICE_BLOB_S3_*`` vars below.
    tts_voice_blob_backend: str = "fs"
    # S3 endpoint URL — leave empty for AWS S3, or set to a MinIO /
    # other-compatible host (e.g. ``http://localhost:9000``).
    tts_voice_blob_s3_endpoint: str = ""
    # Target bucket name. REQUIRED when ``tts_voice_blob_backend == 's3'``.
    tts_voice_blob_s3_bucket: str = ""
    # AWS region; empty means "let aiobotocore resolve via env/config".
    tts_voice_blob_s3_region: str = ""
    # S-025 — per-upload hard cap for voice-CRUD audio (NFR-SE-01). Default 10 MiB.
    tts_refaudio_max_bytes: int = 10 * 1024 * 1024

    def __post_init__(self) -> None:
        """Load all settings from environment and validate their values."""
        self._load_app_identity()
        self._load_provider_models()
        self._load_stt_models()
        self._load_tts_limits()
        self._load_runtime_knobs()
        self._load_voice_store_dir()
        self._load_voice_metadata_backend()
        self._load_voice_blob_backend()
        self.tts_refaudio_max_bytes = self._load_int(
            "TTS_REFAUDIO_MAX_BYTES", self.tts_refaudio_max_bytes, minimum=1
        )
        self.tts_voice_map = self._load_voice_map_from_file()

    def _load_voice_store_dir(self) -> None:
        """Resolve ``TTS_VOICE_STORE_DIR`` (default ``var/voices/``)."""
        raw = os.environ.get("TTS_VOICE_STORE_DIR", "").strip()
        self.tts_voice_store_dir = Path(raw) if raw else Path("var/voices")

    def _load_voice_metadata_backend(self) -> None:
        """Resolve ``TTS_VOICE_METADATA_BACKEND`` (default ``fs_json``)."""
        raw = os.environ.get("TTS_VOICE_METADATA_BACKEND", "").strip().lower()
        backend = raw or "fs_json"
        if backend not in _VALID_VOICE_METADATA_BACKENDS:
            raise ValueError(
                f"TTS_VOICE_METADATA_BACKEND={backend!r} is not valid "
                f"(expected one of: {', '.join(sorted(_VALID_VOICE_METADATA_BACKENDS))})"
            )
        self.tts_voice_metadata_backend = backend
        dsn = os.environ.get("TTS_VOICE_METADATA_DSN", "").strip()
        self.tts_voice_metadata_dsn = dsn or None

    def _load_voice_blob_backend(self) -> None:
        """Resolve ``TTS_VOICE_BLOB_BACKEND`` + S3-specific env vars (S-024).

        When the backend is ``s3`` the bucket name MUST be set; the
        endpoint/region are optional (AWS resolution covers the common
        case, MinIO needs an explicit endpoint). Validation runs here
        so misconfiguration fails fast at ``Settings()`` construction
        rather than at first request.
        """
        self.tts_voice_blob_backend = self._load_enum(
            "TTS_VOICE_BLOB_BACKEND",
            _VALID_VOICE_BLOB_BACKENDS,
            self.tts_voice_blob_backend,
        )
        self.tts_voice_blob_s3_endpoint = os.environ.get("TTS_VOICE_BLOB_S3_ENDPOINT", "").strip()
        self.tts_voice_blob_s3_bucket = os.environ.get("TTS_VOICE_BLOB_S3_BUCKET", "").strip()
        self.tts_voice_blob_s3_region = os.environ.get("TTS_VOICE_BLOB_S3_REGION", "").strip()
        if self.tts_voice_blob_backend == "s3" and not self.tts_voice_blob_s3_bucket:
            raise ValueError("TTS_VOICE_BLOB_S3_BUCKET must be set when TTS_VOICE_BLOB_BACKEND=s3")

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
        """Load provider choice and provider-specific model allow-lists.

        Post-S-006: ``TTS_PROVIDER`` is an *override*, no longer a default
        (FR-HW-06). When unset, empty, or ``auto`` we leave ``tts_provider``
        as the legacy fallback (``mlx_audio``) so any pre-S-006 consumer
        still sees a value, but auto-selection in ``dependencies.py`` will
        replace it with the device-derived choice. When an explicit
        provider is named we still validate the spelling here so a typo
        fails fast in ``Settings.__post_init__`` (before auto-selection
        runs).
        """
        provider_env = os.getenv("TTS_PROVIDER")
        raw = (provider_env or "").strip().lower()
        if raw in {"", "auto"}:
            # Auto-selection mode: keep the legacy default for backward
            # compat with consumers that read ``settings.tts_provider``
            # directly during startup. ``dependencies.py`` overwrites this
            # with the auto-selected name once the device profile is known.
            self.tts_provider = "mlx_audio"
        else:
            if raw not in {"mlx_audio", "voxtral", "vllm-omni"}:
                raise ValueError(
                    "TTS_PROVIDER must be one of 'mlx_audio', 'voxtral', 'vllm-omni' (or 'auto')"
                )
            self.tts_provider = raw

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

    def _load_runtime_knobs(self) -> None:
        """Parse and validate Sprint-2 runtime env vars (FR-CF-01..03).

        Validation policy:

        * **Enum-style** vars (``TTS_DEVICE``, ``TTS_DTYPE``, ``APP_LOG_FORMAT``)
          use the same ``frozenset`` membership pattern as ``engine/device.py``.
          An empty / whitespace-only value is treated as "use the default" so
          shell wrappers like ``export TTS_DEVICE=$DEVICE`` (with ``$DEVICE``
          unset) do not crash startup.
        * **Integer** vars are parsed with ``int()``; non-integers and
          out-of-range values raise ``ValueError`` with the env-var name in
          the message so operators can find the offender in logs.
        * **``TTS_INFERENCE_TIMEOUT_SECONDS``** is opt-in: unset / empty
          leaves the attribute at ``None`` (no ``asyncio.wait_for`` wrapper);
          any positive numeric value enables the wrapper at the synthesis
          path (S-007 / S-010 consume this attribute).
        * **``TTS_PRELOAD_MODELS``** parses a comma-separated list of
          ``provider:model`` pairs; entries without a colon, with an unknown
          provider, or with a model outside that provider's allow-list raise
          ``ValueError`` immediately so misconfiguration cannot defer to the
          first request.
        """
        self.tts_device = self._load_enum("TTS_DEVICE", _VALID_DEVICES, self.tts_device)
        self.tts_dtype = self._load_enum("TTS_DTYPE", _VALID_DTYPES, self.tts_dtype)
        self.app_log_format = self._load_enum(
            "APP_LOG_FORMAT", _VALID_LOG_FORMATS, self.app_log_format
        )

        self.tts_max_queue_depth = self._load_int(
            "TTS_MAX_QUEUE_DEPTH", self.tts_max_queue_depth, minimum=0
        )
        self.tts_model_cache_size = self._load_int(
            "TTS_MODEL_CACHE_SIZE", self.tts_model_cache_size, minimum=1
        )
        self.tts_shutdown_drain_seconds = self._load_int(
            "TTS_SHUTDOWN_DRAIN_SECONDS", self.tts_shutdown_drain_seconds, minimum=0
        )
        self.tts_min_free_memory_gb = self._load_int(
            "TTS_MIN_FREE_MEMORY_GB", self.tts_min_free_memory_gb, minimum=0
        )

        self.tts_inference_timeout_seconds = self._load_optional_timeout(
            "TTS_INFERENCE_TIMEOUT_SECONDS"
        )
        self.tts_preload_models = self._load_preload_models("TTS_PRELOAD_MODELS")

    @staticmethod
    def _load_enum(name: str, allowed: frozenset[str], default: str) -> str:
        """Read an env-driven enum-style value with frozenset membership."""
        raw = os.environ.get(name, default).strip().lower()
        if not raw:
            return default
        if raw not in allowed:
            raise ValueError(
                f"{name}={raw!r} is not valid (expected one of: {', '.join(sorted(allowed))})"
            )
        return raw

    @staticmethod
    def _load_int(name: str, default: int, *, minimum: int) -> int:
        """Read an env-driven integer with a lower-bound check."""
        raw = os.environ.get(name, "").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer") from exc
        if value < minimum:
            raise ValueError(f"{name} must be >= {minimum}")
        return value

    @staticmethod
    def _load_optional_timeout(name: str) -> float | None:
        """Parse an opt-in positive timeout in seconds.

        Unset / empty → ``None`` (timeout wrapper disabled). A positive
        numeric value enables the wrapper at the synthesis path. Zero and
        negative values are rejected because ``asyncio.wait_for(coro, 0)``
        is a foot-gun (it cancels before the coroutine yields).
        """
        raw = os.environ.get(name, "").strip()
        if not raw:
            return None
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must be a positive number of seconds") from exc
        if value <= 0:
            raise ValueError(f"{name} must be > 0 (omit the variable to disable the timeout)")
        return value

    def _load_preload_models(self, name: str) -> list[PreloadEntry]:
        """Parse ``provider:model,provider:model`` into typed entries.

        Validates each provider name against the known registry and the
        model against that provider's allow-list. The allow-list check
        uses ``tts_*_model_allowed`` populated earlier by
        ``_load_provider_models``, so callers must invoke this after
        provider-models loading.
        """
        raw = os.environ.get(name, "").strip()
        if not raw:
            return []
        entries: list[PreloadEntry] = []
        for item in self._split_csv(raw):
            if ":" not in item:
                raise ValueError(f"{name} entry {item!r} must be of the form 'provider:model'")
            provider, model = item.split(":", 1)
            provider = provider.strip()
            model = model.strip()
            if not provider or not model:
                raise ValueError(f"{name} entry {item!r} must have non-empty provider and model")
            allow_list = self._allow_list_for_provider(provider)
            if allow_list is None:
                raise ValueError(f"{name} entry {item!r}: unknown provider {provider!r}")
            if model not in allow_list:
                raise ValueError(
                    f"{name} entry {item!r}: model {model!r} not in "
                    f"allow-list for provider {provider!r}"
                )
            entries.append(PreloadEntry(provider=provider, model=model))
        return entries

    def _allow_list_for_provider(self, provider: str) -> list[str] | None:
        """Return the model allow-list for a known provider, else ``None``."""
        if provider == "mlx_audio":
            return self.tts_mlx_audio_model_allowed
        if provider == "voxtral":
            return self.tts_voxtral_model_allowed
        if provider == "vllm-omni":
            return self.tts_vllm_omni_model_allowed
        return None

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
