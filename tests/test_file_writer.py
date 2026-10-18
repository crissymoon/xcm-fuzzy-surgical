"""Tests for writes that stay visible to editors and pagers."""

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
