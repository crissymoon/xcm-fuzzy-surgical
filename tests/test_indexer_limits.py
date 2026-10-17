"""Tests for indexing safety limits and ignore patterns."""

import os
import tempfile
from pathlib import Path

from fuzzy_surgical.indexer import FileIndexer, IndexLimits


def test_indexer_respects_max_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        for index in range(5):
            (root / f"file{index}.txt").write_text("line\n")

        indexer = FileIndexer(limits=IndexLimits(max_files=2))
        results = indexer.index_directory(root)
        assert len(results) == 2


def test_indexer_skips_large_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "small.txt").write_text("small\n")
        (root / "large.txt").write_text("x" * 100)

        indexer = FileIndexer(limits=IndexLimits(max_file_size=20))
        results = indexer.index_directory(root)
        assert str(root / "small.txt") in results
        assert str(root / "large.txt") not in results


def test_indexer_respects_ignore_globs():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "keep.py").write_text("keep\n")
        (root / "skip.log").write_text("skip\n")
        cache = root / "cache"
        cache.mkdir()
        (cache / "inside.py").write_text("inside\n")

        indexer = FileIndexer()
        results = indexer.index_directory(root, ignore_dirs=["cache", "*.log"])
        assert str(root / "keep.py") in results
        assert str(root / "skip.log") not in results
        assert str(cache / "inside.py") not in results


def test_indexer_does_not_follow_directory_symlinks():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        outside = root / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret\n")
        search = root / "search"
        search.mkdir()
        (search / "visible.txt").write_text("visible\n")

        link = search / "outside-link"
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            return

        indexer = FileIndexer()
        results = indexer.index_directory(search)
        assert str(search / "visible.txt") in results
        assert str(link / "secret.txt") not in results
