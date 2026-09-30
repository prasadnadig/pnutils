"""Handle short-lived secret files staged by local command-line workflows.

Files are overwritten and removed on normal cleanup, but overwrite-in-place is
best-effort on copy-on-write or log-structured filesystems such as APFS. An
abrupt kill, power loss, snapshots, or backups may retain data; the source
environment or shell history may retain values too. Cleanup warns with the
exact path if a file remains; delete it and rotate the credential or reissue
the certificate rather than assuming shredding succeeded.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

__all__ = ["remove_secret_file", "temporary_secret_directory", "write_secret_file"]


def remove_secret_file(path: Path) -> bool:
    """Overwrite ``path`` with random bytes and delete it.

    Overwrite is best-effort and cannot guarantee erasure on copy-on-write or
    log-structured filesystems. Returns ``True`` when the path is gone; callers
    must warn and report the path when this returns ``False``.
    """
    try:
        size = path.stat().st_size
        with path.open("r+b") as handle:
            handle.write(os.urandom(size))
            handle.flush()
            os.fsync(handle.fileno())
        path.unlink()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def write_secret_file(directory: Path, name: str, content: str) -> Path:
    """Write ``content`` to ``directory/name`` with ``0600`` permissions.

    An existing file is truncated and its permissions are reset to ``0600``.
    """
    path = directory / name
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(content)
    return path


@contextmanager
def temporary_secret_directory(prefix: str = "pnutils-") -> Iterator[Path]:
    """Yield a ``0700`` temporary directory and shred its files on exit.

    If a file cannot be removed, a warning names its path and tells the
    operator to delete it and rotate the credential or reissue the certificate.
    """
    directory = Path(tempfile.mkdtemp(prefix=prefix))
    os.chmod(directory, 0o700)
    try:
        yield directory
    finally:
        for child in sorted(directory.rglob("*"), reverse=True):
            if child.is_file():
                if not remove_secret_file(child):
                    print(
                        "WARNING: could not securely remove temporary secret file "
                        f"{child}; delete it and rotate the credential or reissue the certificate.",
                        file=sys.stderr,
                    )
            else:
                child.rmdir()
        with suppress(OSError):
            directory.rmdir()
