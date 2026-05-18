"""S-008 LRU model cache tests (FR-CA-01..04 / UAT-CA-01..03).

Covers the cache class itself plus the provider integration seam:

- UAT-CA-01: ``m1 → m2 → m1`` with size 1 produces 3 loads and 2 unloads.
- UAT-CA-02: invalid model_id rejected at the validator BEFORE the current
  entry is touched (FR-CA-03); cache state is preserved.
- UAT-CA-03: lifespan preload populates the cache; first synthesis hits the
  cache without re-loading.
- Cache-thrash regression: alternating ``m1 ↔ m2`` keeps the load count
  monotonic and never drops both entries below ``max_size``.
- Provider seam: when no cache is attached the legacy unbounded dict path
  remains in effect (so bare-instance provider tests keep working).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from llm_tts_api.errors import OpenAIHTTPException
from llm_tts_api.services.model_cache import LRUModelCache
from llm_tts_api.services.tts_providers.cached_model_provider import CachedModelProvider


class _CountingProvider(CachedModelProvider):
    """In-test stand-in for a real provider with a counting loader."""

    provider_name = "fake_provider"

    def __init__(self) -> None:
        super().__init__()
        self.load_calls: list[str] = []
        self.unload_calls: list[str] = []

    def _load_model(self, model_name: str) -> Any:
        self.load_calls.append(model_name)
        return {"name": model_name}

    def _unload_model(self, model: object) -> None:  # noqa: D401 — test override
        assert isinstance(model, dict)
        self.unload_calls.append(str(model["name"]))


@pytest.fixture
def cache() -> Iterator[LRUModelCache]:
    yield LRUModelCache(max_size=1)


def test_max_size_must_be_positive() -> None:
    """Constructor rejects nonsensical sizes early."""
    with pytest.raises(ValueError, match="max_size must be >= 1"):
        LRUModelCache(max_size=0)


def test_uat_ca_01_swap_m1_m2_m1_produces_three_loads(cache: LRUModelCache) -> None:
    """``m1 → m2 → m1`` against a 1-slot cache loads each model once per visit."""
    provider = _CountingProvider()
    provider.attach_model_cache(cache, allowed_models={"m1", "m2"})

    provider._get_model("m1")
    provider._get_model("m2")
    provider._get_model("m1")

    assert provider.load_calls == ["m1", "m2", "m1"]
    # Each load past the first evicts the previous entry → 2 unloads.
    assert provider.unload_calls == ["m1", "m2"]
    assert cache.loaded_keys() == [("fake_provider", "m1")]


def test_repeat_hits_dont_reload(cache: LRUModelCache) -> None:
    """Same key returned to caller without invoking the loader twice."""
    provider = _CountingProvider()
    provider.attach_model_cache(cache, allowed_models={"m1"})

    first = provider._get_model("m1")
    second = provider._get_model("m1")

    assert first is second
    assert provider.load_calls == ["m1"]


def test_uat_ca_02_invalid_model_id_preserves_current_entry(cache: LRUModelCache) -> None:
    """Validator failure leaves the existing cached entry intact (FR-CA-03)."""
    provider = _CountingProvider()
    provider.attach_model_cache(cache, allowed_models={"m1"})

    provider._get_model("m1")
    assert cache.loaded_keys() == [("fake_provider", "m1")]

    with pytest.raises(OpenAIHTTPException) as exc_info:
        provider._get_model("bogus")
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "unknown_model"
    assert exc_info.value.detail["param"] == "model"

    # The current entry MUST be preserved AND no extra load/unload happened.
    assert cache.loaded_keys() == [("fake_provider", "m1")]
    assert provider.load_calls == ["m1"]
    assert provider.unload_calls == []

    # Subsequent hit on m1 must NOT reload.
    provider._get_model("m1")
    assert provider.load_calls == ["m1"]


def test_thrash_regression_alternating_keys_loads_each_time() -> None:
    """Alternating ``m1 ↔ m2`` thrashes a size-1 cache without dropping below 1."""
    cache = LRUModelCache(max_size=1)
    provider = _CountingProvider()
    provider.attach_model_cache(cache, allowed_models={"m1", "m2"})

    for _ in range(3):
        provider._get_model("m1")
        assert len(cache) == 1
        provider._get_model("m2")
        assert len(cache) == 1

    assert provider.load_calls == ["m1", "m2", "m1", "m2", "m1", "m2"]
    # Five evictions: each load past the first pushes the prior entry out.
    assert provider.unload_calls == ["m1", "m2", "m1", "m2", "m1"]


def test_cache_size_two_holds_both_keys() -> None:
    """A larger cache keeps both entries hot; no eviction until full."""
    cache = LRUModelCache(max_size=2)
    provider = _CountingProvider()
    provider.attach_model_cache(cache, allowed_models={"m1", "m2"})

    provider._get_model("m1")
    provider._get_model("m2")

    assert len(cache) == 2
    assert provider.unload_calls == []
    # MRU first.
    assert cache.loaded_keys() == [("fake_provider", "m2"), ("fake_provider", "m1")]

    # Hitting m1 promotes it back to MRU; the entry order updates accordingly.
    provider._get_model("m1")
    assert cache.loaded_keys() == [("fake_provider", "m1"), ("fake_provider", "m2")]
    assert provider.load_calls == ["m1", "m2"]
    assert provider.unload_calls == []


def test_uat_ca_03_preload_warms_cache_without_extra_load() -> None:
    """``preload`` inserts the entry and a subsequent get returns it directly."""
    cache = LRUModelCache(max_size=1)
    provider = _CountingProvider()
    provider.attach_model_cache(cache, allowed_models={"m1"})

    provider.preload("m1")
    assert provider.load_calls == ["m1"]
    assert cache.loaded_keys() == [("fake_provider", "m1")]

    # First "synthesis" reuses the preloaded entry.
    provider._get_model("m1")
    assert provider.load_calls == ["m1"]


def test_validator_blocks_load_before_mutation() -> None:
    """Validator failure on a cold cache never invokes the loader."""
    cache = LRUModelCache(max_size=1)
    provider = _CountingProvider()
    provider.attach_model_cache(cache, allowed_models={"m1"})

    with pytest.raises(OpenAIHTTPException):
        provider._get_model("not-allowed")
    assert provider.load_calls == []
    assert len(cache) == 0


def test_unload_failure_does_not_break_eviction() -> None:
    """A raising unloader is swallowed (logged) so eviction still completes."""
    cache = LRUModelCache(max_size=1)

    def loader_one() -> dict[str, str]:
        return {"id": "one"}

    def loader_two() -> dict[str, str]:
        return {"id": "two"}

    def angry_unload(_: object) -> None:
        raise RuntimeError("boom")

    cache.get_or_load(provider="fake", model_id="one", loader=loader_one, unloader=angry_unload)
    # The second insert evicts the first; the raising unloader must not propagate.
    cache.get_or_load(provider="fake", model_id="two", loader=loader_two)

    assert cache.loaded_keys() == [("fake", "two")]


def test_legacy_path_still_works_without_attached_cache() -> None:
    """Bare-instance providers (no shared cache) keep the unbounded dict fallback."""
    provider = _CountingProvider()  # no attach_model_cache call

    first = provider._get_model("m1")
    second = provider._get_model("m1")
    assert first is second
    assert provider.load_calls == ["m1"]


def test_empty_allow_list_disables_validator(cache: LRUModelCache) -> None:
    """A provider that cannot enumerate models opts out of allow-list enforcement."""
    provider = _CountingProvider()
    provider.attach_model_cache(cache, allowed_models=())

    provider._get_model("anything-goes")
    assert provider.load_calls == ["anything-goes"]


def test_lifespan_preload_paths_through_settings_pairs() -> None:
    """``build_default_dependencies`` preloads pairs from ``tts_preload_models``.

    Mirrors the UAT-CA-03 startup flow: a configured preload pair is loaded
    during lifespan setup and the resulting cache contains its key.
    """
    from llm_tts_api.config import PreloadEntry
    from llm_tts_api.dependencies import _preload_models
    from llm_tts_api.services.tts_providers.registry import TTSProviderRegistry

    cache = LRUModelCache(max_size=2)
    provider = _CountingProvider()
    provider.attach_model_cache(cache, allowed_models={"m1"})
    registry = TTSProviderRegistry(providers=[provider])  # type: ignore[list-item]

    _preload_models(registry, [PreloadEntry(provider="fake_provider", model="m1")])

    assert provider.load_calls == ["m1"]
    assert ("fake_provider", "m1") in cache
