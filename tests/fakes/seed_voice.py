"""Shared `_seed_voice` helper for the in-memory voice store (S-026 T6).

Two non-frozen test modules previously each carried a near-identical copy of
this fixture (``test_openai_adapter.py`` and ``test_synthesize.py``). They
now import :func:`seed_voice` from here. The S-018 byte-identity paired UAT
(``test_openai_adapter_parity.py``) keeps its own local copy by design —
that file is frozen by the parity gate and cannot be edited as part of a
refactor story.
"""

from __future__ import annotations

import io
import wave

from fastapi.testclient import TestClient

from llm_tts_api.services.voice_store import VoiceRecord


def _tiny_wav_bytes() -> bytes:
    """Return a minimal valid WAV payload — enough to satisfy the temp-file path."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\x00\x00" * 16)
    return buf.getvalue()


async def seed_voice(
    client: TestClient,
    *,
    voice_id: str = "alloy",
    source: str = "crud",
    language: str = "Italian",
    target_db: float = -20.0,
    max_sentences_per_chunk: int = 2,
) -> VoiceRecord:
    """Populate the in-memory voice store fakes with a usable record + blob.

    All knobs are optional — callers that just need *some* valid voice can
    rely on the defaults; callers with more specific assertions (sentence
    chunking, source label, normalize target) override only what they need.
    """
    state = client.app.state
    record = VoiceRecord(
        id=voice_id,
        transcript="ref text",
        language=language,
        consent_acknowledged=True,
        target_db=target_db,
        max_sentences_per_chunk=max_sentences_per_chunk,
        source=source,  # type: ignore[arg-type]
    )
    await state.voice_metadata_repo.create(record)
    await state.voice_blob_repo.put(voice_id, _tiny_wav_bytes())
    return record
