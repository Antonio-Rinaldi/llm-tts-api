from __future__ import annotations

from threading import Lock
from typing import Any


class CachedModelProvider:
    """Shared cache/lock lifecycle for lazily-loaded provider models."""

    def __init__(self) -> None:
        """Initialize model cache and lock structures."""
        self._model_cache: dict[str, object] = {}
        self._model_locks: dict[str, Lock] = {}
        self._cache_lock = Lock()

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

    def _get_model(self, model_name: str) -> Any:
        """Return a cached model or load/store it atomically on first access."""
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

