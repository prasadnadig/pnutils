"""Opinionated logging setup for scripts."""

from __future__ import annotations

import logging
import sys

__all__ = ["setup_logging"]

_DEFAULT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def setup_logging(
    level: int = logging.INFO,
    fmt: str = _DEFAULT_FORMAT,
    logger: logging.Logger | None = None,
) -> logging.Logger:
    """Configure a logger and add a stderr handler when it has none.

    Existing stream handlers are kept as they are. The format string applies
    only when this function creates a new handler.

    Args:
        level: Minimum severity to handle; defaults to ``logging.INFO``.
        fmt: Format string for a newly created handler.
        logger: Logger to configure; ``None`` selects the root logger.

    Returns:
        The configured logger, for example to use in subsequent setup.
    """
    target = logger or logging.getLogger()
    target.setLevel(level)
    if not any(isinstance(h, logging.StreamHandler) for h in target.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(fmt))
        target.addHandler(handler)
    return target
