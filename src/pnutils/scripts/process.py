"""Subprocess execution helpers."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pnutils.exceptions import CommandError

__all__ = ["CommandResult", "run_command"]


@dataclass(frozen=True)
class CommandResult:
    """Captured output and exit status from a completed subprocess.

    Attributes:
        args: Command and arguments passed to the child process.
        returncode: Process exit status; zero indicates success.
        stdout: Text written to standard output.
        stderr: Text written to standard error.
    """

    args: Sequence[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """Whether the command completed successfully (exit status zero)."""
        return self.returncode == 0


def run_command(
    args: Sequence[str],
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    check: bool = True,
    input_text: str | None = None,
) -> CommandResult:
    """Run a subprocess without a shell and capture its output.

    Arguments are passed directly to the executable, so shell syntax such as
    pipes and redirects is not interpreted. The child inherits the current
    environment unless ``env`` is supplied; a supplied mapping replaces it.
    Because command arguments appear in the result and failure message, never
    put passwords, tokens, or other credentials in ``args``.

    Args:
        args: Executable and its arguments, for example ``["git", "status"]``.
            Pass a sequence of strings, not a single command string.
        cwd: Working directory for the child, or ``None`` to use the current one.
        env: Complete environment for the child, or ``None`` to inherit the
            current environment.
        timeout: Maximum run time in seconds, or ``None`` for no time limit.
        check: Raise :class:`CommandError` for a nonzero exit status when true.
        input_text: Optional text to send to the child's standard input.

    Returns:
        The command arguments, exit status, and captured standard output/error.

    Raises:
        TypeError: If ``args`` is a string instead of a sequence.
        CommandError: If the command exits nonzero and ``check`` is true.
        subprocess.TimeoutExpired: If the time limit expires.
    """
    if isinstance(args, (str, bytes)):
        raise TypeError("args must be a sequence of strings, not a single string")

    completed = subprocess.run(  # noqa: S603 - shell is intentionally disabled
        list(args),
        cwd=cwd,
        env=dict(env) if env is not None else None,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    result = CommandResult(
        args=list(args),
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )
    if check and not result.ok:
        raise CommandError(
            f"command {list(args)!r} exited with {result.returncode}: {result.stderr.strip()}"
        )
    return result
