"""S-024 — S3 blob repository: selector wiring + integration tests.

The unit tests in this module exercise paths that DO NOT require
``aiobotocore`` to be installed:

* settings validation for ``TTS_VOICE_BLOB_BACKEND``
* selector dispatch in :func:`_build_voice_blob_repo`
* ``provider_error.missing_extra`` surfacing when the extra is absent

The full Protocol-level CRUD test (against MinIO or AWS S3) is gated by
``@pytest.mark.integration`` and skips when ``aiobotocore`` is not
importable OR ``TTS_VOICE_BLOB_S3_ENDPOINT`` / ``_BUCKET`` are unset.
"""

from __future__ import annotations

import importlib
import os
import sys
import uuid
from collections.abc import Iterator

import pytest

from llm_tts_api.config import Settings
from llm_tts_api.dependencies import _build_voice_blob_repo
from llm_tts_api.services.voice_store import FsBlobRepository, VoiceBlobRepository

# --- Settings validation ---------------------------------------------------


def test_settings_voice_blob_backend_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TTS_VOICE_BLOB_BACKEND", raising=False)
    monkeypatch.delenv("TTS_VOICE_BLOB_S3_BUCKET", raising=False)
    settings = object.__new__(Settings)
    # Set attribute defaults the partial loader expects.
    settings.tts_voice_blob_backend = "fs"
    settings.tts_voice_blob_s3_endpoint = ""
    settings.tts_voice_blob_s3_bucket = ""
    settings.tts_voice_blob_s3_region = ""
    settings._load_voice_blob_backend()  # type: ignore[attr-defined]
    assert settings.tts_voice_blob_backend == "fs"
    assert settings.tts_voice_blob_s3_bucket == ""


def test_settings_voice_blob_backend_s3_requires_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TTS_VOICE_BLOB_BACKEND", "s3")
    monkeypatch.delenv("TTS_VOICE_BLOB_S3_BUCKET", raising=False)
    settings = object.__new__(Settings)
    settings.tts_voice_blob_backend = "fs"
    settings.tts_voice_blob_s3_endpoint = ""
    settings.tts_voice_blob_s3_bucket = ""
    settings.tts_voice_blob_s3_region = ""
    with pytest.raises(ValueError, match="TTS_VOICE_BLOB_S3_BUCKET"):
        settings._load_voice_blob_backend()  # type: ignore[attr-defined]


def test_settings_voice_blob_backend_s3_accepts_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TTS_VOICE_BLOB_BACKEND", "s3")
    monkeypatch.setenv("TTS_VOICE_BLOB_S3_BUCKET", "voices")
    monkeypatch.setenv("TTS_VOICE_BLOB_S3_ENDPOINT", "http://localhost:9000")
    monkeypatch.setenv("TTS_VOICE_BLOB_S3_REGION", "us-east-1")
    settings = object.__new__(Settings)
    settings.tts_voice_blob_backend = "fs"
    settings.tts_voice_blob_s3_endpoint = ""
    settings.tts_voice_blob_s3_bucket = ""
    settings.tts_voice_blob_s3_region = ""
    settings._load_voice_blob_backend()  # type: ignore[attr-defined]
    assert settings.tts_voice_blob_backend == "s3"
    assert settings.tts_voice_blob_s3_bucket == "voices"
    assert settings.tts_voice_blob_s3_endpoint == "http://localhost:9000"
    assert settings.tts_voice_blob_s3_region == "us-east-1"


def test_settings_voice_blob_backend_invalid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TTS_VOICE_BLOB_BACKEND", "gcs")
    settings = object.__new__(Settings)
    settings.tts_voice_blob_backend = "fs"
    settings.tts_voice_blob_s3_endpoint = ""
    settings.tts_voice_blob_s3_bucket = ""
    settings.tts_voice_blob_s3_region = ""
    with pytest.raises(ValueError, match="TTS_VOICE_BLOB_BACKEND"):
        settings._load_voice_blob_backend()  # type: ignore[attr-defined]


# --- Selector dispatch -----------------------------------------------------


def _make_settings(**overrides: object) -> Settings:
    """Hand-build a Settings instance without invoking ``__post_init__``."""
    settings = object.__new__(Settings)
    settings.tts_voice_store_dir = overrides.get("tts_voice_store_dir", "var/voices")  # type: ignore[assignment]
    settings.tts_voice_blob_backend = overrides.get("tts_voice_blob_backend", "fs")  # type: ignore[assignment]
    settings.tts_voice_blob_s3_endpoint = overrides.get("tts_voice_blob_s3_endpoint", "")  # type: ignore[assignment]
    settings.tts_voice_blob_s3_bucket = overrides.get("tts_voice_blob_s3_bucket", "")  # type: ignore[assignment]
    settings.tts_voice_blob_s3_region = overrides.get("tts_voice_blob_s3_region", "")  # type: ignore[assignment]
    return settings


