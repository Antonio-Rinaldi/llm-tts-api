"""Generic config-file watcher primitive (S-029 T1).

Extracted from the cycle-1 S-011 voice-map ingestion watcher loop so that
both ``VoiceSeedIngestor`` and the new :class:`PresetRegistryReloader`
share one implementation. The shape is intentionally small:

* Watch the parent directory (not the file itself) — editors that "save"
  by ``rename`` + ``replace`` would otherwise drop their event for a
  single-file watch.
* Filter the change stream to the resolved target path before invoking
  the callback.
* Honour ``force_polling`` for Docker bind-mounts where inotify is
  unreliable (RISK-3).
* Swallow callback exceptions so a downstream bug never crashes the
  watcher task; the original exception is logged.

The watcher itself is I/O only — no parsing, no validation. Callers wire
their own parse-then-swap logic into ``on_change`` (cycle-1 seed
ingestion, cycle-2 preset reloader).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

logger = logging.getLogger(__name__)


class ConfigWatcher:
    """Watch a single file and invoke an async callback on every change.

    A ``None`` path makes :meth:`watch` an immediate clean no-op — used
    by the cycle-1 seed ingestor (FR-VM-05: unset is valid) and is
    convenient for the cycle-2 reloader's tests too.
    """

    def __init__(
        self,
        *,
        path: Path | None,
        on_change: Callable[[], Awaitable[None]],
        force_polling: bool = False,
        step_ms: int = 200,
    ) -> None:
        self._path = path
        self._on_change = on_change
        self._force_polling = force_polling
        self._step_ms = step_ms

    async def watch(self) -> None:
        """Long-running task: invoke the callback on every detected file change."""
        path = self._path
        if path is None:
            return
        try:
            from watchfiles import awatch
        except ImportError:  # pragma: no cover - watchfiles is a hard dep
            logger.warning("watchfiles unavailable; config hot reload disabled path=%s", path)
            return

        watch_root = path.parent if str(path.parent) else Path(".")
        target = path.resolve()
        try:
            async for changes in awatch(
                watch_root,
                force_polling=self._force_polling,
                step=self._step_ms,
            ):
                # ``awatch`` streams every change in the directory; compare
                # on resolved paths because the watcher may emit absolute
                # paths from a different symlink chain than ``path``.
                touched = any(Path(p).resolve() == target for _change_type, p in changes)
                if not touched:
                    continue
                try:
                    await self._on_change()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - callback bug must not crash watcher
                    logger.exception("config_watcher: on_change callback raised path=%s", path)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the watcher must never crash startup
            logger.exception("config_watcher: watcher loop crashed path=%s", path)
