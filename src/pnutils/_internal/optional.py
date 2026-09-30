"""Helpers for working with optional third-party dependencies."""

from __future__ import annotations

import importlib
from types import ModuleType

from pnutils.exceptions import MissingDependencyError


def require(module: str, extra: str) -> ModuleType:
    """Import ``module`` or raise a helpful error naming the pip extra."""
    try:
        return importlib.import_module(module)
    except ImportError as exc:  # pragma: no cover - depends on install profile
        raise MissingDependencyError(module, extra) from exc
