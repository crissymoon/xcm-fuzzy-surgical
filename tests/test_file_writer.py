"""Tests for writes that stay visible to editors and pagers."""

import hashlib
import os
import stat

import pytest

from fuzzy_surgical.file_writer import FileWriteError, write_text


def test_write_keeps_existing_inode(tmp_path):
    path = tmp_path / "app.py"
    path.write_text("alpha\n", encoding="utf-8")
    before = path.stat().st_ino

    write_text(str(path), "beta\n")

    assert path.read_text(encoding="utf-8") == "beta\n"
    if os.name != "nt":
        assert path.stat().st_ino == before


def test_write_follows_symlink_target(tmp_path):
    target = tmp_path / "real.py"
    target.write_text("old\n", encoding="utf-8")
    link = tmp_path / "link.py"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available")

    write_text(str(link), "new\n")

    assert target.read_text(encoding="utf-8") == "new\n"
    assert link.is_symlink()


def test_write_preserves_permissions(tmp_path):
    path = tmp_path / "app.py"
    path.write_text("old\n", encoding="utf-8")
    path.chmod(0o640)

    write_text(str(path), "new\n")

    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_write_read_only_file_keeps_mode(tmp_path):
    path = tmp_path / "app.py"
    path.write_text("old\n", encoding="utf-8")
    path.chmod(0o444)
    try:
        write_text(str(path), "new\n")
        assert path.read_text(encoding="utf-8") == "new\n"
        assert stat.S_IMODE(path.stat().st_mode) == 0o444
    finally:
        path.chmod(0o644)


def test_write_missing_file_raises(tmp_path):
    with pytest.raises(FileWriteError):
        write_text(str(tmp_path / "missing.py"), "data\n")


def test_write_result_reports_size_and_newer_mtime(tmp_path):
    path = tmp_path / "app.py"
    path.write_text("old\n", encoding="utf-8")
    before = path.stat().st_mtime_ns

    result = write_text(str(path), "new line\n")

    assert result.path == os.path.realpath(str(path))
    assert result.bytes_written == len("new line\n")
    assert result.inode_replaced is False
    assert path.stat().st_mtime_ns > before


def test_open_handle_sees_the_new_content(tmp_path):
    path = tmp_path / "app.py"
    path.write_text("old\n", encoding="utf-8")

    with open(path, "r", encoding="utf-8") as handle:
        write_text(str(path), "new\n")
        handle.seek(0)
        assert handle.read() == "new\n"


def test_read_only_file_keeps_inode_on_posix(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX permission bits are required")
    path = tmp_path / "app.py"
    path.write_text("old\n", encoding="utf-8")
    path.chmod(0o444)
    before = path.stat().st_ino
    try:
        result = write_text(str(path), "new\n")

        assert path.stat().st_ino == before
        assert result.inode_replaced is False
        assert stat.S_IMODE(path.stat().st_mode) == 0o444
        assert path.read_text(encoding="utf-8") == "new\n"
    finally:
        path.chmod(0o644)


def test_write_result_reports_the_requested_path_and_digest(tmp_path):
    path = tmp_path / "app.py"
    path.write_text("old\n", encoding="utf-8")

    result = write_text(str(path), "new\n")

    assert result.requested_path == str(path)
    assert result.digest == hashlib.sha256(b"new\n").hexdigest()
