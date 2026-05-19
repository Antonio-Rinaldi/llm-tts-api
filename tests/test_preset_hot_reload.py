"""S-029 — PresetRegistryReloader (UAT-PR-08, UAT-PR-09, UAT-PR-15).

Verifies the cycle-2 hot-reload contract:

* UAT-PR-08: a valid new ``presets.json`` is picked up within ≤2 s and
  the registry slot is replaced atomically.
* UAT-PR-09: a request that captured a snapshot at request-start uses
  that snapshot for its whole lifetime, even if ``app.state.preset_registry``
  is replaced mid-flight (NFR-PR-04). Subsequent requests see the new one.
* UAT-PR-15: an invalid new ``presets.json`` is rejected; the reloader
  WARN-logs the field-path error and the prior registry remains live
  (NFR-SE-10 validating-before-swap).

The permission check is intentionally NOT re-run on reload (RISK-PR-3 /
NFR-OP-PR-3 documented limitation). A test pins this behavior so a
future refactor doesn't accidentally regress.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

import pytest

from llm_tts_api.config import Settings
from llm_tts_api.services.presets import PresetRegistry, initialize_preset_registry
from llm_tts_api.services.presets.reloader import PresetRegistryReloader

_VALID_PRESETS = {
    "balanced": {
        "label": "Balanced",
        "description": "Cycle-1 baseline.",
        "defaults": {
            "temperature": 0.8,
            "top_p": 0.95,
            "max_sentences_per_chunk": 2,
        },
    },
    "fast": {
        "label": "Fast",
        "description": "Snappy.",
        "defaults": {
            "temperature": 0.6,
            "max_sentences_per_chunk": 1,
        },
    },
}


def _write_presets(path: Path, body: dict[str, object]) -> None:
    path.write_text(json.dumps(body))


def _make_settings(presets_file: Path, default: str = "balanced") -> Settings:
    os.environ["TTS_PRESETS_FILE"] = str(presets_file)
    os.environ["TTS_DEFAULT_PRESET"] = default
    return Settings()


@pytest.fixture(autouse=True)
def _restore_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Each test sets its own TTS_PRESETS_FILE / TTS_DEFAULT_PRESET via os.environ;
    # let monkeypatch tear them down so suite ordering is irrelevant.
    monkeypatch.delenv("TTS_PRESETS_FILE", raising=False)
    monkeypatch.delenv("TTS_DEFAULT_PRESET", raising=False)


# --- T2 / UAT-PR-08 --------------------------------------------------------


async def test_reload_swaps_registry_on_valid_change(tmp_path: Path) -> None:
    presets = tmp_path / "presets.json"
    _write_presets(presets, _VALID_PRESETS)
    settings = _make_settings(presets)
    initial = initialize_preset_registry(settings, provider_registry=None)

    swapped: list[PresetRegistry] = []

    def on_swap(new_registry: PresetRegistry) -> None:
        swapped.append(new_registry)

    reloader = PresetRegistryReloader(
        settings=settings,
        provider_registry=None,
        on_swap=on_swap,
        force_polling=True,
    )

    task = asyncio.create_task(reloader.watch())
    try:
        new_body = {
            **_VALID_PRESETS,
            "quality": {
                "label": "Quality",
                "description": "FLAC.",
                "defaults": {"response_format": "flac"},
            },
        }
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not swapped:
            _write_presets(presets, new_body)
            await asyncio.sleep(0.1)
        assert swapped, "reloader did not detect the valid file change within 2s"
        latest = swapped[-1]
        assert "quality" in latest.names()
        assert latest.get("quality") is not None
        # The initial registry is unchanged (immutability invariant).
        assert "quality" not in initial.names()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# --- T2 / UAT-PR-15 --------------------------------------------------------


async def test_invalid_reload_keeps_prior_registry_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    presets = tmp_path / "presets.json"
    _write_presets(presets, _VALID_PRESETS)
    settings = _make_settings(presets)
    initialize_preset_registry(settings, provider_registry=None)

    swap_calls: list[PresetRegistry] = []

    def on_swap(new_registry: PresetRegistry) -> None:  # pragma: no cover - never called
        swap_calls.append(new_registry)

    reloader = PresetRegistryReloader(
        settings=settings,
        provider_registry=None,
        on_swap=on_swap,
        force_polling=True,
    )

    with caplog.at_level(logging.WARNING):
        # Direct invocation of the reload routine — same code path used by
        # the watcher, but synchronous so we can pin the assertion without
        # depending on watcher cadence.
        _write_presets(presets, {"bad": {"label": ""}})  # missing description + defaults
        await reloader.reload_once()

    assert swap_calls == []
    assert any(
        "preset_reload_failed" in r.message or "config_error.presets_invalid" in r.message
        for r in caplog.records
    ), caplog.text


async def test_reload_skips_permission_check(tmp_path: Path) -> None:
    """RISK-PR-3: permission posture is startup-only; reload MUST NOT re-check it."""
    presets = tmp_path / "presets.json"
    _write_presets(presets, _VALID_PRESETS)
    settings = _make_settings(presets)
    initialize_preset_registry(settings, provider_registry=None)

    swapped: list[PresetRegistry] = []

    reloader = PresetRegistryReloader(
        settings=settings,
        provider_registry=None,
        on_swap=swapped.append,
        force_polling=True,
    )

    # Tamper permissions to world-writable — startup would have refused,
    # but the reloader documented contract is "permission check is startup-only".
    presets.chmod(0o666)
    new_body = {
        **_VALID_PRESETS,
        "extra": {
            "label": "Extra",
            "description": "Added on reload.",
            "defaults": {},
        },
    }
    _write_presets(presets, new_body)
    await reloader.reload_once()

    # Restore safe perms so the temp cleanup is not racy on shared CI hosts.
    presets.chmod(0o600)

    assert swapped, "reload should have swapped despite tampered permissions"
    assert "extra" in swapped[-1].names()


async def test_reload_rejects_unknown_default_preset(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    presets = tmp_path / "presets.json"
    _write_presets(presets, _VALID_PRESETS)
    settings = _make_settings(presets, default="balanced")
    initialize_preset_registry(settings, provider_registry=None)

    swap_calls: list[PresetRegistry] = []
    reloader = PresetRegistryReloader(
        settings=settings,
        provider_registry=None,
        on_swap=swap_calls.append,
        force_polling=True,
    )

    # New file drops "balanced" — TTS_DEFAULT_PRESET no longer resolves.
    _write_presets(presets, {"fast": _VALID_PRESETS["fast"]})
    with caplog.at_level(logging.WARNING):
        await reloader.reload_once()
    assert swap_calls == []
    assert any("preset_reload_failed" in r.message for r in caplog.records), caplog.text


# --- T3 / UAT-PR-09 — in-flight snapshot pattern ---------------------------


async def test_in_flight_snapshot_survives_mid_flight_swap(tmp_path: Path) -> None:
    """A reference captured at request-start MUST NOT see a mid-flight swap.

    S-028 owns ``resolve_preset``; this test pins the call-site invariant
    that S-029 establishes: pass the captured snapshot through, never
    re-read ``app.state.preset_registry`` once a request begins.
    """
    presets = tmp_path / "presets.json"
    _write_presets(presets, _VALID_PRESETS)
    settings = _make_settings(presets)

    # Simulate the lifespan: stash the initial registry on a mutable holder
    # standing in for ``app.state``.
    class _State:
        preset_registry: PresetRegistry

    state = _State()
    state.preset_registry = initialize_preset_registry(settings, provider_registry=None)
    initial_snapshot = state.preset_registry  # what a request captures at entry

    reloader = PresetRegistryReloader(
        settings=settings,
        provider_registry=None,
        on_swap=lambda r: setattr(state, "preset_registry", r),
        force_polling=True,
    )

    # Mid-flight swap: drop "fast" from the file.
    _write_presets(presets, {"balanced": _VALID_PRESETS["balanced"]})
    await reloader.reload_once()

    # The slot moved on — new requests will see the post-swap shape.
    assert "fast" not in state.preset_registry.names()
    # …but the snapshot the in-flight request grabbed at entry MUST still
    # resolve "fast" because it holds the prior (frozen) registry object.
    assert "fast" in initial_snapshot.names()
    assert initial_snapshot.get("fast") is not None
    # And the two are distinct objects — the swap really did rotate state.
    assert initial_snapshot is not state.preset_registry
