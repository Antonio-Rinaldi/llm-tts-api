"""S-029 T1 — ConfigWatcher generic primitive (extracted from cycle-1 S-011).

The watcher MUST:
* Detect a single-file change in the watched directory within ~2 s (NFR-PR-03
  inherits NFR-OP-05 cadence — same 200 ms step constant as cycle-1).
* Filter changes to only the target file (the parent directory may emit
  events for unrelated files).
* Invoke the supplied async callback once per detected touch.
* Honour ``force_polling=True`` for Docker bind-mount environments (RISK-3).
* Be a clean no-op when handed a ``None`` path (treat "not configured" the
  same as cycle-1 S-011 voice-map).
* Swallow exceptions from the watcher loop without crashing — a watcher
  failure must never bring the service down.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from llm_tts_api.services.config_watcher import ConfigWatcher


async def test_watch_invokes_callback_on_file_change(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    target.write_text("{}")

    calls: list[int] = []

    async def on_change() -> None:
        calls.append(1)

    watcher = ConfigWatcher(path=target, on_change=on_change, force_polling=True)
    task = asyncio.create_task(watcher.watch())
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not calls:
            target.write_text(f'{{"v":{time.monotonic()}}}')
            await asyncio.sleep(0.1)
        assert calls, "ConfigWatcher never observed the file change"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_watch_with_none_path_returns_immediately() -> None:
    async def on_change() -> None:  # pragma: no cover - never called
        raise AssertionError("callback should not fire with None path")

    watcher = ConfigWatcher(path=None, on_change=on_change)
    await asyncio.wait_for(watcher.watch(), timeout=0.5)


async def test_watch_ignores_unrelated_directory_changes(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("{}")
    other = tmp_path / "other.json"

    fired = asyncio.Event()

    async def on_change() -> None:
        fired.set()

    watcher = ConfigWatcher(path=target, on_change=on_change, force_polling=True)
    task = asyncio.create_task(watcher.watch())
    try:
        # Touch unrelated file repeatedly — should NOT trigger callback.
        for _ in range(5):
            other.write_text(f"unrelated-{time.monotonic()}")
            await asyncio.sleep(0.1)
        assert not fired.is_set()
        # Now touch the actual target — should trigger.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not fired.is_set():
            target.write_text(f'{{"v":{time.monotonic()}}}')
            await asyncio.sleep(0.1)
        assert fired.is_set()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_watch_callback_exception_does_not_crash_watcher(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    target = tmp_path / "config.json"
    target.write_text("{}")

    state = {"calls": 0}

    async def on_change() -> None:
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("simulated callback failure")

    watcher = ConfigWatcher(path=target, on_change=on_change, force_polling=True)
    task = asyncio.create_task(watcher.watch())
    try:
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline and state["calls"] < 2:
            target.write_text(f'{{"v":{time.monotonic()}}}')
            await asyncio.sleep(0.25)
        assert state["calls"] >= 2, (
            f"watcher should keep running after a callback error (calls={state['calls']})"
        )
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
