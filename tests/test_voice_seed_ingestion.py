"""S-011 — voice_map.json seed ingestion (UAT-VM-01..05).

Covers FR-VM-01..05:
  - empty-store ingest at startup
  - idempotent re-runs that preserve CRUD voices
  - hot reload on file change within ~2 s
  - atomic-per-pass validation: invalid edit preserves prior state
  - unset / missing seed file is a clean no-op
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import pytest

from llm_tts_api.services.voice_store import (
    VoiceRecord,
    VoiceSeedIngestor,
    resolve_seed_file_path,
)
from tests.fakes.fake_voice_store import (
    FakeVoiceBlobRepository,
    FakeVoiceMetadataRepository,
)


def _write_audio(tmp_path: Path, name: str = "ref.wav", payload: bytes = b"RIFF") -> Path:
    p = tmp_path / name
    p.write_bytes(payload)
    return p


def _voice_map(audio: Path, voice_id: str = "alloy") -> dict[str, dict[str, object]]:
    return {
        voice_id: {
            "ref_audio_path": str(audio),
            "ref_text": f"transcript for {voice_id}",
            "language": "English",
            "number_lang": "en",
            "temperature": 0.7,
            "top_p": 0.9,
            "target_db": -18.0,
            "max_sentences_per_chunk": 3,
        }
    }


def _ingestor(
    *, seed: Path | None, force_polling: bool = False
) -> tuple[VoiceSeedIngestor, FakeVoiceMetadataRepository, FakeVoiceBlobRepository]:
    metadata = FakeVoiceMetadataRepository()
    blob = FakeVoiceBlobRepository()
    ing = VoiceSeedIngestor(
        metadata_repo=metadata,
        blob_repo=blob,
        seed_file_path=seed,
        force_polling=force_polling,
    )
    return ing, metadata, blob


# --- UAT-VM-01 -------------------------------------------------------------


async def test_ingest_once_populates_empty_store(tmp_path: Path) -> None:
    audio_a = _write_audio(tmp_path, "a.wav", b"WAVA")
    audio_b = _write_audio(tmp_path, "b.wav", b"WAVB")
    seed = tmp_path / "voice_map.json"
    voice_map = {**_voice_map(audio_a, "alloy"), **_voice_map(audio_b, "nova")}
    seed.write_text(json.dumps(voice_map))
    ing, meta, blob = _ingestor(seed=seed)

    created = await ing.ingest_once()

    assert created == 2
    records = sorted(await meta.list(), key=lambda r: r.id)
    assert [r.id for r in records] == ["alloy", "nova"]
    assert all(r.source == "seed" for r in records)
    assert all(r.consent_acknowledged is True for r in records)
    assert await blob.get("alloy") == b"WAVA"
    assert await blob.get("nova") == b"WAVB"
    # Optional fields propagate from the JSON entry.
    alloy = await meta.get("alloy")
    assert alloy.transcript == "transcript for alloy"
    assert alloy.language == "English"
    assert alloy.temperature == pytest.approx(0.7)


# --- UAT-VM-02 -------------------------------------------------------------


async def test_ingest_once_preserves_crud_and_adds_new_seeds(tmp_path: Path) -> None:
    audio_alloy = _write_audio(tmp_path, "alloy.wav", b"ALLOY")
    audio_new = _write_audio(tmp_path, "new.wav", b"NEW")
    seed = tmp_path / "voice_map.json"
    voice_map = {**_voice_map(audio_alloy, "alloy"), **_voice_map(audio_new, "new_seed")}
    seed.write_text(json.dumps(voice_map))
    ing, meta, blob = _ingestor(seed=seed)

    # Preload: CRUD voice + an existing seed (simulating prior restart).
    await meta.create(
        VoiceRecord(
            id="myvoice",
            transcript="user upload",
            language="English",
            consent_acknowledged=True,
            source="crud",
        )
    )
    await blob.put("myvoice", b"CRUDBLOB")
    await meta.create(
        VoiceRecord(
            id="alloy",
            transcript="stale transcript",
            language="English",
            consent_acknowledged=True,
            source="seed",
        )
    )
    await blob.put("alloy", b"OLDALLOY")

    created = await ing.ingest_once()

    assert created == 1  # only new_seed
    # CRUD voice untouched
    crud = await meta.get("myvoice")
    assert crud.source == "crud"
    assert crud.transcript == "user upload"
    assert await blob.get("myvoice") == b"CRUDBLOB"
    # Existing seed entry is NOT clobbered (idempotency, even for source=seed).
    alloy = await meta.get("alloy")
    assert alloy.transcript == "stale transcript"
    assert await blob.get("alloy") == b"OLDALLOY"
    # New seed is added.
    new_seed = await meta.get("new_seed")
    assert new_seed.source == "seed"
    assert await blob.get("new_seed") == b"NEW"


# --- UAT-VM-03 -------------------------------------------------------------


async def test_watch_picks_up_file_change_within_two_seconds(tmp_path: Path) -> None:
    audio_a = _write_audio(tmp_path, "a.wav", b"A")
    seed = tmp_path / "voice_map.json"
    seed.write_text(json.dumps(_voice_map(audio_a, "alloy")))
    ing, meta, blob = _ingestor(seed=seed, force_polling=True)

    await ing.ingest_once()
    assert [r.id for r in await meta.list()] == ["alloy"]

    watcher = asyncio.create_task(ing.watch_and_ingest())
    try:
        audio_b = _write_audio(tmp_path, "b.wav", b"B")
        new_map = {
            **_voice_map(audio_a, "alloy"),
            **_voice_map(audio_b, "nova"),
        }
        new_payload = json.dumps(new_map)

        # Re-touch the seed until the watcher picks it up. ``awatch`` may
        # need a few hundred ms to bind in polling mode, and on macOS
        # ``mtime`` has coarse (~1 s) resolution under some FSes, so a
        # single write right after task creation can be missed.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            seed.write_text(new_payload)
            await asyncio.sleep(0.1)
            ids = sorted(r.id for r in await meta.list())
            if ids == ["alloy", "nova"]:
                break
        else:  # pragma: no cover - failure path only
            pytest.fail("hot reload did not pick up new entry within 2s")
        assert await blob.get("nova") == b"B"
    finally:
        watcher.cancel()
        with pytest.raises(asyncio.CancelledError):
            await watcher


# --- UAT-VM-04 -------------------------------------------------------------


async def test_invalid_edit_preserves_store_and_logs_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    audio = _write_audio(tmp_path, "a.wav", b"AAA")
    seed = tmp_path / "voice_map.json"
    seed.write_text(json.dumps(_voice_map(audio, "alloy")))
    ing, meta, blob = _ingestor(seed=seed)
    await ing.ingest_once()
    pre_records = await meta.list()
    pre_blob = await blob.get("alloy")
    assert len(pre_records) == 1

    # Now mutate the file to reference a non-existent audio AND add a new
    # entry. FR-VM-03: NO entries from this pass should apply.
    bad_map = {
        "alloy": {
            **_voice_map(audio, "alloy")["alloy"],
            "ref_audio_path": str(tmp_path / "does_not_exist.wav"),
        },
        "new_seed": _voice_map(audio, "new_seed")["new_seed"],
    }
    seed.write_text(json.dumps(bad_map))

    with caplog.at_level(logging.ERROR):
        created = await ing.ingest_once()

    assert created == 0
    assert await meta.list() == pre_records
    assert await blob.get("alloy") == pre_blob
    assert not await blob.exists("new_seed")
    assert any("provider_error.voice_seed_ingest_failed" in r.message for r in caplog.records), (
        caplog.text
    )


# --- UAT-VM-05 -------------------------------------------------------------


async def test_unset_seed_file_is_clean_noop() -> None:
    ing, meta, blob = _ingestor(seed=None)
    created = await ing.ingest_once()
    assert created == 0
    assert await meta.list() == []


async def test_missing_seed_file_is_clean_noop(tmp_path: Path) -> None:
    ghost = tmp_path / "missing.json"
    ing, meta, _blob = _ingestor(seed=ghost)
    created = await ing.ingest_once()
    assert created == 0
    assert await meta.list() == []


# --- Validation surface (atomic-per-pass guarantees) -----------------------


@pytest.mark.parametrize(
    "mutate",
    [
        # invalid voice id (path traversal)
        lambda body: {"../etc/passwd": body["alloy"]},
        # body not an object
        lambda body: {"alloy": "not-an-object"},
        # missing language
        lambda body: {"alloy": {**body["alloy"], "language": ""}},
        # bad temperature
        lambda body: {"alloy": {**body["alloy"], "temperature": 5.0}},
        # bad top_p
        lambda body: {"alloy": {**body["alloy"], "top_p": 0.0}},
        # bad max_sentences_per_chunk
        lambda body: {"alloy": {**body["alloy"], "max_sentences_per_chunk": 0}},
        # ref_audio_path missing on disk (validation, not just runtime)
        lambda body: {"alloy": {**body["alloy"], "ref_audio_path": "/tmp/__no_such_file__.wav"}},
    ],
)
async def test_validation_aborts_full_pass(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    mutate: object,
) -> None:
    audio = _write_audio(tmp_path, "a.wav", b"A")
    base = _voice_map(audio, "alloy")
    seed = tmp_path / "voice_map.json"
    bad = mutate(base)  # type: ignore[operator]
    seed.write_text(json.dumps(bad))
    ing, meta, _blob = _ingestor(seed=seed)
    with caplog.at_level(logging.ERROR):
        created = await ing.ingest_once()
    assert created == 0
    assert await meta.list() == []
    assert any("provider_error.voice_seed_ingest_failed" in r.message for r in caplog.records)


async def test_invalid_json_root_aborts(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    seed = tmp_path / "voice_map.json"
    seed.write_text("not-json{{")
    ing, meta, _blob = _ingestor(seed=seed)
    with caplog.at_level(logging.ERROR):
        created = await ing.ingest_once()
    assert created == 0
    assert await meta.list() == []
    assert any("provider_error.voice_seed_ingest_failed" in r.message for r in caplog.records)


async def test_non_object_root_aborts(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    seed = tmp_path / "voice_map.json"
    seed.write_text(json.dumps([{"id": "alloy"}]))
    ing, meta, _blob = _ingestor(seed=seed)
    with caplog.at_level(logging.ERROR):
        created = await ing.ingest_once()
    assert created == 0
    assert any("provider_error.voice_seed_ingest_failed" in r.message for r in caplog.records)


# --- Env helpers -----------------------------------------------------------


def test_resolve_seed_file_path_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TTS_VOICE_MAP_FILE", raising=False)
    assert resolve_seed_file_path() is None


def test_resolve_seed_file_path_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / "vm.json"
    f.write_text("{}")
    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(f))
    assert resolve_seed_file_path() == f


def test_resolve_seed_file_path_missing_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TTS_VOICE_MAP_FILE", str(tmp_path / "absent.json"))
    assert resolve_seed_file_path() is None


def test_force_polling_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from llm_tts_api.services.voice_store import force_polling_from_env

    monkeypatch.delenv("TTS_VOICE_MAP_WATCH_FORCE_POLLING", raising=False)
    assert force_polling_from_env() is False
    monkeypatch.setenv("TTS_VOICE_MAP_WATCH_FORCE_POLLING", "1")
    assert force_polling_from_env() is True
    monkeypatch.setenv("TTS_VOICE_MAP_WATCH_FORCE_POLLING", "true")
    assert force_polling_from_env() is True
    monkeypatch.setenv("TTS_VOICE_MAP_WATCH_FORCE_POLLING", "off")
    assert force_polling_from_env() is False


# --- Watcher with no seed path is a clean no-op ----------------------------


async def test_watch_and_ingest_no_seed_returns_immediately() -> None:
    ing, _meta, _blob = _ingestor(seed=None)
    # Should return without spinning the watchfiles loop.
    await asyncio.wait_for(ing.watch_and_ingest(), timeout=0.5)
