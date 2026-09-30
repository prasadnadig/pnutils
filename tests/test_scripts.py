"""Tests for subprocess execution and secure temporary-file helpers."""

import sys

import pytest

from pnutils.exceptions import CommandError
from pnutils.scripts import run_command
from pnutils.scripts import secure as secure_helpers


def test_run_success():
    result = run_command([sys.executable, "-c", "print('hi')"])
    assert result.ok
    assert result.stdout.strip() == "hi"


def test_run_failure_raises():
    with pytest.raises(CommandError):
        run_command([sys.executable, "-c", "raise SystemExit(3)"])


def test_run_failure_no_check():
    result = run_command([sys.executable, "-c", "raise SystemExit(3)"], check=False)
    assert result.returncode == 3


def test_run_rejects_string():
    with pytest.raises(TypeError):
        run_command("echo hi")  # type: ignore[arg-type]


def test_secure_temp_dir_warns_when_secret_file_remains(tmp_path, monkeypatch, capsys):
    def make_temp_dir(prefix):
        directory = tmp_path / prefix
        directory.mkdir()
        return str(directory)

    monkeypatch.setattr(secure_helpers.tempfile, "mkdtemp", make_temp_dir)
    monkeypatch.setattr(secure_helpers, "remove_secret_file", lambda path: False)

    with secure_helpers.temporary_secret_directory() as directory:
        secret_file = secure_helpers.write_secret_file(directory, "key", "secret")

    warning = capsys.readouterr().err
    assert str(secret_file) in warning
    assert "delete it and rotate the credential" in warning
    assert secret_file.exists()
    secret_file.unlink()
    directory.rmdir()


def test_write_secret_file_resets_existing_permissions(tmp_path):
    secret_file = tmp_path / "key"
    secret_file.write_text("old")
    secret_file.chmod(0o644)

    written = secure_helpers.write_secret_file(tmp_path, "key", "new")

    assert written.read_text() == "new"
    assert written.stat().st_mode & 0o777 == 0o600
