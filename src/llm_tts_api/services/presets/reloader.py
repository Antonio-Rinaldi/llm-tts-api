"""Validating-before-swap preset hot-reloader (S-029 T2).

Watches ``TTS_PRESETS_FILE`` via :class:`ConfigWatcher` and, on every
detected change, re-runs the cycle-2 startup validation chain MINUS the
permission posture check (NFR-OP-PR-3 / RISK-PR-3 — permission posture
is startup-only; the hot-reload path accepts the documented ``mv`` +
``chmod`` race). On full validation success, the new
:class:`PresetRegistry` is handed to the ``on_swap`` callback which the
lifespan binds to ``app.state.preset_registry`` for atomic replacement.

On ANY validation failure the prior registry stays live and a single
WARN line is emitted with the cycle-2 ``config_error.*`` code + field-
path detail (NFR-SE-10 attack-tolerant: a bad edit cannot bring the
service down). Future operator edits resume the normal cadence.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import TYPE_CHECKING

from llm_tts_api.errors import (
    CONFIG_ERROR_PRESET_PROVIDER_INVALID,
    CONFIG_ERROR_PRESETS_INVALID,
)
from llm_tts_api.services.config_watcher import ConfigWatcher
from llm_tts_api.services.presets.config import (
    PresetProviderInvalidError,
    PresetRegistry,
    PresetsInvalidError,
    load_preset_registry,
    validate_preset_providers,
)
from llm_tts_api.services.presets.startup import _allow_lists_from_settings

if TYPE_CHECKING:
    from llm_tts_api.config import Settings
    from llm_tts_api.services.tts_providers.registry import TTSProviderRegistry

logger = logging.getLogger(__name__)

RELOAD_FAILED_LOG_KEY = "preset_reload_failed"


def force_polling_from_env() -> bool:
    """Return ``True`` when ``TTS_PRESETS_WATCH_FORCE_POLLING`` is truthy (RISK-3)."""
    raw = os.environ.get("TTS_PRESETS_WATCH_FORCE_POLLING", "").strip().lower()
    return raw in {"1", "true", "yes"}


class PresetRegistryReloader:
    """Watch the presets file and swap ``app.state.preset_registry`` on valid edits."""

    def __init__(
        self,
        *,
        settings: Settings,
        provider_registry: TTSProviderRegistry | None,
        on_swap: Callable[[PresetRegistry], None],
        force_polling: bool = False,
    ) -> None:
        self._settings = settings
        self._provider_registry = provider_registry
        self._on_swap = on_swap
        self._force_polling = force_polling

    @property
    def presets_file_path(self) -> object:
        """Expose the watched path (mostly for diagnostics / log lines)."""
        return self._settings.tts_presets_file

    async def watch(self) -> None:
        """Long-running task: re-validate on every file change."""
        watcher = ConfigWatcher(
            path=self._settings.tts_presets_file,
            on_change=self.reload_once,
            force_polling=self._force_polling,
        )
        await watcher.watch()

    async def reload_once(self) -> None:
        """Re-run validation + swap once. Logs WARN on any failure; never raises.

        Permission check is intentionally skipped per NFR-OP-PR-3 (RISK-PR-3).
        """
        path = self._settings.tts_presets_file
        try:
            registry = load_preset_registry(path)
        except PresetsInvalidError as exc:
            logger.warning(
                "%s code=%s reason=%s path=%s",
                RELOAD_FAILED_LOG_KEY,
                CONFIG_ERROR_PRESETS_INVALID,
                exc,
                path,
            )
            return

        default_name = self._settings.tts_default_preset
        if default_name not in registry.names():
            logger.warning(
                "%s code=%s reason=default_preset_unknown default=%s defined=%s path=%s",
                RELOAD_FAILED_LOG_KEY,
                CONFIG_ERROR_PRESETS_INVALID,
                default_name,
                sorted(registry.names()),
                path,
            )
            return

        allow_lists = _allow_lists_from_settings(self._settings, self._provider_registry)
        try:
            validate_preset_providers(registry, allow_lists)
        except PresetProviderInvalidError as exc:
            logger.warning(
                "%s code=%s reason=%s path=%s",
                RELOAD_FAILED_LOG_KEY,
                CONFIG_ERROR_PRESET_PROVIDER_INVALID,
                exc,
                path,
            )
            return

        logger.info(
            "preset_registry_reloaded path=%s presets=%s",
            path,
            sorted(registry.names()),
        )
        self._on_swap(registry)
