"""Bounded LRU cache for loaded TTS model objects.

Implements the S-008 model-cache contract from FR-CA-01..04 (SRS §4.7):

* **FR-CA-01** — entries keyed by ``(provider, model_id)``; eviction policy is
  least-recently-used; capacity comes from ``TTS_MODEL_CACHE_SIZE`` (default 1).
* **FR-CA-02** — eviction invokes the per-entry ``unloader`` callback when
  registered. None of the in-tree providers currently exposes an ``unload()``
  method, so the default policy is "drop reference and let CPython's refcount
  reclaim the GPU/host memory once the in-flight request returns". The cache
  exposes the seam so a future provider can register a real teardown without
  another refactor.
* **FR-CA-03** — validation (allow-list, file deps) runs BEFORE any mutation.
  A failing validator leaves the current cache contents untouched.
* **FR-CA-04** — ``preload()`` is just ``get_or_load`` with no held reference.

The cache is process-wide and is published on ``app.state.model_cache`` by
the lifespan (S-003 wiring). Routers do not consume it directly; the TTS
provider strategies do, via :class:`CachedModelProvider.attach_model_cache`.

The internal data structures use ``threading.Lock`` rather than
``asyncio.Lock`` so that the cache stays usable from the sync provider
backends today and from the ``anyio.to_thread.run_sync`` wrappers S-007
will introduce — without forcing the call site into async-only code.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeAlias

logger = logging.getLogger(__name__)

CacheKey: TypeAlias = tuple[str, str]
Loader: TypeAlias = Callable[[], Any]
Validator: TypeAlias = Callable[[], None]
Unloader: TypeAlias = Callable[[Any], None]


@dataclass(slots=True)
class _CacheEntry:
    """One cached model plus its optional teardown callback."""

    model: Any
    unloader: Unloader | None = None


class LRUModelCache:
    """Bounded LRU cache for ``(provider, model_id) -> model`` entries."""

    def __init__(self, max_size: int) -> None:
        """Create a cache bounded to ``max_size`` (must be >= 1)."""
        if max_size < 1:
            raise ValueError("LRUModelCache max_size must be >= 1")
        self._max_size = max_size
        self._entries: OrderedDict[CacheKey, _CacheEntry] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def max_size(self) -> int:
        """Maximum number of (provider, model_id) entries held simultaneously."""
        return self._max_size

    def loaded_keys(self) -> list[CacheKey]:
        """Snapshot of cached keys in most-recently-used-first order."""
        with self._lock:
            return list(reversed(self._entries.keys()))

    def __contains__(self, key: object) -> bool:
        if not (isinstance(key, tuple) and len(key) == 2):
            return False
        with self._lock:
            return key in self._entries

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def get_or_load(
        self,
        *,
        provider: str,
        model_id: str,
        loader: Loader,
        validator: Validator | None = None,
        unloader: Unloader | None = None,
    ) -> Any:
        """Return the cached model or load it via ``loader``.

        ``validator`` (if given) runs BEFORE the existing entry is touched,
        so a failing validator raises without disturbing the cache (FR-CA-03).
        On a successful load the new entry is inserted; if that pushes the
        cache past ``max_size`` the least-recently-used entry is evicted and
        its ``unloader`` (if any) is invoked OUTSIDE the cache lock.
        """
        key = (provider, model_id)
        hit = self._try_hit(key)
        if hit is not None:
            return hit

        if validator is not None:
            validator()

        loaded = loader()
        return self._install(key, loaded, unloader)

    def preload(
        self,
        *,
        provider: str,
        model_id: str,
        loader: Loader,
        validator: Validator | None = None,
        unloader: Unloader | None = None,
    ) -> Any:
        """Load and warm a model into the cache during startup."""
        return self.get_or_load(
            provider=provider,
            model_id=model_id,
            loader=loader,
            validator=validator,
            unloader=unloader,
        )

    def _try_hit(self, key: CacheKey) -> Any | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            return entry.model

    def _install(self, key: CacheKey, model: Any, unloader: Unloader | None) -> Any:
        pending_unloads: list[tuple[Unloader, Any]] = []
        with self._lock:
            existing = self._entries.get(key)
            if existing is not None:
                # Lost a race with another loader; adopt their entry.
                self._entries.move_to_end(key)
                return existing.model
            self._entries[key] = _CacheEntry(model=model, unloader=unloader)
            while len(self._entries) > self._max_size:
                _, evicted = self._entries.popitem(last=False)
                if evicted.unloader is not None:
                    pending_unloads.append((evicted.unloader, evicted.model))
        for unload_fn, evicted_model in pending_unloads:
            try:
                unload_fn(evicted_model)
            except Exception:  # noqa: BLE001
                # Unload failures are non-fatal: the entry is already removed
                # from the cache and CPython refcounting will reclaim memory.
                logger.warning("Model unload callback raised; ignoring", exc_info=True)
        return model
