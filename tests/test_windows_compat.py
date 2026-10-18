"""Windows-only branches that can be exercised from any platform.

The real Windows container runs the whole suite, but these tests keep the
drive-letter and LOCALAPPDATA paths covered on Linux and macOS too.
"""

import ntpath
import pathlib
from pathlib import Path

import pytest

from fuzzy_surgical import history
from fuzzy_surgical.lsp import protocol

ROOT = Path(__file__).resolve().parent.parent


def test_path_to_uri_uses_a_windows_drive(monkeypatch):
    monkeypatch.setattr(protocol.os, "name", "nt")
    monkeypatch.setattr(protocol.os.path, "abspath", lambda _path: r"C:\work\my file.py")
    monkeypatch.setattr(protocol.os.path, "splitdrive", ntpath.splitdrive)

    assert protocol.path_to_uri("ignored") == "file:///C:/work/my%20file.py"


def test_uri_to_path_removes_the_windows_leading_slash(monkeypatch):
    monkeypatch.setattr(protocol.os, "name", "nt")

    assert protocol.uri_to_path("file:///C:/work/app.py") == "C:/work/app.py"


def test_windows_history_path_uses_localappdata(monkeypatch, tmp_path):
    monkeypatch.setattr(history.os, "name", "nt")
    # pathlib selects WindowsPath from os.name, which cannot be instantiated on
    # a POSIX host, so keep the concrete class while testing the branch.
    monkeypatch.setattr(history, "Path", pathlib.PosixPath)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("FUZZY_SURGICAL_HISTORY", raising=False)

    expected = tmp_path / "fuzzy-surgical" / "history.sqlite3"
    assert history.default_history_path() == expected


def test_installers_require_the_supported_python():
    shell = (ROOT / "install.sh").read_text(encoding="utf-8")
    powershell = (ROOT / "install.ps1").read_text(encoding="utf-8")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'requires-python = ">=3.9"' in pyproject
    assert shell.count("(3, 9)") >= 2
    assert "(3, 8)" not in shell
    assert powershell.count("(3, 9)") >= 2
    assert "Python 3.8" not in powershell


@pytest.mark.parametrize(
    "relative_path",
    [
        "docker/Dockerfile.linux",
        "docker/Dockerfile.windows",
        "docker/run-linux-tests.sh",
        "docker/run-tests.sh",
        "docker/run-tests.ps1",
        "docker/check-install-ps1.ps1",
        "docker/windows-smoke.ps1",
        "scripts/headless_smoke.py",
        "scripts/smoke_tui.py",
        ".dockerignore",
    ],
)
def test_container_harness_file_exists(relative_path):
    assert (ROOT / relative_path).is_file()


def test_windows_image_uses_server_core():
    dockerfile = (ROOT / "docker" / "Dockerfile.windows").read_text(encoding="utf-8")

    assert "mcr.microsoft.com/windows/servercore" in dockerfile
    assert "install.ps1" in dockerfile
