"""Programmatic cross-reference tests between docs and code (S-019 T5).

Three assertions, all pure unit tests with no fixtures:

(a) Every env var name consumed by :class:`llm_tts_api.config.Settings`
    appears verbatim in ``README.md`` (UAT-CF-04).
(b) Every ``(type, code)`` pair declared in
    :mod:`llm_tts_api.errors`'s ``ERROR_CODES`` registry appears in
    ``README.md``.
(c) Every router-prefix path exposed by
    :func:`llm_tts_api.main.create_app` has a matching ``paths:`` entry
    in ``docs/openapi/openapi.yaml``.

The discovery is AST-based for env vars (parse the ``Settings`` class for
``os.environ.get(...)`` / ``os.getenv(...)`` literal-string lookups) so
the test self-updates whenever a new env var lands in ``config.py``
without requiring the test fixture to be edited. Errors come from the
``ERROR_CODES`` constant (a closed registry that documents the codes the
service emits). Router prefixes are walked on a constructed app using
the ``LLM_TTS_API_TEST_NO_LIFESPAN=1`` bypass so no singletons spin up.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest
import yaml

from llm_tts_api.errors import ERROR_CODES

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
README_PATH: Path = REPO_ROOT / "README.md"
CONFIG_PATH: Path = REPO_ROOT / "src" / "llm_tts_api" / "config.py"
OPENAPI_PATH: Path = REPO_ROOT / "docs" / "openapi" / "openapi.yaml"


def _collect_env_var_names(source: str) -> set[str]:
    """Return every literal env-var name read by Settings in ``source``.

    Picks up calls of the form ``os.environ.get("NAME"...)`` /
    ``os.environ["NAME"]`` / ``os.getenv("NAME"...)`` where ``NAME`` is a
    constant string. Non-literal lookups are skipped (we cannot statically
    resolve them); the convention in ``config.py`` is to use literals so
    the inventory stays discoverable.
    """
    tree = ast.parse(source)
    names: set[str] = set()
    # Helpers in Settings that take the env-var name as their first positional
    # arg — pick those up too so the inventory covers vars loaded via the
    # ``_load_int`` / ``_load_enum`` / ``_load_optional_timeout`` /
    # ``_load_preload_models`` indirection.
    helper_names: frozenset[str] = frozenset(
        {
            "_load_int",
            "_load_enum",
            "_load_optional_timeout",
            "_load_preload_models",
        }
    )
    # Keyword args on the per-provider loader that carry env-var names
    # (``default_env=...`` / ``allowed_env=...``).
    helper_kwargs: frozenset[str] = frozenset({"default_env", "allowed_env"})
    for node in ast.walk(tree):
        # ``self._load_int("X", ...)`` / ``self._load_enum("X", ...)`` / ...
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in helper_names and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    names.add(first.value)
            for kw in node.keywords:
                if (
                    kw.arg in helper_kwargs
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ):
                    names.add(kw.value.value)
        # ``os.environ.get("X")`` / ``os.getenv("X")``
        if isinstance(node, ast.Call):
            func = node.func
            target: str | None = None
            if isinstance(func, ast.Attribute) and func.attr == "get":
                base = func.value
                if (
                    isinstance(base, ast.Attribute)
                    and base.attr == "environ"
                    and isinstance(base.value, ast.Name)
                    and base.value.id == "os"
                ):
                    target = "environ.get"
                elif isinstance(base, ast.Name) and base.id == "os":
                    pass
            elif isinstance(func, ast.Attribute) and func.attr == "getenv":
                base = func.value
                if isinstance(base, ast.Name) and base.id == "os":
                    target = "getenv"
            if target is not None and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    names.add(first.value)
        # ``os.environ["X"]``
        elif isinstance(node, ast.Subscript):
            value = node.value
            if (
                isinstance(value, ast.Attribute)
                and value.attr == "environ"
                and isinstance(value.value, ast.Name)
                and value.value.id == "os"
            ):
                idx = node.slice
                if isinstance(idx, ast.Constant) and isinstance(idx.value, str):
                    names.add(idx.value)
    return names


@pytest.fixture(scope="module")
def readme_text() -> str:
    return README_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def env_var_names() -> set[str]:
    return _collect_env_var_names(CONFIG_PATH.read_text(encoding="utf-8"))


def test_every_settings_env_var_appears_in_readme(
    readme_text: str, env_var_names: set[str]
) -> None:
    """UAT-CF-04: README documents every env var Settings reads."""
    # Sanity: the collector must find at least a representative slice. If
    # this collapses to zero, the AST walker drifted and the test is
    # silently passing.
    assert "APP_LOG_LEVEL" in env_var_names
    assert "TTS_VOICE_STORE_DIR" in env_var_names
    missing = sorted(name for name in env_var_names if name not in readme_text)
    assert not missing, f"env vars missing from README.md: {missing}"


def test_every_error_taxonomy_pair_appears_in_readme(readme_text: str) -> None:
    """Every (type, code) pair from errors.ERROR_CODES is documented in README."""
    missing: list[tuple[str, str]] = []
    for error_type, codes in ERROR_CODES.items():
        # The type itself must show up at least once.
        if error_type not in readme_text:
            missing.append((error_type, "<type-itself>"))
            continue
        for code in codes:
            if code not in readme_text:
                missing.append((error_type, code))
    assert not missing, f"error taxonomy pairs missing from README.md: {missing}"


def test_every_router_prefix_appears_in_openapi() -> None:
    """Every router-prefix in create_app is covered by OpenAPI paths."""
    # Use the test bypass so building the app doesn't trigger DI/lifespan.
    os.environ.setdefault("LLM_TTS_API_TEST_NO_LIFESPAN", "1")
    from llm_tts_api.main import create_app

    app = create_app()
    spec = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    documented: set[str] = set(spec.get("paths", {}).keys())

    # Collect every full path from registered routes (skip auto-generated
    # OpenAPI / docs endpoints by namespace).
    route_paths: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if not isinstance(path, str):
            continue
        if path.startswith(("/openapi", "/docs", "/redoc")):
            continue
        route_paths.add(path)

    # For each route, at least one documented path-prefix family must
    # cover it. We require the exact route path OR a documented path
    # that shares the same prefix family — the latter accommodates
    # path-parameterized routes (``/v1/tts/voices/{voice_id}``) which
    # are documented as their templated form.
    missing: list[str] = []
    for path in sorted(route_paths):
        if path in documented:
            continue
        # Fallback: check that a documented path shares the route's
        # router-prefix (everything up to the last segment).
        prefix = path.rsplit("/", 1)[0] or "/"
        if any(doc.startswith(prefix) for doc in documented):
            continue
        missing.append(path)
    assert not missing, f"routes missing from openapi.yaml paths: {missing}"
