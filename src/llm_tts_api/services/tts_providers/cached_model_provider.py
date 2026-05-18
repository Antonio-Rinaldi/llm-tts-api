"""Per-provider model-cache mixin.

Post-S-008: when the lifespan attaches a shared :class:`LRUModelCache` via
:meth:`attach_model_cache`, ``_get_model`` and ``preload`` route through it,
so all providers share one bounded cache keyed by ``(provider, model_id)``
(FR-CA-01..04). When no cache is attached (legacy / bare-instance unit
tests) the class falls back to its original unbounded per-provider dict so
existing provider tests that construct a provider directly continue to work.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from threading import Lock
from typing import Any

from llm_tts_api.errors import invalid_request
from llm_tts_api.services.model_cache import LRUModelCache

logger = logging.getLogger(__name__)


class CachedModelProvider:
    """Shared cache/lock lifecycle for lazily-loaded provider models."""

    provider_name: str = ""

    def __init__(self) -> None:
        """Initialize legacy cache + per-model locks."""
        # Legacy unbounded cache: only used when no LRU cache is attached.
        # Test contexts that instantiate the provider directly rely on it.
        self._model_cache: dict[str, object] = {}
        self._model_locks: dict[str, Lock] = {}
        self._cache_lock = Lock()

        # S-008 shared cache + allow-list (populated by attach_model_cache).
        self._shared_cache: LRUModelCache | None = None
        self._allowed_models: frozenset[str] = frozenset()

    # -- S-008 wiring ---------------------------------------------------

    def attach_model_cache(self, cache: LRUModelCache, allowed_models: Iterable[str] = ()) -> None:
        """Bind the process-wide LRU model cache and this provider's allow-list.

        The allow-list seeds the pre-eviction validator (FR-CA-03): requests
        for models outside it are rejected BEFORE the current cache entry is
        touched. An empty allow-list disables the check (useful when a provider
        cannot enumerate its valid model ids ahead of time).
        """
        self._shared_cache = cache
        self._allowed_models = frozenset(allowed_models)

    def _validate_model(self, model_name: str) -> None:
        """Reject unknown model ids before any cache mutation (FR-CA-03)."""
        if self._allowed_models and model_name not in self._allowed_models:
            raise invalid_request(
                f"model '{model_name}' is not allowed for provider "
                f"'{self.provider_name or type(self).__name__}'",
                param="model",
                code="unknown_model",
            )

    def _unload_model(self, model: object) -> None:
        """Provider-defined teardown invoked on eviction (FR-CA-02).

        Default: call ``model.unload()`` if the loaded object exposes one; the
        in-tree providers do not, so the entry is simply dropped and reclaimed
        by CPython refcounting once the in-flight request returns its
        reference. Subclasses may override for backend-specific teardown.
        """
        unload = getattr(model, "unload", None)
        if callable(unload):
            try:
                unload()
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Provider %r unload() raised; continuing eviction",
                    self.provider_name,
                    exc_info=True,
                )

    # -- legacy lock plumbing ------------------------------------------

    def _get_model_lock(self, model_name: str) -> Lock:
        """Return a per-model lock used to serialize model generation calls."""
        with self._cache_lock:
            model_lock = self._model_locks.get(model_name)
            if model_lock is None:
                model_lock = Lock()
                self._model_locks[model_name] = model_lock
            return model_lock

    def _load_model(self, model_name: str) -> Any:
        """Load a model instance from the concrete provider implementation."""
        raise NotImplementedError

    # -- public retrieval ----------------------------------------------

    def _get_model(self, model_name: str) -> Any:
        """Return a cached model or load it.

        When :meth:`attach_model_cache` has been called, retrieval goes
        through the shared LRU (which handles validation, eviction, and
        unload callbacks). Without a shared cache, retain the original
        unbounded-dict behavior so bare-instance unit tests still pass.
        """
        if self._shared_cache is not None:
            return self._shared_cache.get_or_load(
                provider=self.provider_name,
                model_id=model_name,
                loader=lambda: self._load_model(model_name),
                validator=lambda: self._validate_model(model_name),
                unloader=self._unload_model,
            )

        with self._cache_lock:
            cached_model = self._model_cache.get(model_name)
        if cached_model is not None:
            return cached_model

        loaded_model = self._load_model(model_name)

        with self._cache_lock:
            existing_model = self._model_cache.get(model_name)
            if existing_model is not None:
                return existing_model
            self._model_cache[model_name] = loaded_model
            self._model_locks.setdefault(model_name, Lock())
            return loaded_model

    def preload(self, model_name: str) -> None:
        """Warm up the cache for one model name during startup."""
        self._get_model(model_name)
