"""Tests for the safe file and folder listing used by the browser."""

from __future__ import annotations

from pathlib import Path

import pytest

from fuzzy_surgical.browser_navigator import FileBrowserNavigator


def _make_tree(root: Path) -> None:
    (root / "alpha").mkdir()
    (root / "beta").mkdir()
    (root / ".git").mkdir()
    (root / "one.py").write_text("print('one')\n", encoding="utf-8")
    (root / "two.txt").write_text("two\n", encoding="utf-8")
    (root / "picture.png").write_bytes(b"\x89PNG\r\n")
    (root / ".hidden").write_text("secret\n", encoding="utf-8")


def test_lists_folders_before_files_and_skips_ignored_names(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    nav = FileBrowserNavigator(tmp_path)

    entries = nav.list_entries()
    labels = [entry.label for entry in entries]

    assert labels[0] == "up one level"
    assert "alpha" in labels and "beta" in labels
    assert ".git" not in labels
    assert "picture.png" not in labels
    assert "one.py" in labels and "two.txt" in labels
    assert labels.index("alpha") < labels.index("one.py")

    alpha_index = labels.index("alpha")
    assert entries[alpha_index].kind == "dir"
    one_index = labels.index("one.py")
    assert entries[one_index].kind == "file"
    assert entries[one_index].size == len("print('one')\n")


def test_filter_scores_directories_and_files(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    nav = FileBrowserNavigator(tmp_path)

    labels = [entry.label for entry in nav.list_entries("one")]

    assert "one.py" in labels
    assert "up one level" in labels
    assert nav.navigation_rows("one") == 0


def test_extensions_filter_files(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    nav = FileBrowserNavigator(tmp_path, extensions=["py"])

    labels = [entry.label for entry in nav.list_entries()]

    assert "one.py" in labels
    assert "two.txt" not in labels


def test_change_rejects_files_and_missing_folders(tmp_path: Path) -> None:
    _make_tree(tmp_path)
    nav = FileBrowserNavigator(tmp_path)
    before = nav.cwd

    assert nav.change(tmp_path / "one.py") is False
    assert nav.error
    assert nav.cwd == before

    assert nav.change(tmp_path / "missing") is False
    assert nav.cwd == before


def test_parent_navigation_stops_at_the_root(tmp_path: Path) -> None:
    nav = FileBrowserNavigator(tmp_path)
    assert nav.has_parent() is True
    assert nav.go_parent() is True
    assert nav.cwd == tmp_path.parent

    root = FileBrowserNavigator(Path(tmp_path.anchor))
    assert root.has_parent() is False
    assert root.go_parent() is False


def test_symlinked_directories_are_never_followed(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "inside.txt").write_text("inside\n", encoding="utf-8")
    link = tmp_path / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this platform")

    nav = FileBrowserNavigator(tmp_path)
    labels = [entry.label for entry in nav.list_entries()]

    assert "linked" not in labels


def test_unreadable_folder_is_reported(monkeypatch, tmp_path: Path) -> None:
    nav = FileBrowserNavigator(tmp_path)

    def boom(_path):
        raise PermissionError("denied")

    monkeypatch.setattr("os.scandir", boom)

    entries = nav.list_entries()
    assert [entry for entry in entries if entry.kind != "parent"] == []
    assert "cannot be read" in nav.error


def test_unreadable_start_directory_falls_back_home() -> None:
    nav = FileBrowserNavigator(Path("/definitely/not/a/real/path"))
    assert nav.cwd.is_dir()
