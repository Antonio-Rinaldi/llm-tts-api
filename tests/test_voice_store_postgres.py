"""S-023 — Postgres metadata backend: selector + missing-extra + integration.

The integration test (`@pytest.mark.integration`) hits a real Postgres via
``TTS_VOICE_METADATA_DSN``; it skips cleanly when unset so default CI passes
without infra. Unit tests cover the dependency-selector branches: default
``fs_json``, explicit ``postgres``-with-extra, and ``postgres``-without-the-extra
(simulated by injecting ``ModuleNotFoundError`` into ``importlib``).
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import os
import sys
import uuid
from pathlib import Path

import pytest

from llm_tts_api.config import Settings
from llm_tts_api.dependencies import build_voice_metadata_repo
from llm_tts_api.services.voice_store import (
    FsJsonMetadataRepository,
    VoiceAlreadyExistsError,
    VoiceMetadataRepository,
    VoiceNotFoundError,
    VoiceRecord,
)


def _bare_settings(tmp_path: Path) -> Settings:
    """Construct a Settings without triggering the env-driven __post_init__."""
    settings = object.__new__(Settings)
    settings.tts_voice_store_dir = tmp_path
    settings.tts_voice_metadata_backend = "fs_json"
    settings.tts_voice_metadata_dsn = None
    return settings


# --- Settings parsing ------------------------------------------------------


def test_settings_voice_metadata_backend_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backend defaults to ``fs_json``; DSN defaults to ``None``."""
    settings = object.__new__(Settings)
    settings._load_voice_metadata_backend()  # type: ignore[attr-defined]
    assert settings.tts_voice_metadata_backend == "fs_json"
    assert settings.tts_voice_metadata_dsn is None


def test_settings_voice_metadata_backend_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TTS_VOICE_METADATA_BACKEND", "postgres")
    monkeypatch.setenv("TTS_VOICE_METADATA_DSN", "postgresql://x/y")
    settings = object.__new__(Settings)
    settings._load_voice_metadata_backend()  # type: ignore[attr-defined]
    assert settings.tts_voice_metadata_backend == "postgres"
    assert settings.tts_voice_metadata_dsn == "postgresql://x/y"


def test_settings_voice_metadata_backend_rejects_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TTS_VOICE_METADATA_BACKEND", "mongo")
    settings = object.__new__(Settings)
    with pytest.raises(ValueError, match="TTS_VOICE_METADATA_BACKEND"):
        settings._load_voice_metadata_backend()  # type: ignore[attr-defined]


# --- Dependency selector ---------------------------------------------------


def test_build_voice_metadata_repo_fs_json_default(tmp_path: Path) -> None:
    settings = _bare_settings(tmp_path)
    repo = build_voice_metadata_repo(settings)
    assert isinstance(repo, FsJsonMetadataRepository)
    assert isinstance(repo, VoiceMetadataRepository)


def test_build_voice_metadata_repo_unknown_backend_raises(tmp_path: Path) -> None:
    settings = _bare_settings(tmp_path)
    settings.tts_voice_metadata_backend = "mongo"
    with pytest.raises(ValueError, match="unknown voice metadata backend"):
        build_voice_metadata_repo(settings)


def test_build_voice_metadata_repo_postgres_missing_dsn(tmp_path: Path) -> None:
    settings = _bare_settings(tmp_path)
    settings.tts_voice_metadata_backend = "postgres"
    settings.tts_voice_metadata_dsn = None
    # If the [postgres] extra is installed in this env, we'll fail past the
    # import on the DSN check. If it's missing, we'll fail on missing_extra.
    # Either way, this configuration must NOT succeed.
    with pytest.raises((ValueError, RuntimeError)):
        build_voice_metadata_repo(settings)


