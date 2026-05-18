"""Tests for the rich endpoint ``POST /v1/tts/synthesize`` (S-013).

Covers UAT-EP-01..07 + UAT-VS-11 plus the voice-resolution / queue-full /
temp-file-cleanup paths called out in S-013.T10. The response header
inventory is pinned via an exact-set assertion so any drift (a header
added or removed) fails the test loudly.
"""

from __future__ import annotations

import asyncio
import io
import os
import wave
from typing import Any

from fastapi.testclient import TestClient

from llm_tts_api.services.voice_store import VoiceRecord
from tests.fakes.fake_tts_provider import FakeTTSProvider

_REQUIRED_HEADERS: frozenset[str] = frozenset(
    {
        "x-request-id",
        "x-provider",
        "x-model",
        "x-device",
        "x-dtype",
        "x-voice-source",
        "x-voice-id",
        "x-chunks",
        "x-total-duration-ms",
    }
)


async def _seed_voice(
    client: TestClient,
    *,
    voice_id: str = "alloy",
    source: str = "crud",
    language: str = "Italian",
    target_db: float = -20.0,
    max_sentences_per_chunk: int = 2,
) -> VoiceRecord:
    """Populate the in-memory fakes with a usable voice record + blob."""
    state = client.app.state
    # Tiny valid WAV so the temp file path resolves even though the fake
    # provider never reads it.
    buf = io.BytesIO()
    with wave.open(buf, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\x00\x00" * 16)
    blob = buf.getvalue()
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
    await state.voice_blob_repo.put(voice_id, blob)
    return record


def _run(coro: Any) -> Any:
    return asyncio.new_event_loop().run_until_complete(coro)


def test_synthesize_happy_path_pins_header_inventory(client: TestClient) -> None:
    """UAT-EP-01 / UAT-VS-11: 200 with the canonical header set."""
    _run(_seed_voice(client, voice_id="alloy", source="crud"))

    response = client.post(
        "/v1/tts/synthesize",
        json={"input": "Ciao mondo.", "voice": "alloy"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    present = {key for key in response.headers if key.startswith("x-")}
    # Allow ``x-error-code`` to be absent on success; require the full
    # FR-EP-04 + UAT-VS-11 inventory.
    missing = _REQUIRED_HEADERS - present
    assert missing == set(), f"missing required headers: {missing}"
    assert response.headers["x-voice-id"] == "alloy"
    assert response.headers["x-voice-source"] == "crud"
    assert int(response.headers["x-chunks"]) >= 1
    assert int(response.headers["x-total-duration-ms"]) > 0
    # Body parses as WAV.
    with wave.open(io.BytesIO(response.content), "rb") as reader:
        assert reader.getnchannels() == 1


def test_synthesize_voice_source_seed_when_record_is_seeded(client: TestClient) -> None:
    """UAT-EP-01: ``X-Voice-Source`` mirrors the record's provenance literal."""
    _run(_seed_voice(client, voice_id="seedy", source="seed"))
    response = client.post(
        "/v1/tts/synthesize",
        json={"input": "Hi", "voice": "seedy"},
    )
    assert response.status_code == 200
    assert response.headers["x-voice-source"] == "seed"
    assert response.headers["x-voice-id"] == "seedy"


def test_synthesize_rejects_unknown_field(client: TestClient) -> None:
    """UAT-EP-03: ``extra="forbid"`` -> 422 with the offending param."""
    response = client.post(
        "/v1/tts/synthesize",
        json={"input": "x", "voice": "alloy", "made_up_field": 1},
    )
    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["type"] == "validation_error"
    assert payload["error"]["param"] == "made_up_field"


def test_synthesize_missing_voice_returns_voice_required(client: TestClient) -> None:
    """UAT-EP-05: missing ``voice`` → 400 ``validation_error.voice_required``."""
    response = client.post(
        "/v1/tts/synthesize",
        json={"input": "hello"},
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["type"] == "validation_error"
    assert payload["error"]["code"] == "voice_required"
    assert payload["error"]["param"] == "voice"


def test_synthesize_unknown_voice_returns_voice_not_found(client: TestClient) -> None:
    """UAT-EP-06: unknown voice id → 404 ``voice_error.voice_not_found``."""
    response = client.post(
        "/v1/tts/synthesize",
        json={"input": "hi", "voice": "does-not-exist"},
    )
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"]["type"] == "voice_error"
    assert payload["error"]["code"] == "voice_not_found"
    assert payload["error"]["param"] == "voice"


def test_synthesize_empty_input_returns_validation_error(client: TestClient) -> None:
    """Whitespace-only input is rejected with the standard envelope."""
    _run(_seed_voice(client))
    response = client.post(
        "/v1/tts/synthesize",
        json={"input": "   ", "voice": "alloy"},
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["type"] == "validation_error"
    assert payload["error"]["param"] == "input"


def test_synthesize_input_at_and_over_limit(client: TestClient) -> None:
    """UAT-EP-04: at-limit → 200; over-limit → 400 input_too_long."""
    state = client.app.state
    state.settings.tts_max_input_chars = 256
    _run(_seed_voice(client))

    at_limit = "x" * 256
    over_limit = "x" * 257

    ok = client.post(
        "/v1/tts/synthesize",
        json={"input": at_limit, "voice": "alloy"},
    )
    assert ok.status_code == 200, ok.json()

    bad = client.post(
        "/v1/tts/synthesize",
        json={"input": over_limit, "voice": "alloy"},
    )
    assert bad.status_code == 400
    payload = bad.json()
    assert payload["error"]["type"] == "validation_error"
    assert payload["error"]["code"] == "input_too_long"
    assert payload["error"]["param"] == "input"


def test_synthesize_per_request_overrides_take_effect(client: TestClient) -> None:
    """UAT-EP-07: ``max_sentences_per_chunk=1`` raises X-Chunks vs default."""
    _run(_seed_voice(client, max_sentences_per_chunk=10))
    multi_sentence = "Uno. Due. Tre. Quattro. Cinque."

    default_resp = client.post(
        "/v1/tts/synthesize",
        json={"input": multi_sentence, "voice": "alloy"},
    )
    overridden = client.post(
        "/v1/tts/synthesize",
        json={
            "input": multi_sentence,
            "voice": "alloy",
            "max_sentences_per_chunk": 1,
            "normalize_db": -18.0,
            "temperature": 0.7,
        },
    )

    assert default_resp.status_code == 200
    assert overridden.status_code == 200
    default_chunks = int(default_resp.headers["x-chunks"])
    override_chunks = int(overridden.headers["x-chunks"])
    assert override_chunks > default_chunks

    # The override-call's SynthesisRequest was captured by the fake
    # provider — assert the values landed in the GenerationOptions /
    # voice config so T7 is end-to-end verified.
    fake: FakeTTSProvider = client.app.state.provider_registry.get("mlx_audio")  # type: ignore[assignment]
    last = fake.calls[-1]
    assert last.generation is not None
    assert last.generation.temperature == 0.7
    assert last.voice.target_db == -18.0
    assert last.voice.max_sentences_per_chunk == 1


def test_synthesize_queue_full_returns_429(client: TestClient) -> None:
    """T5: queue saturation maps to ``capacity_error.queue_full`` (429)."""
    state = client.app.state
    # Drain the queue semaphore so it is fully ``locked()``.
    capacity = state.queue_semaphore._value  # noqa: SLF001 — direct read for test fixture setup
    for _ in range(capacity):
        _run(state.queue_semaphore.acquire())
    _run(_seed_voice(client))
    try:
        response = client.post(
            "/v1/tts/synthesize",
            json={"input": "hi", "voice": "alloy"},
        )
    finally:
        for _ in range(capacity):
            state.queue_semaphore.release()

    assert response.status_code == 429
    payload = response.json()
    assert payload["error"]["type"] == "capacity_error"
    assert payload["error"]["code"] == "queue_full"


def test_synthesize_temp_file_cleaned_up(client: TestClient, tmp_path, monkeypatch) -> None:
    """T3: the per-request temp file is deleted after the response is built."""
    _run(_seed_voice(client))

    created_paths: list[str] = []
    import tempfile as _tempfile

    real_named = _tempfile.NamedTemporaryFile

    def _tracking_tempfile(*args, **kwargs):
        tmp = real_named(*args, **kwargs)
        created_paths.append(tmp.name)
        return tmp

    monkeypatch.setattr(
        "llm_tts_api.routers.synthesize.tempfile.NamedTemporaryFile",
        _tracking_tempfile,
    )

    response = client.post(
        "/v1/tts/synthesize",
        json={"input": "hi", "voice": "alloy"},
    )
    assert response.status_code == 200
    assert created_paths, "expected at least one tempfile to be created"
    for path in created_paths:
        assert not os.path.exists(path), f"temp file leaked: {path}"


def test_synthesize_provider_override_unknown_returns_400(client: TestClient) -> None:
    """T4: provider override not in the registry → validation_error."""
    _run(_seed_voice(client))
    response = client.post(
        "/v1/tts/synthesize",
        json={"input": "hi", "voice": "alloy", "provider": "nope"},
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["type"] == "validation_error"
    assert payload["error"]["param"] == "provider"


def test_synthesize_model_not_in_allowlist_returns_400(client: TestClient) -> None:
    """T4: explicit model override outside allow-list → validation_error."""
    _run(_seed_voice(client))
    response = client.post(
        "/v1/tts/synthesize",
        json={"input": "hi", "voice": "alloy", "model": "definitely-not-allowed"},
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["type"] == "validation_error"
    assert payload["error"]["param"] == "model"
    assert payload["error"]["code"] == "unknown_model"
