"""S-022 — Voice store: Protocols, FS defaults, atomicity, path-safety."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from llm_tts_api.services.voice_store import (
    VOICE_ID_PATTERN,
    FsBlobRepository,
    FsJsonMetadataRepository,
    VoiceAlreadyExistsError,
    VoiceBlobRepository,
    VoiceIdInvalidError,
    VoiceMetadataRepository,
    VoiceNotFoundError,
    VoiceRecord,
    validate_voice_id,
)


def _make_record(voice_id: str = "alice", consent: bool = True) -> VoiceRecord:
    return VoiceRecord(
        id=voice_id,
        transcript="hello world",
        language="en",
        consent_acknowledged=consent,
    )


# --- voice id validation (NFR-SE-03) ---------------------------------------


@pytest.mark.parametrize("good", ["a", "abc", "a-b_c", "abc-123_xyz", "a" * 64, "0", "_-"])
def test_validate_voice_id_accepts(good: str) -> None:
    assert validate_voice_id(good) == good


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "A",  # uppercase forbidden
        "..",
        "../etc/passwd",
        "a/b",
        "a\\b",
        "a.b",
        "a b",
        "a" * 65,  # length cap
        "héllo",  # non-ascii
        "/",
        "abs/path",
    ],
)
def test_validate_voice_id_rejects(bad: str) -> None:
    with pytest.raises(VoiceIdInvalidError):
        validate_voice_id(bad)


def test_voice_id_pattern_constant() -> None:
    assert VOICE_ID_PATTERN == r"^[a-z0-9_-]{1,64}$"


# --- protocol structural conformance ---------------------------------------


def test_fs_repos_satisfy_protocols(tmp_path: Path) -> None:
    meta = FsJsonMetadataRepository(tmp_path)
    blob = FsBlobRepository(tmp_path)
    assert isinstance(meta, VoiceMetadataRepository)
    assert isinstance(blob, VoiceBlobRepository)


# --- FsJsonMetadataRepository CRUD -----------------------------------------


async def test_metadata_create_get_list_update_delete(tmp_path: Path) -> None:
    repo = FsJsonMetadataRepository(tmp_path)
    assert await repo.list() == []

    record = _make_record("alice")
    persisted = await repo.create(record)
    assert persisted.id == "alice"
    assert await repo.exists("alice") is True

    fetched = await repo.get("alice")
    assert fetched.transcript == "hello world"
    assert fetched.language == "en"

    listed = await repo.list()
    assert [r.id for r in listed] == ["alice"]

    updated_record = _make_record("alice")
    updated_record.transcript = "updated"
    updated = await repo.update(updated_record)
    assert updated.transcript == "updated"
    assert (await repo.get("alice")).transcript == "updated"

    await repo.delete("alice")
    assert await repo.exists("alice") is False
    assert await repo.list() == []


async def test_metadata_create_duplicate_raises(tmp_path: Path) -> None:
    repo = FsJsonMetadataRepository(tmp_path)
    await repo.create(_make_record("alice"))
    with pytest.raises(VoiceAlreadyExistsError):
        await repo.create(_make_record("alice"))


async def test_metadata_get_missing_raises(tmp_path: Path) -> None:
    repo = FsJsonMetadataRepository(tmp_path)
    with pytest.raises(VoiceNotFoundError):
        await repo.get("missing")


async def test_metadata_update_missing_raises(tmp_path: Path) -> None:
    repo = FsJsonMetadataRepository(tmp_path)
    with pytest.raises(VoiceNotFoundError):
        await repo.update(_make_record("nope"))


async def test_metadata_delete_missing_raises(tmp_path: Path) -> None:
    repo = FsJsonMetadataRepository(tmp_path)
    with pytest.raises(VoiceNotFoundError):
        await repo.delete("nope")


async def test_metadata_invalid_id_rejected(tmp_path: Path) -> None:
    repo = FsJsonMetadataRepository(tmp_path)
    with pytest.raises(VoiceIdInvalidError):
        await repo.get("../etc/passwd")
    with pytest.raises(VoiceIdInvalidError):
        await repo.exists("BAD")
    with pytest.raises(VoiceIdInvalidError):
        await repo.delete("a/b")
    with pytest.raises(VoiceIdInvalidError):
        await repo.create(_make_record("Bad-ID"))


async def test_metadata_persists_across_instances(tmp_path: Path) -> None:
    repo_a = FsJsonMetadataRepository(tmp_path)
    await repo_a.create(_make_record("alice"))

    repo_b = FsJsonMetadataRepository(tmp_path)
    assert await repo_b.exists("alice") is True
    record = await repo_b.get("alice")
    assert record.language == "en"


async def test_metadata_atomic_write_no_partial_on_failure(tmp_path: Path) -> None:
    repo = FsJsonMetadataRepository(tmp_path)
    await repo.create(_make_record("alice"))
    original = (tmp_path / "metadata.json").read_bytes()

    # Simulate a failure mid-write by replacing tempfile.NamedTemporaryFile
    # with one that raises on write. The on-disk file must remain the prior
    # state because we ``os.replace`` only the fully-written temp.
    bad_record = _make_record("bob")
    real_replace = os.replace

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated rename failure")

    os.replace = boom  # type: ignore[assignment]
    try:
        with pytest.raises(OSError, match="simulated"):
            await repo.create(bad_record)
    finally:
        os.replace = real_replace  # type: ignore[assignment]

    # File untouched; no orphan temp left lying around.
    assert (tmp_path / "metadata.json").read_bytes() == original
    leftover = [p for p in tmp_path.iterdir() if p.name.startswith(".metadata.")]
    assert leftover == []


async def test_metadata_concurrent_creates_serialize(tmp_path: Path) -> None:
    repo = FsJsonMetadataRepository(tmp_path)
    records = [_make_record(f"voice-{i}") for i in range(10)]
    await asyncio.gather(*(repo.create(r) for r in records))
    persisted_ids = sorted(r.id for r in await repo.list())
    assert persisted_ids == sorted(r.id for r in records)

    raw = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
    assert sorted(raw.keys()) == persisted_ids


# --- FsBlobRepository ------------------------------------------------------


async def test_blob_put_get_delete(tmp_path: Path) -> None:
    repo = FsBlobRepository(tmp_path)
    assert await repo.exists("alice") is False

    await repo.put("alice", b"WAV-BYTES")
    assert await repo.exists("alice") is True
    assert await repo.get("alice") == b"WAV-BYTES"

    await repo.put("alice", b"NEW-BYTES")  # overwrite
    assert await repo.get("alice") == b"NEW-BYTES"

    await repo.delete("alice")
    assert await repo.exists("alice") is False
    with pytest.raises(VoiceNotFoundError):
        await repo.get("alice")


async def test_blob_path_layout(tmp_path: Path) -> None:
    repo = FsBlobRepository(tmp_path)
    await repo.put("alice", b"X")
    assert (tmp_path / "blobs" / "alice.wav").read_bytes() == b"X"


@pytest.mark.parametrize(
    "bad",
    ["..", "../etc/passwd", "a/b", "a\\b", "abs/path", "A", ""],
)
async def test_blob_rejects_malformed_ids(tmp_path: Path, bad: str) -> None:
    repo = FsBlobRepository(tmp_path)
    with pytest.raises(VoiceIdInvalidError):
        await repo.put(bad, b"X")
    with pytest.raises(VoiceIdInvalidError):
        await repo.get(bad)
    with pytest.raises(VoiceIdInvalidError):
        await repo.exists(bad)
    with pytest.raises(VoiceIdInvalidError):
        await repo.delete(bad)


async def test_blob_traversal_does_not_escape_store_dir(tmp_path: Path) -> None:
    """NFR-SE-03 / UAT-VS-06: ensure no file is created outside the store."""
    repo = FsBlobRepository(tmp_path)
    parent_before = sorted(tmp_path.parent.iterdir())
    with pytest.raises(VoiceIdInvalidError):
        await repo.put("../escape", b"X")
    # Nothing was created in the parent.
    assert sorted(tmp_path.parent.iterdir()) == parent_before


async def test_blob_atomic_overwrite(tmp_path: Path) -> None:
    repo = FsBlobRepository(tmp_path)
    await repo.put("alice", b"FIRST")

    real_replace = os.replace

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated rename failure")

    os.replace = boom  # type: ignore[assignment]
    try:
        with pytest.raises(OSError, match="simulated"):
            await repo.put("alice", b"SECOND")
    finally:
        os.replace = real_replace  # type: ignore[assignment]

    assert (tmp_path / "blobs" / "alice.wav").read_bytes() == b"FIRST"
    leftover = [p for p in (tmp_path / "blobs").iterdir() if p.name.startswith(".alice.")]
    assert leftover == []


async def test_blob_delete_missing_raises(tmp_path: Path) -> None:
    repo = FsBlobRepository(tmp_path)
    with pytest.raises(VoiceNotFoundError):
        await repo.delete("alice")


# --- Base install does not pull optional extras (NFR-ST-01) ----------------


def test_base_install_does_not_import_optional_extras() -> None:
    """Importing the voice-store package MUST NOT pull psycopg/boto3/aiobotocore.

    Step-2 stories add these as optional extras. Running this check in a
    subprocess (clean ``sys.modules``) protects against accidental top-level
    imports leaking back into the default install path.
    """
    script = (
        "import sys\n"
        "import llm_tts_api.services.voice_store  # noqa: F401\n"
        "leaked = sorted(name for name in sys.modules\n"
        "                if name.split('.')[0] in {'psycopg', 'boto3',\n"
        "                                          'botocore', 'aiobotocore',\n"
        "                                          'sqlalchemy'})\n"
        "if leaked:\n"
        "    raise SystemExit('leaked: ' + ','.join(leaked))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"voice_store package leaked optional-extra imports: {result.stderr or result.stdout}"
    )


# --- Settings.tts_voice_store_dir + dependencies wiring --------------------


def test_settings_voice_store_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """``TTS_VOICE_STORE_DIR`` defaults to ``var/voices`` when unset."""
    from llm_tts_api.config import Settings

    # Skip the env-driven __post_init__ path; just exercise the loader.
    settings = object.__new__(Settings)
    settings._load_voice_store_dir()  # type: ignore[attr-defined]
    assert settings.tts_voice_store_dir == Path("var/voices")


def test_settings_voice_store_dir_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from llm_tts_api.config import Settings

    monkeypatch.setenv("TTS_VOICE_STORE_DIR", "/tmp/custom-voices")
    settings = object.__new__(Settings)
    settings._load_voice_store_dir()  # type: ignore[attr-defined]
    assert settings.tts_voice_store_dir == Path("/tmp/custom-voices")
