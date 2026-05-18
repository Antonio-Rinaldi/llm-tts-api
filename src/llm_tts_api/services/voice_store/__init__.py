"""Voice store package — repository Protocols + default FS implementations.

This module is the publish point for S-022. Step-2 stories (S-023 Postgres,
S-024 S3) import these Protocols and provide alternate implementations behind
optional extras. S-025 CRUD endpoints and S-011 seed ingestion consume the
repos via ``app.state.voice_metadata_repo`` / ``app.state.voice_blob_repo``.
"""

from __future__ import annotations

from llm_tts_api.services.voice_store.errors import (
    VoiceAlreadyExistsError,
    VoiceIdInvalidError,
    VoiceNotFoundError,
    VoiceStoreError,
)
from llm_tts_api.services.voice_store.fs_blob import FsBlobRepository
from llm_tts_api.services.voice_store.fs_json_metadata import FsJsonMetadataRepository
from llm_tts_api.services.voice_store.protocols import (
    VoiceBlobRepository,
    VoiceMetadataRepository,
)
from llm_tts_api.services.voice_store.records import (
    VOICE_ID_PATTERN,
    VOICE_ID_REGEX,
    VoiceRecord,
    validate_voice_id,
)
from llm_tts_api.services.voice_store.seed_ingestion import (
    VoiceSeedIngestor,
    force_polling_from_env,
    resolve_seed_file_path,
)

__all__ = [
    "VOICE_ID_PATTERN",
    "VOICE_ID_REGEX",
    "FsBlobRepository",
    "FsJsonMetadataRepository",
    "VoiceAlreadyExistsError",
    "VoiceBlobRepository",
    "VoiceIdInvalidError",
    "VoiceMetadataRepository",
    "VoiceNotFoundError",
    "VoiceRecord",
    "VoiceSeedIngestor",
    "VoiceStoreError",
    "force_polling_from_env",
    "resolve_seed_file_path",
    "validate_voice_id",
]
