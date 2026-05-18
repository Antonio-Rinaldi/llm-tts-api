"""S-025 voice CRUD endpoint tests — UAT-VS-01..10.

Uses the standard ``client`` fixture (in-memory fake repos in ``app.state``).
"""

from __future__ import annotations

import json
import struct

from fastapi.testclient import TestClient


def _wav_bytes(payload_len: int = 16) -> bytes:
    """Minimal RIFF/WAVE byte sequence with ``payload_len`` of trailing data.

    Real WAV files have fmt + data subchunks; for magic-bytes inspection only
    the first 12 bytes (``RIFF<size>WAVE``) matter, but a longer body is more
    realistic. The total length is 12 + payload_len.
    """
    riff_chunk_size = 4 + payload_len
    header = b"RIFF" + struct.pack("<I", riff_chunk_size) + b"WAVE"
    return header + (b"\x00" * payload_len)


def _metadata(**overrides: object) -> str:
    base = {
        "id": "myvoice",
        "transcript": "Hello world",
        "language": "Italian",
        "consent_acknowledged": True,
    }
    base.update(overrides)
    return json.dumps(base)


def _post_voice(
    client: TestClient,
    *,
    metadata: str | None = None,
    audio_bytes: bytes | None = None,
    content_type: str = "audio/wav",
) -> object:
    """Submit a multipart POST against /v1/tts/voices."""
    return client.post(
        "/v1/tts/voices",
        data={"metadata": metadata if metadata is not None else _metadata()},
        files={
            "audio": (
                "voice.wav",
                audio_bytes if audio_bytes is not None else _wav_bytes(),
                content_type,
            ),
        },
    )


def test_create_voice_happy_path_uat_vs_01(client: TestClient) -> None:
    response = _post_voice(client)
    assert response.status_code == 201
    body = response.json()
    assert body["id"] == "myvoice"
    assert body["language"] == "Italian"
    assert body["source"] == "crud"
    assert body["consent_acknowledged"] is True
    # FR-VS-06 / FR-VS-07: no path/URI fields exposed.
    assert "ref_audio_path" not in body
    assert "path" not in body

    list_response = client.get("/v1/tts/voices")
    assert list_response.status_code == 200
    items = list_response.json()["data"]
    assert [item["id"] for item in items] == ["myvoice"]
    assert "transcript" not in items[0]


def test_create_without_consent_returns_400_uat_vs_02(client: TestClient) -> None:
    response = _post_voice(client, metadata=_metadata(consent_acknowledged=False))
    assert response.status_code == 400
    err = response.json()["error"]
    assert err["type"] == "validation_error"
    assert err["code"] == "consent_required"


def test_duplicate_id_returns_400_uat_vs_03(client: TestClient) -> None:
    first = _post_voice(client)
    assert first.status_code == 201
    second = _post_voice(client)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "voice_id_exists"


def test_oversized_audio_rejected_uat_vs_04(
    client: TestClient,
) -> None:
    # Drop the cap to something we can blow past with a small fake payload.
    client.app.state.settings.tts_refaudio_max_bytes = 100  # type: ignore[attr-defined]
    response = _post_voice(client, audio_bytes=_wav_bytes(payload_len=200))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "ref_audio_invalid"


def test_corrupt_magic_bytes_rejected_uat_vs_05(client: TestClient) -> None:
    response = _post_voice(client, audio_bytes=b"NOT_A_WAV_HEADER____PAYLOAD")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "ref_audio_invalid"


def test_wrong_content_type_rejected(client: TestClient) -> None:
    response = _post_voice(client, content_type="text/plain")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "ref_audio_invalid"


def test_path_traversal_id_rejected_uat_vs_06(client: TestClient) -> None:
    response = _post_voice(client, metadata=_metadata(id="../etc/passwd"))
    assert response.status_code in {400, 422}
    body = response.json()["error"]
    assert body["type"] == "validation_error"


