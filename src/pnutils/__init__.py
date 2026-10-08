"""pnutils - a collection of focused utility submodules.

Submodules are imported lazily so that optional third-party dependencies
(kubernetes, ...) are only required when the relevant submodule is actually
used.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from pnutils.__about__ import __version__

_SUBMODULES = (
    "k8s",
    "linode",
    "scripts",
    "ssl_certs",
)

__all__ = [*_SUBMODULES, "__version__"]

if TYPE_CHECKING:
    from pnutils import k8s as k8s
    from pnutils import linode as linode
    from pnutils import scripts as scripts
    from pnutils import ssl_certs as ssl_certs


def __getattr__(name: str):
    if name in _SUBMODULES:
        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