def test_build_voice_metadata_repo_postgres_missing_extra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``postgres`` backend without ``psycopg`` → ``config_error.missing_extra``."""
    settings = _bare_settings(tmp_path)
    settings.tts_voice_metadata_backend = "postgres"
    settings.tts_voice_metadata_dsn = "postgresql://x/y"

    # Force ModuleNotFoundError on the lazy import of psycopg even when the
    # dev environment has it installed: drop both the postgres_metadata
    # module and ``psycopg`` from sys.modules, then point the import system
    # at a finder that raises for ``psycopg``.
    monkeypatch.delitem(
        sys.modules,
        "llm_tts_api.services.voice_store.postgres_metadata",
        raising=False,
    )
    for name in list(sys.modules):
        if name == "psycopg" or name.startswith("psycopg."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    real_find_spec = importlib.util.find_spec
    original_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__

    def fake_import(
        name: str,
        globals: object = None,
        locals: object = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "psycopg" or name.startswith("psycopg"):
            raise ModuleNotFoundError(f"No module named {name!r}", name="psycopg")
        return original_import(name, globals, locals, fromlist, level)  # type: ignore[misc, operator]

    # ``builtins.__import__`` is the hook the ``import`` statement uses.
    import builtins

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(RuntimeError, match="config_error.missing_extra"):
        build_voice_metadata_repo(settings)

    # Sanity: find_spec still works (we did not break the importer wholesale).
    assert real_find_spec("llm_tts_api.services.voice_store") is not None


# --- Integration: Protocol-level CRUD against a real Postgres --------------

_DSN_ENV = "TTS_VOICE_METADATA_DSN_TEST"
_dsn = os.environ.get(_DSN_ENV, "").strip()


def _make_record(voice_id: str) -> VoiceRecord:
    return VoiceRecord(
        id=voice_id,
        transcript="hello",
        language="en",
        consent_acknowledged=True,
    )


@pytest.mark.integration
@pytest.mark.skipif(not _dsn, reason=f"{_DSN_ENV} not set; integration test skipped")
async def test_postgres_metadata_protocol_crud() -> None:
    """Same Protocol-level surface as ``FsJsonMetadataRepository`` (FR-VS-01)."""
    pytest.importorskip("psycopg")
    from llm_tts_api.services.voice_store.postgres_metadata import (
        PostgresMetadataRepository,
    )

    repo = PostgresMetadataRepository(_dsn)
    # Unique voice id per run; clean up at the end so reruns are idempotent.
    voice_id = f"itest-{uuid.uuid4().hex[:8]}"
    try:
        assert isinstance(repo, VoiceMetadataRepository)
        assert await repo.exists(voice_id) is False

        record = _make_record(voice_id)
        persisted = await repo.create(record)
        assert persisted.id == voice_id
        assert await repo.exists(voice_id) is True

        fetched = await repo.get(voice_id)
        assert fetched.language == "en"
        assert fetched.consent_acknowledged is True

        listed_ids = {r.id for r in await repo.list()}
        assert voice_id in listed_ids

        updated_record = _make_record(voice_id)
        updated_record.transcript = "updated text"
        updated = await repo.update(updated_record)
        assert updated.transcript == "updated text"
        assert (await repo.get(voice_id)).transcript == "updated text"

        with pytest.raises(VoiceAlreadyExistsError):
            await repo.create(_make_record(voice_id))
    finally:
        with contextlib.suppress(VoiceNotFoundError):
            await repo.delete(voice_id)
        assert await repo.exists(voice_id) is False
        with pytest.raises(VoiceNotFoundError):
            await repo.delete(voice_id)


@pytest.mark.integration
@pytest.mark.skipif(not _dsn, reason=f"{_DSN_ENV} not set; integration test skipped")
async def test_postgres_metadata_concurrent_creates(tmp_path: Path) -> None:
    pytest.importorskip("psycopg")
    from llm_tts_api.services.voice_store.postgres_metadata import (
        PostgresMetadataRepository,
    )

    repo = PostgresMetadataRepository(_dsn)
    prefix = f"itest-{uuid.uuid4().hex[:6]}"
    ids = [f"{prefix}-{i}" for i in range(5)]
    try:
        await asyncio.gather(*(repo.create(_make_record(vid)) for vid in ids))
        existing = {r.id for r in await repo.list()}
        for vid in ids:
            assert vid in existing
    finally:
        for vid in ids:
            with contextlib.suppress(VoiceNotFoundError):
                await repo.delete(vid)
