"""Tests for fuzzy directory discovery."""

import os
import tempfile
from pathlib import Path

import pytest

from fuzzy_surgical.directory_navigator import DirectoryNavigator


def test_list_entries_has_current_parent_and_children():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "alpha").mkdir()
        (root / "beta").mkdir()
        (root / "notes.txt").write_text("not a directory")

        navigator = DirectoryNavigator(root)
        entries = navigator.list_entries()

        kinds = [entry.kind for entry in entries]
        assert kinds[0] == "current"
        assert "parent" in kinds
        labels = [entry.label for entry in entries]
        assert "alpha" in labels
        assert "beta" in labels
        assert "notes.txt" not in labels


def test_fuzzy_filter_prefers_matching_children():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "projects").mkdir()
        (root / "pictures").mkdir()
        (root / "music").mkdir()

        navigator = DirectoryNavigator(root)
        entries = navigator.list_entries("prj")

        child_labels = [entry.label for entry in entries if entry.kind == "dir"]
        assert child_labels == ["projects"]


def test_change_rejects_missing_directory():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        navigator = DirectoryNavigator(root)

        assert navigator.change(root / "missing") is False
        assert navigator.cwd == root.resolve()
        assert navigator.error


def test_ignored_directories_are_not_listed():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "node_modules").mkdir()
        (root / "src").mkdir()

        navigator = DirectoryNavigator(root, ignore_names=["node_modules"])
        labels = [entry.label for entry in navigator.list_entries()]
        assert "src" in labels
        assert "node_modules" not in labels


def test_symlinked_directories_are_not_followed():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        target = root / "target"
        target.mkdir()
        link = root / "link"
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks are unavailable")

        navigator = DirectoryNavigator(root)
        labels = [entry.label for entry in navigator.list_entries()]
        assert "target" in labels
        assert "link" not in labels
