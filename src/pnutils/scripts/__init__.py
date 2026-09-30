"""Helpers for writing command line scripts."""

from __future__ import annotations

from pnutils.scripts.logging import setup_logging
from pnutils.scripts.process import CommandResult, run_command
from pnutils.scripts.secure import (
    remove_secret_file,
    temporary_secret_directory,
    write_secret_file,
)

__all__ = [
    "CommandResult",
    "run_command",
    "remove_secret_file",
    "setup_logging",
    "temporary_secret_directory",
    "write_secret_file",
]
