"""Exception types shared across pnutils submodules."""

from __future__ import annotations

__all__ = [
    "CommandError",
    "ConfirmationDeclined",
    "KubeApiError",
    "KubectlError",
    "MissingDependencyError",
    "PnUtilsError",
    "ValidationError",
]


class PnUtilsError(Exception):
    """Base class for every error raised by pnutils."""


class MissingDependencyError(PnUtilsError, ImportError):
    """Raised when an optional dependency for a submodule is not installed."""

    def __init__(self, package: str, extra: str) -> None:
        super().__init__(
            f"{package!r} is required for this feature. "
            f"Install it with: pip install 'pnutils[{extra}]'"
        )
        self.package = package
        self.extra = extra


class ValidationError(PnUtilsError, ValueError):
    """Raised when a value fails validation."""


class CommandError(PnUtilsError):
    """Raised when an external command exits with a non-zero status."""


class KubectlError(CommandError):
    """Raised when ``kubectl`` is unavailable or fails."""


class KubeApiError(PnUtilsError):
    """Raised when the Kubernetes API rejects a request."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ConfirmationDeclined(PnUtilsError):
    """Raised when an operator declines a destructive operation."""