def test_selector_default_returns_fs_blob_repo(tmp_path: object) -> None:
    settings = _make_settings(tts_voice_store_dir=tmp_path)
    repo = _build_voice_blob_repo(settings)
    assert isinstance(repo, FsBlobRepository)
    assert isinstance(repo, VoiceBlobRepository)


def test_selector_s3_missing_extra_raises_named_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``aiobotocore`` is unimportable, selector must surface the name.

    We simulate the missing extra by inserting a finder that raises
    ``ModuleNotFoundError`` for the ``aiobotocore`` package, which
    forces the lazy ``from … import S3BlobRepository`` to fail.
    """
    # Clear any cached copies so the import path is re-evaluated.
    for mod in list(sys.modules):
        if mod.startswith(("aiobotocore", "llm_tts_api.services.voice_store.s3_blob")):
            sys.modules.pop(mod, None)
    sys.modules["aiobotocore"] = None  # type: ignore[assignment]

    settings = _make_settings(tts_voice_blob_backend="s3", tts_voice_blob_s3_bucket="voices")
    try:
        with pytest.raises(RuntimeError, match="provider_error.missing_extra") as exc_info:
            _build_voice_blob_repo(settings)
        assert "aiobotocore" in str(exc_info.value)
        assert "[s3]" in str(exc_info.value)
    finally:
        # Reset our sentinel so the integration test below can still import
        # aiobotocore if it is installed.
        sys.modules.pop("aiobotocore", None)


def test_selector_rejects_unknown_backend_value() -> None:
    settings = _make_settings(tts_voice_blob_backend="azure")
    with pytest.raises(ValueError, match="TTS_VOICE_BLOB_BACKEND"):
        _build_voice_blob_repo(settings)


# --- Integration: real S3 (MinIO) ------------------------------------------


def _s3_integration_skip_reason() -> str | None:
    if importlib.util.find_spec("aiobotocore") is None:
        return "aiobotocore not installed (install with '.[s3]')"
    if not os.environ.get("TTS_VOICE_BLOB_S3_ENDPOINT"):
        return "TTS_VOICE_BLOB_S3_ENDPOINT not set"
    if not os.environ.get("TTS_VOICE_BLOB_S3_BUCKET"):
        return "TTS_VOICE_BLOB_S3_BUCKET not set"
    return None


@pytest.fixture
def _ensure_bucket() -> Iterator[None]:
    """Create the target bucket if it does not exist (MinIO test helper)."""
    pytest.importorskip("aiobotocore")
    import asyncio

    from aiobotocore.session import get_session  # type: ignore[import-not-found]
    from botocore.exceptions import ClientError  # type: ignore[import-not-found]

    bucket = os.environ["TTS_VOICE_BLOB_S3_BUCKET"]
    endpoint = os.environ.get("TTS_VOICE_BLOB_S3_ENDPOINT") or None
    region = os.environ.get("TTS_VOICE_BLOB_S3_REGION") or None

    async def _create() -> None:
        session = get_session()
        async with session.create_client("s3", endpoint_url=endpoint, region_name=region) as client:
            try:
                await client.head_bucket(Bucket=bucket)
            except ClientError:
                await client.create_bucket(Bucket=bucket)

    asyncio.get_event_loop().run_until_complete(_create())
    yield


@pytest.mark.integration
@pytest.mark.skipif(_s3_integration_skip_reason() is not None, reason="s3 disabled")
async def test_s3_blob_protocol_crud(_ensure_bucket: None) -> None:
    """Same surface as the FsBlobRepository tests, run against a live S3."""
    pytest.importorskip("aiobotocore")
    from llm_tts_api.services.voice_store import VoiceNotFoundError
    from llm_tts_api.services.voice_store.s3_blob import S3BlobRepository

    repo = S3BlobRepository(
        bucket=os.environ["TTS_VOICE_BLOB_S3_BUCKET"],
        endpoint_url=os.environ.get("TTS_VOICE_BLOB_S3_ENDPOINT") or None,
        region_name=os.environ.get("TTS_VOICE_BLOB_S3_REGION") or None,
    )
    voice_id = f"itg-{uuid.uuid4().hex[:8]}"

    try:
        assert await repo.exists(voice_id) is False

        await repo.put(voice_id, b"WAV-BYTES")
        assert await repo.exists(voice_id) is True
        assert await repo.get(voice_id) == b"WAV-BYTES"

        await repo.put(voice_id, b"NEW-BYTES")
        assert await repo.get(voice_id) == b"NEW-BYTES"

        await repo.delete(voice_id)
        assert await repo.exists(voice_id) is False
        with pytest.raises(VoiceNotFoundError):
            await repo.get(voice_id)
    finally:
        # Best-effort cleanup if the test bails mid-flight.
        import contextlib

        with contextlib.suppress(Exception):
            await repo.delete(voice_id)
