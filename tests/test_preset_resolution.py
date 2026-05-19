"""S-028 — Preset resolution + EffectiveSynthesisConfig + header emission.

Covers UAT-PR-01..07 plus a parametrized add-on to the S-018 paired UAT so
``rich(preset='balanced') ↔ OpenAI-default`` is exercised alongside the
master ``test_openai_adapter_parity.py`` case (which stays byte-identical
to its cycle-1 form per NFR-PT-05).

The resolver itself (`llm_tts_api.services.synthesize_service.resolve_preset`)
is pure — it never reads ``app.state`` — so direct unit tests build a
``PresetRegistry`` in-memory and call the function with a stub ``Settings``.
Endpoint-level tests go through ``TestClient`` so header emission +
RICH-only stripping on the OpenAI adapter path are exercised.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import wave
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from llm_tts_api.config import Settings
from llm_tts_api.errors import OpenAIHTTPException
from llm_tts_api.schemas.synthesis import SynthesizeRequest
from llm_tts_api.services.presets.config import (
    PresetDefaults,
    PresetEntry,
    PresetPostprocess,
    PresetRegistry,
)
from llm_tts_api.services.synthesize_service import (
    EffectiveSynthesisConfig,
    resolve_preset,
)
from llm_tts_api.services.voice_store import VoiceRecord

# ---------------------------------------------------------------------------
# Resolver unit-test scaffolding
# ---------------------------------------------------------------------------


def _stub_settings(default_preset: str = "balanced") -> Settings:
    """Build a Settings instance without running ``__post_init__``."""
    settings = object.__new__(Settings)
    settings.tts_default_preset = default_preset
    settings.tts_presets_file = Path("config/presets.json")
    settings.tts_silence_trim_threshold_db = -50.0
    return settings


def _registry(**entries: PresetEntry) -> PresetRegistry:
    return PresetRegistry(_presets=dict(entries))


def _entry(
    *,
    label: str = "stub",
    description: str = "stub",
    **defaults: Any,
) -> PresetEntry:
    return PresetEntry(
        label=label,
        description=description,
        defaults=PresetDefaults(**defaults),
    )


# ---------------------------------------------------------------------------
# UAT-PR-01 — Default preset applies when request omits ``preset``
# ---------------------------------------------------------------------------


def test_uat_pr_01_default_preset_applies_when_request_omits_preset() -> None:
    snapshot = _registry(
        balanced=_entry(
            temperature=0.8,
            top_p=0.95,
            max_sentences_per_chunk=2,
            normalize_db=-20.0,
            response_format="wav",
        ),
    )
    request = SynthesizeRequest(input="hello", voice="alloy")

    cfg = resolve_preset(request, snapshot, _stub_settings("balanced"))

    assert isinstance(cfg, EffectiveSynthesisConfig)
    assert cfg.preset_name == "balanced"
    assert cfg.temperature == 0.8
    assert cfg.top_p == 0.95
    assert cfg.max_sentences_per_chunk == 2
    assert cfg.normalize_db == -20.0
    assert cfg.response_format == "wav"
    assert cfg.ignored_knobs == ()
    assert dict(cfg.effective_overrides) == {}


# ---------------------------------------------------------------------------
# UAT-PR-02 — Named preset applies its defaults (quality → flac)
# ---------------------------------------------------------------------------


def test_uat_pr_02_named_preset_quality_pulls_flac_into_effective_config() -> None:
    snapshot = _registry(
        balanced=_entry(temperature=0.8, top_p=0.95, response_format="wav"),
        quality=_entry(
            temperature=0.8,
            top_p=0.95,
            max_sentences_per_chunk=3,
            normalize_db=-20.0,
            response_format="flac",
            postprocess=PresetPostprocess(rms_normalize=True, silence_trim=True),
        ),
    )
    request = SynthesizeRequest(input="hi", voice="alloy", preset="quality")

    cfg = resolve_preset(request, snapshot, _stub_settings("balanced"))

    assert cfg.preset_name == "quality"
    assert cfg.response_format == "flac"
    assert cfg.postprocess is not None
    assert cfg.postprocess.rms_normalize is True
    assert cfg.postprocess.silence_trim is True
    # Pipeline still wav-only until S-033 → flac is soft-ignored.
    assert "response_format" in cfg.ignored_knobs


# ---------------------------------------------------------------------------
# UAT-PR-03 — Unknown preset → 400 validation_error.preset_unknown
# ---------------------------------------------------------------------------


def test_uat_pr_03_unknown_preset_raises_validation_error() -> None:
    snapshot = _registry(
        balanced=_entry(temperature=0.8, top_p=0.95, response_format="wav"),
    )
    request = SynthesizeRequest(input="hi", voice="alloy", preset="nonexistent")

    with pytest.raises(OpenAIHTTPException) as exc_info:
        resolve_preset(request, snapshot, _stub_settings("balanced"))

    exc = exc_info.value
    assert exc.status_code == 400
    assert exc.error.type == "validation_error"
    assert exc.error.code == "preset_unknown"
    assert exc.error.param == "preset"
    # Available preset names listed in the message per FR-PR-07.
    assert "balanced" in exc.error.message
    assert "nonexistent" in exc.error.message


def test_uat_pr_03_unknown_preset_via_http_returns_400(client: TestClient) -> None:
    _run(_seed_voice(client))
    response = client.post(
        "/v1/tts/synthesize",
        json={
            "input": "hello",
            "voice": "alloy",
            "provider": "mlx_audio",
            "preset": "nonexistent",
        },
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "validation_error"
    assert body["error"]["code"] == "preset_unknown"
    assert body["error"]["param"] == "preset"


# ---------------------------------------------------------------------------
# UAT-PR-04 — Explicit field overrides preset pin + WARN log + header
# ---------------------------------------------------------------------------


def test_uat_pr_04_explicit_overrides_preset_pin_and_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    snapshot = _registry(
        balanced=_entry(
            temperature=0.8,
            top_p=0.95,
            max_sentences_per_chunk=2,
            normalize_db=-20.0,
            response_format="wav",
        ),
    )
    request = SynthesizeRequest(
        input="hi",
        voice="alloy",
        preset="balanced",
        temperature=0.5,
    )

    with caplog.at_level(logging.WARNING, logger="llm_tts_api.services.synthesize_service"):
        cfg = resolve_preset(request, snapshot, _stub_settings("balanced"))

    assert cfg.temperature == 0.5
    assert "temperature" in dict(cfg.effective_overrides)
    assert dict(cfg.effective_overrides)["temperature"] == "0.5"
    # WARN log per FR-PR-08.
    assert any(
        "preset override" in rec.message and "temperature" in rec.message for rec in caplog.records
    )


def test_uat_pr_04_x_preset_effective_header_carries_resolved_value(
    client: TestClient,
) -> None:
    _run(_seed_voice(client))
    response = client.post(
        "/v1/tts/synthesize",
        json={
            "input": "hello",
            "voice": "alloy",
            "provider": "mlx_audio",
            "preset": "balanced",
            "temperature": 0.5,
        },
    )
    assert response.status_code == 200, response.text
    header = response.headers.get("x-preset-effective")
    assert header is not None
    assert header.startswith("balanced(")
    assert "temperature=0.5" in header


# ---------------------------------------------------------------------------
# UAT-PR-05 — Provider-incompatible knobs soft-ignored + header
# ---------------------------------------------------------------------------


def test_uat_pr_05_soft_ignored_format_surfaces_in_header(client: TestClient) -> None:
    _run(_seed_voice(client))
    response = client.post(
        "/v1/tts/synthesize",
        json={
            "input": "hello",
            "voice": "alloy",
            "provider": "mlx_audio",
            "preset": "quality",
        },
    )
    assert response.status_code == 200, response.text
    ignored = response.headers.get("x-preset-ignored-knobs")
    assert ignored is not None
    assert "response_format" in ignored


def test_uat_pr_05_no_ignored_header_when_empty(client: TestClient) -> None:
    """``X-Preset-Ignored-Knobs`` is emitted only when non-empty (FR-PR-09)."""
    _run(_seed_voice(client))
    response = client.post(
        "/v1/tts/synthesize",
        json={
            "input": "hello",
            "voice": "alloy",
            "provider": "mlx_audio",
            "preset": "balanced",
        },
    )
    assert response.status_code == 200
    assert "x-preset-ignored-knobs" not in {k.lower() for k in response.headers}


# ---------------------------------------------------------------------------
# UAT-PR-06 — OpenAI path always uses TTS_DEFAULT_PRESET (S-018 byte-identity)
# ---------------------------------------------------------------------------


def test_uat_pr_06_openai_path_strips_preset_headers(client: TestClient) -> None:
    """OpenAI-adapter response MUST NOT leak rich preset headers."""
    _run(_seed_voice(client))
    response = client.post(
        "/v1/audio/speech",
        json={
            "model": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
            "input": "hello",
            "voice": "alloy",
            "response_format": "wav",
            "provider": "mlx_audio",
        },
    )
    assert response.status_code == 200, response.text
    header_set = {k.lower() for k in response.headers}
    assert "x-preset-effective" not in header_set
    assert "x-preset-ignored-knobs" not in header_set


def test_uat_pr_06_paired_byte_identity_with_explicit_balanced(client: TestClient) -> None:
    """rich(preset='balanced') ↔ OpenAI-default — byte-identical body
    (parametrized add-on to S-018's paired UAT; NFR-PT-05).
    """
    _run(_seed_voice(client))
    openai_resp = client.post(
        "/v1/audio/speech",
        json={
            "model": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
            "input": "Uno. Due. Tre.",
            "voice": "alloy",
            "response_format": "wav",
            "provider": "mlx_audio",
        },
    )
    rich_resp = client.post(
        "/v1/tts/synthesize",
        json={
            "model": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
            "input": "Uno. Due. Tre.",
            "voice": "alloy",
            "response_format": "wav",
            "provider": "mlx_audio",
            "preset": "balanced",
        },
    )
    assert openai_resp.status_code == 200, openai_resp.text
    assert rich_resp.status_code == 200, rich_resp.text
    openai_digest = hashlib.sha256(openai_resp.content).hexdigest()
    rich_digest = hashlib.sha256(rich_resp.content).hexdigest()
    assert openai_digest == rich_digest, (
        "S-028 byte-identity regression: rich(preset=balanced) ≠ OpenAI-default "
        f"(rich={rich_digest} openai={openai_digest})"
    )


# ---------------------------------------------------------------------------
# UAT-PR-07 — Extra ``preset`` field on OpenAI request rejected with 422
# ---------------------------------------------------------------------------


def test_uat_pr_07_openai_request_rejects_preset_field(client: TestClient) -> None:
    _run(_seed_voice(client))
    response = client.post(
        "/v1/audio/speech",
        json={
            "model": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
            "input": "hello",
            "voice": "alloy",
            "response_format": "wav",
            "provider": "mlx_audio",
            "preset": "quality",  # extra="forbid" → 422
        },
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"]["type"] == "validation_error"


# ---------------------------------------------------------------------------
# UAT-PR-18 — HF-2: language / number_lang / voice flow through preset defaults
# (FR-PR-03 amended). Explicit request fields still override per BR-10.
# ---------------------------------------------------------------------------


def test_uat_pr_18_preset_defaults_language_number_lang_voice_apply() -> None:
    """Happy path — preset-pinned ``language`` / ``number_lang`` / ``voice``
    flow through ``EffectiveSynthesisConfig`` when the request omits them."""
    snapshot = _registry(
        custom=_entry(
            temperature=0.7,
            top_p=0.9,
            response_format="wav",
            language="en",
            number_lang="en",
            voice="alloy",
        ),
    )
    request = SynthesizeRequest(input="hello", preset="custom")

    cfg = resolve_preset(request, snapshot, _stub_settings("custom"))

    assert cfg.language == "en"
    assert cfg.number_lang == "en"
    assert cfg.voice == "alloy"
    assert dict(cfg.effective_overrides) == {}


def test_uat_pr_18_explicit_request_fields_override_preset_pins(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Override regression — explicit request fields beat preset pins per BR-10."""
    snapshot = _registry(
        custom=_entry(
            language="en",
            number_lang="en",
            voice="alloy",
            response_format="wav",
        ),
    )
    request = SynthesizeRequest(
        input="hello",
        preset="custom",
        language="it",
        number_lang="it",
        voice="echo",
    )

    with caplog.at_level(logging.WARNING, logger="llm_tts_api.services.synthesize_service"):
        cfg = resolve_preset(request, snapshot, _stub_settings("custom"))

    assert cfg.language == "it"
    assert cfg.number_lang == "it"
    assert cfg.voice == "echo"
    overrides = dict(cfg.effective_overrides)
    assert overrides["language"] == "it"
    assert overrides["number_lang"] == "it"
    assert overrides["voice"] == "echo"
    messages = " ".join(rec.message for rec in caplog.records)
    assert "language" in messages
    assert "number_lang" in messages
    assert "voice" in messages


# ---------------------------------------------------------------------------
# HF-2 shipped-preset regressions — guard config/presets.json against drift.
# ---------------------------------------------------------------------------


def test_hf2_quality_preset_pins_provider_and_model() -> None:
    """Quality preset MUST demonstrate provider+model out of the box (HF-2)."""
    from llm_tts_api.services.presets.config import load_preset_registry

    registry = load_preset_registry(Path("config/presets.json"))
    quality = registry.get("quality")
    assert quality is not None
    assert quality.defaults.provider == "mlx_audio"
    assert quality.defaults.model == "Qwen/Qwen3-TTS-12Hz-0.6B-Base"


def test_hf2_balanced_preset_leaves_provider_and_model_unset() -> None:
    """Balanced preset MUST stay default for A-PR-1 byte-compat (HF-2 / NFR-PT-05)."""
    from llm_tts_api.services.presets.config import load_preset_registry

    registry = load_preset_registry(Path("config/presets.json"))
    balanced = registry.get("balanced")
    assert balanced is not None
    assert balanced.defaults.provider is None
    assert balanced.defaults.model is None


# ---------------------------------------------------------------------------
# Fixture helpers shared with the parity case
# ---------------------------------------------------------------------------


def _run(coro: Any) -> Any:
    return asyncio.new_event_loop().run_until_complete(coro)


async def _seed_voice(client: TestClient, *, voice_id: str = "alloy") -> VoiceRecord:
    state = client.app.state
    buf = io.BytesIO()
    with wave.open(buf, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\x00\x00" * 16)
    record = VoiceRecord(
        id=voice_id,
        transcript="ref text",
        language="Italian",
        consent_acknowledged=True,
        source="crud",  # type: ignore[arg-type]
    )
    await state.voice_metadata_repo.create(record)
    await state.voice_blob_repo.put(voice_id, buf.getvalue())
    return record