def test_get_metadata_uat_vs_07(client: TestClient) -> None:
    _post_voice(client)
    response = client.get("/v1/tts/voices/myvoice")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "myvoice"
    assert body["transcript"] == "Hello world"
    assert response.headers["content-type"].startswith("application/json")


def test_get_metadata_unknown_returns_404(client: TestClient) -> None:
    response = client.get("/v1/tts/voices/missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "voice_not_found"


def test_get_audio_uat_vs_08(client: TestClient) -> None:
    _post_voice(client)
    response = client.get("/v1/tts/voices/myvoice/audio")
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["x-voice-id"] == "myvoice"
    assert response.headers["x-voice-source"] == "crud"
    assert "x-content-sha256" in response.headers
    assert response.content[:4] == b"RIFF"


def test_get_audio_blob_missing_uat_vs_08b(client: TestClient) -> None:
    _post_voice(client)
    # Drop the blob out-of-band but keep the metadata.
    import asyncio as _asyncio

    blob_repo = client.app.state.voice_blob_repo  # type: ignore[attr-defined]
    _asyncio.new_event_loop().run_until_complete(blob_repo.delete("myvoice"))

    response = client.get("/v1/tts/voices/myvoice/audio")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "voice_blob_missing"


def test_update_voice_replaces_metadata_and_audio_uat_vs_09(client: TestClient) -> None:
    _post_voice(client)
    new_metadata = json.dumps(
        {
            "transcript": "Updated transcript",
            "language": "English",
            "consent_acknowledged": True,
        }
    )
    new_audio = _wav_bytes(payload_len=32)
    response = client.put(
        "/v1/tts/voices/myvoice",
        data={"metadata": new_metadata},
        files={"audio": ("voice.wav", new_audio, "audio/wav")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["transcript"] == "Updated transcript"
    assert body["language"] == "English"

    audio = client.get("/v1/tts/voices/myvoice/audio")
    assert audio.status_code == 200
    assert audio.content == new_audio


def test_update_voice_metadata_only(client: TestClient) -> None:
    _post_voice(client)
    original_audio = client.get("/v1/tts/voices/myvoice/audio").content
    new_metadata = json.dumps(
        {
            "transcript": "Updated only",
            "language": "Italian",
            "consent_acknowledged": True,
        }
    )
    response = client.put(
        "/v1/tts/voices/myvoice",
        data={"metadata": new_metadata},
    )
    assert response.status_code == 200
    assert response.json()["transcript"] == "Updated only"
    audio = client.get("/v1/tts/voices/myvoice/audio")
    assert audio.content == original_audio


def test_update_missing_voice_returns_404(client: TestClient) -> None:
    metadata = json.dumps(
        {
            "transcript": "x",
            "language": "Italian",
            "consent_acknowledged": True,
        }
    )
    response = client.put(
        "/v1/tts/voices/missing",
        data={"metadata": metadata},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "voice_not_found"


def test_delete_voice_uat_vs_10(client: TestClient) -> None:
    _post_voice(client)
    response = client.delete("/v1/tts/voices/myvoice")
    assert response.status_code == 204
    assert client.get("/v1/tts/voices/myvoice").status_code == 404


def test_delete_missing_voice_returns_404(client: TestClient) -> None:
    response = client.delete("/v1/tts/voices/myvoice")
    assert response.status_code == 404


def test_audio_voices_remains_reserved_stub(client: TestClient) -> None:
    """The OpenAI-compat reservation at /v1/audio/voices stays a 501 stub (SRS §4.4)."""
    response = client.post("/v1/audio/voices")
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "not_implemented"


def test_metadata_invalid_json(client: TestClient) -> None:
    response = client.post(
        "/v1/tts/voices",
        data={"metadata": "not json"},
        files={"audio": ("v.wav", _wav_bytes(), "audio/wav")},
    )
    assert response.status_code == 400
    body = response.json()["error"]
    assert body["param"] == "metadata"


def test_metadata_extra_field_rejected(client: TestClient) -> None:
    response = _post_voice(client, metadata=_metadata(unexpected="x"))
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "validation_error"
